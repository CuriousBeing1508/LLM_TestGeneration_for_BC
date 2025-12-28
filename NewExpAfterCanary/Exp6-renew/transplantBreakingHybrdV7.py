import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from common import (
    parse_package_summary,
    classify_compilation_error,
    LOG_DIR_BATCH_BRE,
    clean_llm_code,
)

# multithreading concept in docker execution:
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Add locks for thread-safe operations
results_lock = threading.Lock()
print_lock = threading.Lock()

# Thread-safe print function
def safe_print(*args, **kwargs):
    """Thread-safe print function."""
    with print_lock:
        print(*args, **kwargs)

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/ConfigFiles/package_structure_summary.txt"
PRE_RESULTS_PATH = "/Volumes/Rachna-HD/GPTResults/Exp6BatchResults/pre/transplant_results_pre_filteredExp6.json"
BREAKING_OUTPUT = Path("/Volumes/Rachna-HD/GPTResults/Exp6BatchResults/breaking/transplant_results_final_breaking_hybrid.json")
MULTI_MODULE_LIST = Path("/Volumes/Rachna-HD/ConfigFiles/multi_module_instances.json")
ABC_ROOT = Path("/Volumes/Rachna-HD/FilteredDataset/Exp6LLMOutput/GPT4o")
MODEL_NAME = ABC_ROOT.name

SCRATCH_BASE = Path("/tmp/bump_breaking_scratch")
REPORTS_BASE = Path("/Volumes/Rachna-HD/GPTResults/Exp7BatchResults/breaking/reports")

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

success_count = 0
failure_count = 0
failure_categories = Counter()

carry_forward_instances = set()
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})
multi_module_info = {}
csv_data = {}


def _load_multi_module_info():
    """Load multi-module project information."""
    if not MULTI_MODULE_LIST.exists():
        safe_print(f"[INFO] Multi-module list not found: {MULTI_MODULE_LIST}")
        return {}
    
    try:
        data = json.loads(MULTI_MODULE_LIST.read_text(encoding="utf-8"))
        safe_print(f"[INFO] Loaded {len(data)} multi-module projects")
        return data
    except Exception as e:
        safe_print(f"[ERROR] Failed to load multi-module list: {e}")
        return {}


def _load_csv_data():
    """Load CSV file into a dictionary for quick lookup by custom_id."""
    csv_dict = {}
    try:
        with open(CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                custom_id = row["custom_id"].strip()
                csv_dict[custom_id] = row
        safe_print(f"[INFO] Loaded {len(csv_dict)} entries from CSV")
        return csv_dict
    except Exception as e:
        safe_print(f"[ERROR] Failed to load CSV: {e}")
        return {}


def _abc_has_any_file(custom_id: str) -> bool:
    d = ABC_ROOT / custom_id
    if not d.is_dir():
        return False
    for p in d.rglob("*"):
        if p.is_file():
            return True
    return False


def _sanitize_class_name(name: str) -> str:
    cleaned = []
    for ch in name:
        cleaned.append(ch if (ch.isalnum() or ch == "_") else "_")
    if not cleaned:
        return "XEmpty"
    base = "".join(cleaned)
    if base[0].isdigit():
        base = "X" + base
    return base


def _to_java_filename(txt_name: str) -> tuple[str, str]:
    if txt_name.endswith("_prompt.txt"):
        base = txt_name[:-len("_prompt.txt")]
    elif txt_name.endswith(".txt"):
        base = txt_name[:-len(".txt")]
    else:
        base = txt_name
    base = _sanitize_class_name(base)
    return f"{base}.java", base


def _extract_llm_java_block(text: str) -> str:
    lines = text.splitlines()
    in_block = False
    buf = []
    for line in lines:
        s = line.strip()
        if not in_block:
            if s.lower() == "```java":
                in_block = True
        else:
            if s == "```":
                break
            buf.append(line)
    return "\n".join(buf).strip()


def _rewrite_package_and_class(code_text: str, package_decl: str, class_name: str) -> str:
    code = code_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"^\s*package\s+[\w\.]+;\s*$", code, flags=re.MULTILINE):
        code = re.sub(
            r"^\s*package\s+[\w\.]+;\s*$",
            f"package {package_decl};",
            code,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        code = f"package {package_decl};\n\n{code}"
    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)", r"\1" + class_name, code, count=1)
    return code


