import os
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from collections import Counter, defaultdict
from common import (
    classify_compilation_error,
    LOG_DIR_BATCH_BRE,
    clean_llm_code,
)

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv"
PRE_RESULTS_PATH = "/Volumes/Rachna-HD/Exp7BatchResults/pre/transplant_results_final_pre.json"
BREAKING_OUTPUT = Path("/Volumes/Rachna-HD/Exp7BatchResults/breaking/transplant_results_final_breaking_multimodule_v2.json")
MULTI_MODULE_LIST = Path("/Volumes/Rachna-HD/multi_module_instances.json")
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp7Batch/GPT4o")

# Use /tmp for scratch - Docker can always access this
SCRATCH_BASE = Path("/tmp/bump_breaking_scratch")

results = {}
success_count = 0
failure_count = 0

carry_forward_instances = set()
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})
multi_module_info = {}
csv_data = {}


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


def _load_multi_module_info():
    """Load multi-module project information."""
    if not MULTI_MODULE_LIST.exists():
        print(f"[ERROR] Multi-module list not found: {MULTI_MODULE_LIST}")
        return {}
    
    try:
        data = json.loads(MULTI_MODULE_LIST.read_text(encoding="utf-8"))
        print(f"[INFO] Loaded {len(data)} multi-module projects")
        return data
    except Exception as e:
        print(f"[ERROR] Failed to load multi-module list: {e}")
        return {}