def _infer_project_root(custom_id: str) -> str:
    """Infer project root from common patterns (for multi-module)."""
    common_roots = {
        "BBC19": "/IDS-Messaging-Services",
        "BBC47": "/IDS-Messaging-Services",
        "BBC52": "/IDS-Messaging-Services",
        "BBC82": "/IDS-Messaging-Services",
        "BBC89": "/IDS-Messaging-Services",
        "BBC90": "/IDS-Messaging-Services",
        "BBC111": "/IDS-Messaging-Services",
        "BBC119": "/IDS-Messaging-Services",
        "BBC124": "/IDS-Messaging-Services",
        "BBC179": "/IDS-Messaging-Services",
        "BBC182": "/IDS-Messaging-Services",
        "BBC185": "/IDS-Messaging-Services",
        "BBC24": "/jadler",
        "BBC44": "/jadler",
        "BBC99": "/jadler",
        "BBC118": "/avans",
        "BBC153": "/avans",
        "BBC155": "/avans",
    }
    
    return common_roots.get(custom_id, "/workspace")


def _determine_test_location(custom_id: str, stage: str, pkg_info: dict, quiet: bool = False) -> tuple[str, str, str]:
    """
    Determine test root and package for single-module projects.
    Returns: (test_root, llm_package, strategy)
    quiet: If True, suppress informational prints (for parallel execution)
    """
    
    def log(msg):
        if not quiet:
            safe_print(msg)
    
    # Try to get from pkg_info directly
    test_root, package = pkg_info.get((custom_id, stage), (None, None))
    
    if test_root and package:
        log(f"[INFO] Found: test_root={test_root}, package={package}")
        llm_package = f"{package}.LLMTest"
        return test_root, llm_package, "with_test_root"
    
    log(f"[WARN] Missing test_root/package for ({custom_id}, {stage})")
    
    # Try to find package from any stage for this custom_id
    if not package:
        for key, value in pkg_info.items():
            if key[0] == custom_id:
                _, potential_package = value
                if potential_package:
                    package = potential_package
                    log(f"[INFO] Found package from stage '{key[1]}': {package}")
                    break
    
    if not package:
        package = "llmtest"
        log(f"[WARN] No package found, using generic: {package}")
    
    # Collect test root samples from the same stage
    test_root_samples = [v[0] for k, v in pkg_info.items() if k[1] == stage and v[0]]
    
    if test_root_samples:
        log(f"[INFO] Analyzing {len(test_root_samples)} test root samples")
        
        # Strategy 1: Find a sample with matching package prefix
        package_prefix = package.split(".")[0] if "." in package else package
        for sample in test_root_samples:
            if package_prefix in sample or package in sample:
                log(f"[INFO] Found matching sample: {sample}")
                inferred_test_root = sample
                llm_package = f"{package}.LLMTest"
                return inferred_test_root, llm_package, "inferred_test_root"
        
        # Strategy 2: Analyze patterns and use real project roots
        project_roots = []
        for tr in test_root_samples:
            if "/src/test/java" in tr:
                project_root = tr.split("/src/test/java")[0]
                project_roots.append((project_root, "/src/test/java"))
            elif "/test/java" in tr:
                project_root = tr.split("/test/java")[0]
                project_roots.append((project_root, "/test/java"))
            elif tr.endswith("/test"):
                project_root = tr.rsplit("/test", 1)[0]
                project_roots.append((project_root, "/test"))
        
        if project_roots:
            # Find most common pattern
            pattern_counts = Counter([pattern for _, pattern in project_roots])
            most_common_pattern = pattern_counts.most_common(1)[0][0]
            
            # Use first project root with this pattern
            for proj_root, pattern in project_roots:
                if pattern == most_common_pattern:
                    inferred_test_root = proj_root + most_common_pattern
                    log(f"[INFO] Inferred from pattern: {inferred_test_root}")
                    llm_package = f"{package}.LLMTest"
                    return inferred_test_root, llm_package, "inferred_test_root"
        
        # Strategy 3: Use first sample
        log(f"[WARN] Using first sample: {test_root_samples[0]}")
        inferred_test_root = test_root_samples[0]
    else:
        # No samples - generic fallback
        log(f"[WARN] No samples, using generic fallback")
        inferred_test_root = "/workspace/src/test/java"
    
    llm_package = f"{package}.LLMTest"
    log(f"[INFO] Final: test_root={inferred_test_root}, package={llm_package}")
    
    return inferred_test_root, llm_package, "inferred_test_root"