def _load_csv_data():
    """Load CSV file into a dictionary for quick lookup by custom_id."""
    import csv
    csv_dict = {}
    try:
        with open(CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                custom_id = row["custom_id"].strip()
                csv_dict[custom_id] = row
        print(f"[INFO] Loaded {len(csv_dict)} entries from CSV")
        return csv_dict
    except Exception as e:
        print(f"[ERROR] Failed to load CSV: {e}")
        return {}


def _infer_project_root(custom_id: str) -> str:
    """Infer project root from common patterns."""
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


def run_test_in_isolation(image_tag: str, custom_id: str, java_file: str,
                          last_module: str, last_module_package: str):
    """
    Run a single LLM test in the breaking Docker image.
    Transplants test to LAST module and uses maven.test.failure.ignore=true.
    """
    
    # Find source file
    txt_path = None
    src_dir = ABC_ROOT / custom_id
    for p in src_dir.rglob("*.txt"):
        fname, _ = _to_java_filename(p.name)
        if fname == java_file:
            txt_path = p
            break
    if not txt_path:
        print(f"[ERROR] Source not found: {java_file}")
        return False, None, ""

    # Setup scratch directory
    scratch_dir = SCRATCH_BASE / custom_id / java_file.replace(".java", "")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # Extract and process code
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    java_only = _extract_llm_java_block(raw)
    if not java_only:
        print(f"[ERROR] No Java code found")
        return False, {"category": "no_code", "reason": "No Java code block found"}, ""
    
    cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
    if not cleaned:
        print(f"[ERROR] Empty after cleaning")
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
    
    print(f"  [TRANSPLANT] {last_module} → {last_module_package}")

    # Setup logging
    log_path = LOG_DIR_BATCH_BRE / f"{custom_id}_{java_file}_breaking_exec_multi_v3.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # === V2 STRATEGY: Override Docker CMD with Maven command ===
    # === V2 STRATEGY: Override Docker CMD with Maven command ===
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
        f"Strategy: V2 - Maven reactor with maven.test.failure.ignore=true",
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
            log_lines.append(f"[INFO] Captured {len(our_test_lines)} lines")
        else:
            log_lines.append("=== TEST NOT FOUND IN OUTPUT ===")
            log_lines.append("[ERROR] Test did not execute or was not found")
            log_lines.append("")
            log_lines.append("=== FULL OUTPUT (for debugging) ===")
            log_lines.extend(combined.split("\n"))
        
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
                
                log_lines.append(f"{'='*80}")
                log_lines.append(f"TEST RESULTS SUMMARY")
                log_lines.append(f"{'='*80}")
                log_lines.append(f"Tests Run:  {tests_run}")
                log_lines.append(f"Failures:   {failures}")
                log_lines.append(f"Errors:     {errors}")
                log_lines.append(f"{'='*80}")
                
                if tests_run > 0:
                    success = (failures == 0 and errors == 0)
                    if success:
                        log_lines.append(f"[✓] PASS - No breaking change detected")
                    else:
                        log_lines.append(f"[✗] FAIL - Breaking change detected!")
                        
                        if failures > 0:
                            log_lines.append(f"")
                            log_lines.append(f"=== FAILURE DETAILS ===")
                            failure_section = []
                            in_failure = False
                            for line in our_test_lines:
                                if "Failed tests:" in line or "<<< FAILURE!" in line:
                                    in_failure = True
                                if in_failure:
                                    failure_section.append(line)
                                    if len(failure_section) > 50:
                                        break
                            if failure_section:
                                log_lines.extend(failure_section)
                            else:
                                log_lines.append("(No detailed failure message found)")
                        
                        if errors > 0:
                            log_lines.append(f"")
                            log_lines.append(f"=== ERROR DETAILS ===")
                            error_section = []
                            in_error = False
                            for line in our_test_lines:
                                if "Tests in error:" in line or "<<< ERROR!" in line:
                                    in_error = True
                                if in_error:
                                    error_section.append(line)
                                    if len(error_section) > 50:
                                        break
                            if error_section:
                                log_lines.extend(error_section)
                            else:
                                log_lines.append("(No detailed error message found)")
                else:
                    log_lines.append(f"[?] WARNING - 0 tests ran")
                    success = False
            else:
                log_lines.append(f"[?] Could not parse test results")
                success = False
        else:
            log_lines.append(f"[✗] TEST DID NOT EXECUTE")
            log_lines.append("")
            
            if "COMPILATION ERROR" in combined or "cannot find symbol" in combined:
                log_lines.append("[DIAGNOSIS] Compilation error")
                log_lines.append("")
                error_count = 0
                for line in combined.split("\n"):
                    if "error:" in line.lower() and ".java" in line:
                        log_lines.append(f"  {line.strip()}")
                        error_count += 1
                        if error_count >= 10:
                            break
            elif "BUILD FAILURE" in combined:
                log_lines.append("[DIAGNOSIS] Build failure")
            else:
                log_lines.append("[DIAGNOSIS] Unknown")
            
            success = False
        
        log_lines.append("")
        log_lines.append(f"{'='*80}")
            
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

def main():
    global success_count, failure_count, results, carry_forward_instances, carry_forward_tests, multi_module_info, csv_data

    print(f"\n{'='*80}")
    print(f"BREAKING STAGE - Multi-Module Projects (V2)")
    print(f"{'='*80}\n")
    
    multi_module_info = _load_multi_module_info()
    if not multi_module_info:
        return
    
    csv_data = _load_csv_data()
    if not csv_data:
        return
    
    pre_data = json.loads(Path(PRE_RESULTS_PATH).read_text(encoding="utf-8"))
    carry_forward_tests.update(pre_data.get("carry_forward_tests", {}))
    carry_forward_instances.update(pre_data.get("carry_forward_instances", []))
    print(f"[INFO] Loaded {len(carry_forward_instances)} instances\n")

    if BREAKING_OUTPUT.exists():
        try:
            existing = json.loads(BREAKING_OUTPUT.read_text(encoding="utf-8"))
            results = existing.get("results", {})
            success_count = existing.get("summary", {}).get("total_pass", 0)
            failure_count = existing.get("summary", {}).get("total_fail", 0)
            print(f"[INFO] Resuming: Pass={success_count}, Fail={failure_count}\n")
        except:
            pass

    SCRATCH_BASE.mkdir(parents=True, exist_ok=True)
    LOG_DIR_BATCH_BRE.mkdir(parents=True, exist_ok=True)
    
    processed_count = 0
    skipped_count = 0

    for custom_id in sorted(multi_module_info.keys()):
        if custom_id not in csv_data:
            skipped_count += 1
            continue
        
        commit = csv_data[custom_id].get("breakingCommit", "").strip()
        if not commit:
            skipped_count += 1
            continue

        last_module = multi_module_info[custom_id]["last_module"]
        last_module_pkg = multi_module_info[custom_id]["last_module_package"]
        
        print(f"\n{custom_id} | {last_module} | {commit[:8]}")

        if custom_id not in carry_forward_instances or not _abc_has_any_file(custom_id):
            skipped_count += 1
            continue

        passed_tests = carry_forward_tests[custom_id]["passed"]
        if not passed_tests:
            skipped_count += 1
            continue

        image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
        per_test_status = {"passed": [], "failed": []}

        for java_file in passed_tests:
            print(f"  → {java_file}...", end=" ", flush=True)
            
            success, err_info, log_path = run_test_in_isolation(
                image_tag, custom_id, java_file, last_module, last_module_pkg
            )
            
            if success:
                print(f"✓")
                success_count += 1
                per_test_status["passed"].append(java_file)
            else:
                cat = err_info.get("category", "?") if err_info else "?"
                print(f"✗ ({cat})")
                failure_count += 1
                per_test_status["failed"].append(java_file)

        results[custom_id] = {
            "tests": per_test_status,
            "last_module": last_module,
            "last_module_package": last_module_pkg
        }
        processed_count += 1

        BREAKING_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        output_data = {
            "results": results,
            "summary": {
                "total_pass": success_count,
                "total_fail": failure_count,
                "processed": processed_count,
                "skipped": skipped_count
            }
        }
        BREAKING_OUTPUT.write_text(json.dumps(output_data, indent=2), encoding="utf-8")

    print(f"\n{'='*80}")
    print(f"✓ COMPLETE")
    print(f"Processed: {processed_count}, Pass: {success_count}, Fail: {failure_count}")
    print(f"{'='*80}\n")

    # === CLEANUP ===
    if SCRATCH_BASE.exists():
        print(f"[CLEANUP] Removing scratch directory: {SCRATCH_BASE}")
        shutil.rmtree(SCRATCH_BASE)
        print(f"[CLEANUP] ✓ Done")


if __name__ == "__main__":
    main()