def run_test_single_module(image_tag: str, custom_id: str, test_root: str,
                           package_decl: str, java_file: str, strategy: str, quiet: bool = False):
    """
    Run test for SINGLE-MODULE projects using Document 2's approach:
    - Compile test with javac
    - Run with surefire:test
    quiet: If True, suppress console output (logs still written to file)
    """
    
    def log(msg):
        """Only log to console if not in quiet mode."""
        if not quiet:
            safe_print(msg)
    
    # Find source file
    txt_path = None
    src_dir = ABC_ROOT / custom_id
    for p in src_dir.rglob("*.txt"):
        fname, _ = _to_java_filename(p.name)
        if fname == java_file:
            txt_path = p
            break
    if not txt_path:
        log(f"[ERROR] Source not found: {java_file}")
        return False, {"category": "no_source", "reason": "Source file not found"}, ""

    # Setup scratch directory
    scratch_dir = SCRATCH_BASE / custom_id / java_file.replace(".java", "")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # Extract and process code
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    java_only = _extract_llm_java_block(raw)
    if not java_only:
        log(f"[ERROR] No Java code found")
        return False, {"category": "no_code", "reason": "No Java code block found"}, ""
    
    cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
    if not cleaned:
        log(f"[ERROR] Empty after cleaning")
        return False, {"category": "empty_code", "reason": "Empty after cleaning"}, ""
    
    _, class_base = _to_java_filename(txt_path.name)
    final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)

    pkg_path = Path(*package_decl.split("."))
    out_file = scratch_dir / pkg_path / java_file
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(final_code, encoding="utf-8")

    log_path = LOG_DIR_BATCH_BRE / f"{custom_id}_{java_file}_breaking_single.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    test_class = java_file.replace(".java", "")
    fqn = f"{package_decl}.{test_class}"
    
    # Detect project structure
    project_root = None
    test_root_parts = test_root.strip("/").split("/")
    
    if "src" in test_root_parts:
        src_index = test_root_parts.index("src")
        if src_index >= 1:
            project_root = "/" + "/".join(test_root_parts[:src_index])
        else:
            project_root = "/"
    
    if not project_root or project_root == "/":
        project_root = "/workspace"
    
    # Single module: compile test with javac, then run
    maven_cmd = (
        f"cd {project_root} && "
        f"javac -cp \"target/classes:target/test-classes:$(mvn dependency:build-classpath -q -DincludeScope=test -Dmdep.outputFile=/dev/stdout 2>/dev/null)\" "
        f"-d target/test-classes "
        f"src/test/java/{pkg_path.as_posix()}/{java_file} 2>&1 && "
        f"mvn surefire:test -Dtest={fqn} -DfailIfNoTests=false"
    )
    
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{scratch_dir}:{test_root}:ro",
        image_tag,
        "sh", "-c", maven_cmd
    ]

    log_lines = [
        f"{'='*80}",
        f"Instance: {custom_id} | Test: {java_file}",
        f"Project Type: SINGLE-MODULE",
        f"Strategy: {strategy}",
        f"{'='*80}",
        f"Test Configuration:",
        f"  Package: {package_decl}",
        f"  FQN: {fqn}",
        f"  Test Class: {test_class}",
        f"{'='*80}",
        f"Paths:",
        f"  Test Root: {test_root}",
        f"  Project Root: {project_root}",
        f"  Container File: {test_root}/{pkg_path}/{java_file}",
        f"{'='*80}",
        f"Docker: {image_tag}",
        f"Approach: javac + surefire:test (Document 2 style)",
        f"{'='*80}",
        f"",
    ]
    
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        stdout = proc.stdout
        stderr = proc.stderr
        combined = stdout + "\n" + stderr
        
        log_lines.append("=== STDOUT ===")
        log_lines.append(stdout)
        log_lines.append("")
        log_lines.append("=== STDERR ===")
        log_lines.append(stderr)
        log_lines.append("")
        log_lines.append(f"=== EXIT CODE: {proc.returncode} ===")
        log_lines.append("")
        
        # Parse results
        success = False
        
        if f"Running {fqn}" in combined or f"Running {test_class}" in combined:
            pattern = rf"Running.*?{test_class}.*?Tests run:\s*(\d+).*?Failures:\s*(\d+).*?Errors:\s*(\d+)"
            match = re.search(pattern, combined, flags=re.DOTALL | re.IGNORECASE)
            
            if match:
                tests_run = int(match.group(1))
                failures = int(match.group(2))
                errors = int(match.group(3))
                
                log_lines.append(f"[RESULTS] Tests: {tests_run}, Failures: {failures}, Errors: {errors}")
                
                if tests_run > 0:
                    success = (failures == 0 and errors == 0)
                    if success:
                        log_lines.append(f"[✓] PASS")
                    else:
                        log_lines.append(f"[✗] FAIL - Breaking change detected")
                else:
                    log_lines.append(f"[?] WARNING - 0 tests ran")
            else:
                log_lines.append(f"[?] Could not parse results")
        else:
            log_lines.append(f"[✗] TEST DID NOT EXECUTE")
            if "COMPILATION ERROR" in combined or "error:" in combined.lower():
                log_lines.append("[DIAGNOSIS] Compilation error")
            
    except subprocess.TimeoutExpired:
        log_lines.append("[ERROR] Timeout (600s)")
        success = False
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        import traceback
        log_lines.append(traceback.format_exc())
        success = False

    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    err_info = classify_compilation_error(log_text) if not success else None
    
    return success, err_info, str(log_path)


def run_test_multi_module(image_tag: str, custom_id: str, java_file: str,
                          last_module: str, last_module_package: str, quiet: bool = False):
    """
    Run test for MULTI-MODULE projects using Document 1's approach:
    - Transplant to last module
    - Run full maven reactor with maven.test.failure.ignore=true
    quiet: If True, suppress console output (logs still written to file)
    """
    
    def log(msg):
        """Only log to console if not in quiet mode."""
        if not quiet:
            safe_print(msg)
    
    # Find source file
    txt_path = None
    src_dir = ABC_ROOT / custom_id
    for p in src_dir.rglob("*.txt"):
        fname, _ = _to_java_filename(p.name)
        if fname == java_file:
            txt_path = p
            break
    if not txt_path:
        log(f"[ERROR] Source not found: {java_file}")
        return False, {"category": "no_source", "reason": "Source file not found"}, ""

    # Setup scratch directory
    scratch_dir = SCRATCH_BASE / custom_id / java_file.replace(".java", "")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # Extract and process code
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    java_only = _extract_llm_java_block(raw)
    if not java_only:
        log(f"[ERROR] No Java code found")
        return False, {"category": "no_code", "reason": "No Java code block found"}, ""
    
    cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
    if not cleaned:
        log(f"[ERROR] Empty after cleaning")
        return False, {"category": "empty_code", "reason": "Empty after cleaning"}, ""
    
    _, class_base = _to_java_filename(txt_path.name)
    test_class = java_file.replace(".java", "")
    
    # Use last module's package for our test
    final_code = _rewrite_package_and_class(cleaned, last_module_package, class_base)
    fqn = f"{last_module_package}.{test_class}"
    
    # Infer project root
    project_root = _infer_project_root(custom_id)
    
    # Transplant to LAST module
    transplant_test_root = f"{project_root}/{last_module}/src/test/java"
    pkg_path = Path(*last_module_package.split("."))
    
    # Create package structure in scratch
    out_file = scratch_dir / pkg_path / java_file
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(final_code, encoding="utf-8")

    # Setup logging
    log_path = LOG_DIR_BATCH_BRE / f"{custom_id}_{java_file}_breaking_multi.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Multi-module strategy: full Maven reactor with failure ignore
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{scratch_dir}:{transplant_test_root}:ro",
        image_tag,
        "sh", "-c",
        f"cd {project_root} && mvn clean test -Dmaven.test.failure.ignore=true -DfailIfNoTests=false -Dtest={test_class}"
    ]

    log_lines = [
        f"{'='*80}",
        f"Instance: {custom_id} | Test: {java_file}",
        f"Project Type: MULTI-MODULE",
        f"{'='*80}",
        f"Configuration:",
        f"  Test Class: {test_class}",
        f"  FQN: {fqn}",
        f"  Last Module: {last_module}",
        f"  Last Module Package: {last_module_package}",
        f"{'='*80}",
        f"Paths:",
        f"  Project Root: {project_root}",
        f"  Transplant Target: {transplant_test_root}",
        f"  Local Scratch: {scratch_dir}",
        f"  Test File: {transplant_test_root}/{pkg_path}/{java_file}",
        f"{'='*80}",
        f"Docker: {image_tag}",
        f"Approach: Maven reactor with failure ignore (Document 1 style)",
        f"Command: mvn clean test -Dmaven.test.failure.ignore=true -DfailIfNoTests=false -Dtest={test_class}",
        f"{'='*80}",
        f"",
    ]
    
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        stdout = proc.stdout
        stderr = proc.stderr
        combined = stdout + "\n" + stderr
        
        # Extract test output
        our_test_lines = []
        in_our_test = False
        found_results_line = False
        lines_after_results = 0
        
        for line in combined.split("\n"):
            if f"Running {fqn}" in line or f"Running {test_class}" in line:
                in_our_test = True
                our_test_lines = [line]
                found_results_line = False
                lines_after_results = 0
            elif in_our_test:
                our_test_lines.append(line)
                
                if line.strip().startswith("Results :") or re.search(r"^Tests run:\s*\d+,\s*Failures:", line.strip()):
                    found_results_line = True
                    lines_after_results = 0
                
                if found_results_line:
                    lines_after_results += 1
                    if lines_after_results > 10:
                        break
                
                if len(our_test_lines) > 200:
                    break
        
        if our_test_lines:
            log_lines.append("=== TEST EXECUTION OUTPUT ===")
            log_lines.extend(our_test_lines)
            log_lines.append("")
        else:
            log_lines.append("=== TEST NOT FOUND IN OUTPUT ===")
            log_lines.append("[ERROR] Test did not execute")
        
        log_lines.append("")
        log_lines.append(f"=== EXIT CODE: {proc.returncode} ===")
        log_lines.append("")
        
        # Parse results
        success = False
        
        if f"Running {fqn}" in combined or f"Running {test_class}" in combined:
            pattern = rf"Running.*?{test_class}.*?Tests run:\s*(\d+).*?Failures:\s*(\d+).*?Errors:\s*(\d+)"
            match = re.search(pattern, combined, flags=re.DOTALL | re.IGNORECASE)
            
            if match:
                tests_run = int(match.group(1))
                failures = int(match.group(2))
                errors = int(match.group(3))
                
                log_lines.append(f"[RESULTS] Tests: {tests_run}, Failures: {failures}, Errors: {errors}")
                
                if tests_run > 0:
                    success = (failures == 0 and errors == 0)
                    if success:
                        log_lines.append(f"[✓] PASS - No breaking change")
                    else:
                        log_lines.append(f"[✗] FAIL - Breaking change detected")
                else:
                    log_lines.append(f"[?] WARNING - 0 tests ran")
            else:
                log_lines.append(f"[?] Could not parse results")
        else:
            log_lines.append(f"[✗] TEST DID NOT EXECUTE")
            if "COMPILATION ERROR" in combined or "cannot find symbol" in combined:
                log_lines.append("[DIAGNOSIS] Compilation error")
            
    except subprocess.TimeoutExpired:
        log_lines.append("[ERROR] Timeout (600s)")
        success = False
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        import traceback
        log_lines.append(traceback.format_exc())
        success = False

    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    err_info = classify_compilation_error(log_text) if not success else None
    
    return success, err_info, str(log_path)


def process_single_test(args):
    """Wrapper function to process a single test (runs in parallel)."""
    (image_tag, custom_id, test_root, package_decl, java_file, strategy, 
     is_multi_module, last_module, last_module_pkg) = args
    
    # Run with quiet=True to suppress console output from worker functions
    if is_multi_module:
        success, err_info, log_path = run_test_multi_module(
            image_tag, custom_id, java_file, last_module, last_module_pkg, quiet=True
        )
    else:
        success, err_info, log_path = run_test_single_module(
            image_tag, custom_id, test_root, package_decl, java_file, strategy, quiet=True
        )
    
    return (custom_id, java_file, success, err_info, is_multi_module)


def main():
    global success_count, failure_count, results, carry_forward_instances, carry_forward_tests, multi_module_info, csv_data

    START_ID = 1
    END_ID = 190
    MAX_WORKERS = 4  # Run 4 tests in parallel - adjust based on your system

    safe_print(f"\n{'='*80}")
    safe_print(f"HYBRID Breaking Stage (PARALLEL EXECUTION)")
    safe_print(f"Single-module: javac + surefire (Doc 2)")
    safe_print(f"Multi-module: Maven reactor (Doc 1)")
    safe_print(f"ID Range: {START_ID} to {END_ID}")
    safe_print(f"Max Parallel Workers: {MAX_WORKERS}")
    safe_print(f"{'='*80}\n")
    
    # Load configurations
    multi_module_info = _load_multi_module_info()
    csv_data = _load_csv_data()
    if not csv_data:
        return
    
    # Load pre results
    pre_data = json.loads(Path(PRE_RESULTS_PATH).read_text(encoding="utf-8"))
    carry_forward_tests.update(pre_data.get("carry_forward_tests", {}))
    carry_forward_instances.update(pre_data.get("carry_forward_instances", []))
    safe_print(f"[INFO] Loaded {len(carry_forward_instances)} instances with passing tests\n")

    # Resume if exists
    if BREAKING_OUTPUT.exists():
        try:
            existing = json.loads(BREAKING_OUTPUT.read_text(encoding="utf-8"))
            results = existing.get("results", {})
            success_count = existing.get("summary", {}).get("total_pass", 0)
            failure_count = existing.get("summary", {}).get("total_fail", 0)
            safe_print(f"[INFO] Resuming: Pass={success_count}, Fail={failure_count}\n")
        except:
            pass

    # Create directories
    SCRATCH_BASE.mkdir(parents=True, exist_ok=True)
    LOG_DIR_BATCH_BRE.mkdir(parents=True, exist_ok=True)
    
    processed_count = 0
    skipped_count = 0
    single_module_count = 0
    multi_module_count = 0

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            
            match = re.search(r"(\d+)$", custom_id)
            if not match:
                continue
            cid_num = int(match.group(1))
            if cid_num < START_ID or cid_num > END_ID:
                continue

            commit = row["breakingCommit"].strip()
            if not commit:
                skipped_count += 1
                continue

            if custom_id not in carry_forward_instances or not _abc_has_any_file(custom_id):
                skipped_count += 1
                continue

            passed_tests = carry_forward_tests[custom_id]["passed"]
            if not passed_tests:
                skipped_count += 1
                continue

            # Determine if multi-module or single-module
            is_multi_module = custom_id in multi_module_info
            
            if is_multi_module:
                last_module = multi_module_info[custom_id]["last_module"]
                last_module_pkg = multi_module_info[custom_id]["last_module_package"]
                safe_print(f"\n{custom_id} [MULTI-MODULE] | {last_module} | {commit[:8]}")
                multi_module_count += 1
            else:
                safe_print(f"\n{custom_id} [SINGLE-MODULE] | {commit[:8]}")
                test_root, package_decl, strategy = _determine_test_location(custom_id, "breaking", pkg_info, quiet=False)
                single_module_count += 1

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
            per_test_status = {"passed": [], "failed": []}

            safe_print(f"[INFO] Testing {len(passed_tests)} file(s) in parallel...")
            
            # Prepare all test tasks for this instance
            test_tasks = []
            for java_file in passed_tests:
                if is_multi_module:
                    task_args = (image_tag, custom_id, None, None, java_file, None,
                                is_multi_module, last_module, last_module_pkg)
                else:
                    task_args = (image_tag, custom_id, test_root, package_decl, java_file, strategy,
                                is_multi_module, None, None)
                test_tasks.append(task_args)
            
            # Execute tests in parallel
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_test = {executor.submit(process_single_test, task): task for task in test_tasks}
                
                for future in as_completed(future_to_test):
                    task = future_to_test[future]
                    try:
                        cid, java_file, success, err_info, is_mm = future.result()
                        
                        # Thread-safe counter updates and console output
                        with results_lock:
                            if success:
                                safe_print(f"  → {java_file}... ✓")
                                success_count += 1
                                per_test_status["passed"].append(java_file)
                            else:
                                cat = err_info.get("category", "?") if err_info else "?"
                                safe_print(f"  → {java_file}... ✗ ({cat})")
                                failure_count += 1
                                per_test_status["failed"].append(java_file)
                    except Exception as exc:
                        java_file = task[4]  # Extract java_file from task args
                        with results_lock:
                            safe_print(f"  → {java_file}... ✗ (Exception: {exc})")
                            failure_count += 1
                            per_test_status["failed"].append(java_file)

            # Store results after processing all tests for this instance
            if is_multi_module:
                results[custom_id] = {
                    "tests": per_test_status,
                    "type": "multi-module",
                    "last_module": last_module,
                    "last_module_package": last_module_pkg
                }
            else:
                results[custom_id] = {
                    "tests": per_test_status,
                    "type": "single-module",
                    "strategy": strategy,
                    "test_root": test_root,
                    "package": package_decl
                }
            
            processed_count += 1

            # Save incrementally
            BREAKING_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            output_data = {
                "results": results,
                "summary": {
                    "total_pass": success_count,
                    "total_fail": failure_count,
                    "processed": processed_count,
                    "skipped": skipped_count,
                    "single_module_count": single_module_count,
                    "multi_module_count": multi_module_count
                }
            }
            BREAKING_OUTPUT.write_text(json.dumps(output_data, indent=2), encoding="utf-8")

    safe_print(f"\n{'='*80}")
    safe_print(f"✓ COMPLETE")
    safe_print(f"Processed: {processed_count} ({single_module_count} single, {multi_module_count} multi)")
    safe_print(f"Pass: {success_count}, Fail: {failure_count}")
    safe_print(f"{'='*80}\n")

    # Cleanup
    if SCRATCH_BASE.exists():
        safe_print(f"[CLEANUP] Removing: {SCRATCH_BASE}")
        shutil.rmtree(SCRATCH_BASE)

if __name__ == "__main__":
    main()