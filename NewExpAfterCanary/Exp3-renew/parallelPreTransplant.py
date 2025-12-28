import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from common import parse_package_summary, classify_compilation_error, LOG_DIR_BATCH, clean_llm_code

# Add threading for parallel execution
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Thread-safe locks
results_lock = threading.Lock()
print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    """Thread-safe print function."""
    with print_lock:
        print(*args, **kwargs)

# === CONFIG ===
CSV_PATH = "/Volumes/RachnaPSSD/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = "/Volumes/RachnaPSSD/package_structure_summary.txt"
TRANSPLANT_OUTPUT = Path("/Volumes/RachnaPSSD/Exp3BatchResults/pre/transplant_results_final_pre.json")
CSV_SUMMARY_OUTPUT = Path("/Volumes/RachnaPSSD/Exp3BatchResults/pre/transplant_results_final_pre_summary.csv")
ABC_ROOT = Path("/Volumes/RachnaPSSD/GeneratedOutputClientsExp3/GPT4o")
MODEL_NAME = ABC_ROOT.name  # e.g., "GPT4o"

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

success_count = 0
failure_count = 0
failure_categories = Counter()
csv_rows = []

# Per-bump stats
per_bump_success = Counter()
per_bump_failure = Counter()
per_bump_instances = defaultdict(dict)  # bump_id -> {custom_id: {"result": "pass"/"fail"}}

# Carry forward info
carry_forward_instances = set()
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})

# Test count tracker
test_counts = defaultdict(lambda: {"generated": 0, "executed": 0})


def _extract_error_fields(err_info):
    if err_info is None:
        return ("unknown", "")
    if isinstance(err_info, dict):
        category = str(err_info.get("category", "unknown"))
        reason = str(err_info.get("reason", err_info.get("message", "")))
        if not reason:
            try:
                reason = json.dumps(err_info, ensure_ascii=False)
            except Exception:
                reason = str(err_info)
        return (category, reason)
    return ("unknown", str(err_info))


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
    """
    BBC10U1Test_prompt.txt  -> (BBC10U1Test.java, BBC10U1Test)
    anyname.txt             -> (anyname.java, anyname)
    """
    base = txt_name
    if base.endswith("_prompt.txt"):
        base = base[:-len("_prompt.txt")]
    elif base.endswith(".txt"):
        base = base[:-len(".txt")]
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
    """
    - Replace existing 'package ...;' with 'package <package_decl>;' OR inject it at the top if missing.
    - Rename the first 'public class <X>' to 'public class <class_name>'.
    """
    code = code_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    if re.search(r"^\s*package\s+[\w\.]+;\s*$", code, flags=re.MULTILINE):
        code = re.sub(
            r"^\s*package\s+[\w\.]+;\s*$",
            f"package {package_decl};",
            code,
            count=1,
            flags=re.MULTILINE
        )
    else:
        code = f"package {package_decl};\n\n{code}"

    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)", r"\1" + class_name, code, count=1)

    return code


def _count_test_methods(java_path: Path) -> int:
    """Count @Test annotations in a given Java file."""
    if not java_path.exists():
        return 0
    text = java_path.read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r'@Test\b', text))


def prepare_staging_tests(custom_id: str, package_decl: str) -> tuple[str, list[str]]:
    """
    Prepare all candidate tests into a staging dir.
    Returns (staging_root, [java_files]).
    """
    src_dir = ABC_ROOT / custom_id
    staging_root = Path(f"/tmp/llm_exec/{custom_id}/staging")
    shutil.rmtree(staging_root, ignore_errors=True)

    pkg_path = Path(*package_decl.split(".")) if package_decl else Path(".")
    dest_dir = staging_root / "LLMTest" / pkg_path
    dest_dir.mkdir(parents=True, exist_ok=True)

    java_files = []
    for p in src_dir.rglob("*.txt"):
        try:
            java_filename, class_base = _to_java_filename(p.name)
            raw = p.read_text(encoding="utf-8", errors="ignore")
            java_only = _extract_llm_java_block(raw)
            if not java_only:
                continue
            cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
            if not cleaned:
                continue
            final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)
            out_file = dest_dir / java_filename
            out_file.write_text(final_code, encoding="utf-8")
            java_files.append(java_filename)

            # Count generated tests (thread-safe)
            with results_lock:
                test_counts[custom_id]["generated"] += _count_test_methods(out_file)

        except Exception as e:
            safe_print(f"[WARN] Failed to convert {p}: {e}")
            continue

    return str(staging_root), java_files


def run_test_in_isolation(image_tag: str, custom_id: str, test_root: str, staging_root: str, 
                          java_file: str, package_decl: str, quiet: bool = False):
    """
    Run a single test in isolation by mounting only that test file.
    quiet: If True, suppress console output (logs still written to file)
    Returns: (success, err_info, log_path, failure_type)
    """
    def log(msg):
        if not quiet:
            safe_print(msg)
    
    scratch_dir = Path(f"/tmp/llm_exec/{custom_id}/scratch/{java_file}")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_pkg_dir = scratch_dir / "LLMTest"
    scratch_pkg_dir.mkdir(parents=True, exist_ok=True)

    # copy only this java file from staging
    found = None
    for root, _, files in os.walk(staging_root):
        if java_file in files:
            src_path = Path(root) / java_file
            rel_path = Path(root).relative_to(Path(staging_root) / "LLMTest")
            dest_path = scratch_pkg_dir / rel_path
            dest_path.mkdir(parents=True, exist_ok=True)
            shutil.copy(src_path, dest_path / java_file)
            found = src_path
            break
    if not found:
        log(f"[WARN] Could not find staged file {java_file} for {custom_id}")
        return False, None, "", "no_source"

    log_path = LOG_DIR_BATCH / f"{custom_id}_{java_file}_Exp3.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    container_mount = f"{test_root}/LLMTest"
    
    # Use the same approach as breaking script: compile with javac, run with surefire
    pkg_path = Path(*package_decl.split("."))
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
    
    # Compile and run approach from breaking script
    maven_cmd = (
        f"cd {project_root} && "
        f"javac -cp \"target/classes:target/test-classes:$(mvn dependency:build-classpath -q -DincludeScope=test -Dmdep.outputFile=/dev/stdout 2>/dev/null)\" "
        f"-d target/test-classes "
        f"src/test/java/{pkg_path.as_posix()}/{java_file} 2>&1 && "
        f"mvn surefire:test -Dtest={fqn} -DfailIfNoTests=false"
    )
    
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{scratch_pkg_dir}:{test_root}:ro",
        image_tag,
        "sh", "-c", maven_cmd
    ]

    log_lines = [f"[INFO] Running isolated test {java_file} for {custom_id} using {image_tag}"]
    log_lines.append(f"FQN: {fqn}")
    log_lines.append(f"Command: {maven_cmd}")
    
    failure_type = None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        stdout = proc.stdout
        stderr = proc.stderr
        combined = stdout + "\n" + stderr
        
        log_lines.append("=== STDOUT ===")
        log_lines.append(stdout)
        log_lines.append("=== STDERR ===")
        log_lines.append(stderr)
        
        # Check for compilation failure first
        has_compilation_error = (
            "COMPILATION ERROR" in combined or 
            "error:" in combined.lower() and "javac" in combined or
            "cannot find symbol" in combined or
            "package" in combined and "does not exist" in combined
        )
        
        # Parse results similar to breaking script
        success = False
        if f"Running {fqn}" in combined or f"Running {test_class}" in combined:
            # Test executed - check results
            pattern = rf"Running.*?{test_class}.*?Tests run:\s*(\d+).*?Failures:\s*(\d+).*?Errors:\s*(\d+)"
            match = re.search(pattern, combined, flags=re.DOTALL | re.IGNORECASE)
            
            if match:
                tests_run = int(match.group(1))
                failures = int(match.group(2))
                errors = int(match.group(3))
                
                log_lines.append(f"[RESULTS] Tests: {tests_run}, Failures: {failures}, Errors: {errors}")
                
                if tests_run > 0:
                    success = (failures == 0 and errors == 0)
                    if not success:
                        failure_type = "test_failure"
                        log_lines.append("[FAILURE TYPE] Test failure")
                else:
                    log_lines.append(f"[?] WARNING - 0 tests ran")
                    failure_type = "no_tests_ran"
        else:
            # Test did not execute - determine why
            if has_compilation_error:
                failure_type = "compilation_failure"
                log_lines.append("[FAILURE TYPE] Compilation failure")
            else:
                # Fallback to BUILD SUCCESS check
                success = proc.returncode == 0 and "BUILD SUCCESS" in stdout
                if not success:
                    failure_type = "execution_failure"
                    log_lines.append("[FAILURE TYPE] Execution failure (test did not run)")
            
    except subprocess.TimeoutExpired:
        log_lines.append("[ERROR] Timeout (600s)")
        failure_type = "timeout"
        success = False
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        failure_type = "exception"
        success = False

    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    err_info = classify_compilation_error(log_text) if not success else None
    return success, err_info, str(log_path), failure_type


def process_single_test(args):
    """Wrapper function to process a single test (runs in parallel)."""
    (image_tag, custom_id, test_root, staging_root, java_file, package_decl) = args
    
    success, err_info, log_path, failure_type = run_test_in_isolation(
        image_tag, custom_id, test_root, staging_root, java_file, package_decl, quiet=True
    )
    
    return (custom_id, java_file, success, err_info, log_path, failure_type)


def main():
    global success_count, failure_count, results, carry_forward_instances, carry_forward_tests, test_counts

    # === BATCH CONFIG ===
    START_ID = 1
    END_ID = 190
    MAX_WORKERS = 4  # Number of parallel Docker containers - adjust based on your system

    safe_print(f"\n{'='*80}")
    safe_print(f"PRE Stage Transplant (PARALLEL EXECUTION)")
    safe_print(f"ID Range: {START_ID} to {END_ID}")
    safe_print(f"Max Parallel Workers: {MAX_WORKERS}")
    safe_print(f"{'='*80}\n")

    # Load existing JSON if it exists
    if TRANSPLANT_OUTPUT.exists():
        try:
            existing = json.loads(TRANSPLANT_OUTPUT.read_text(encoding="utf-8"))
            results = existing.get("results", {})
            carry_forward_instances = set(existing.get("carry_forward_instances", []))
            carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []},
                                              existing.get("carry_forward_tests", {}))
            test_counts = defaultdict(lambda: {"generated": 0, "executed": 0},
                                      existing.get("test_counts", {}))
            safe_print(f"[INFO] Loaded existing results with {len(results)} custom_ids\n")
        except Exception as e:
            safe_print(f"[WARN] Failed to load existing JSON: {e}\n")

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()

            # Extract numeric suffix from IDs like "BBC11"
            match = re.search(r"(\d+)$", custom_id)
            if not match:
                continue
            cid_num = int(match.group(1))
            if cid_num < START_ID or cid_num > END_ID:
                continue

            commit = row["breakingCommit"].strip()
            bump_id = row.get("bump_id", commit)
            if not commit:
                continue

            if not _abc_has_any_file(custom_id):
                safe_print(f"[SKIP] No files under {ABC_ROOT}/{custom_id}; skipping.")
                continue

            test_root, real_package = pkg_info.get((custom_id, "pre"), (None, None))
            if not test_root or not real_package:
                safe_print(f"[SKIP] No test_root/package for {custom_id}")
                continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
            staging_root, java_files = prepare_staging_tests(custom_id, real_package)
            safe_print(f"\n{custom_id} | {commit[:8]}")
            safe_print(f"[INFO] Prepared {len(java_files)} candidate test file(s)")
            
            if not java_files:
                continue

            per_test_status = {"passed": [], "failed": []}
            good_tests_dir = Path(f"/tmp/llm_exec/{custom_id}/{MODEL_NAME}/LLMTest")
            good_tests_dir.mkdir(parents=True, exist_ok=True)
            
            # Track failure types for this instance
            compilation_failures = []
            test_failures = []
            other_failures = []

            # Prepare all test tasks for this instance
            test_tasks = []
            for java_file in java_files:
                task_args = (image_tag, custom_id, test_root, staging_root, java_file, real_package)
                test_tasks.append(task_args)
            
            safe_print(f"[INFO] Testing {len(test_tasks)} file(s) in parallel...")
            
            # Execute tests in parallel
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_test = {executor.submit(process_single_test, task): task for task in test_tasks}
                
                for future in as_completed(future_to_test):
                    task = future_to_test[future]
                    java_file = task[4]  # Extract java_file from task args
                    
                    try:
                        cid, jf, success, err_info, log_path, failure_type = future.result()
                        
                        # Find staged file for test counting
                        staged_file = None
                        for root, _, files in os.walk(staging_root):
                            if java_file in files:
                                staged_file = Path(root) / java_file
                                break
                        
                        # Thread-safe updates
                        with results_lock:
                            if success:
                                safe_print(f"  → {java_file}... ✓")
                                success_count += 1
                                per_test_status["passed"].append(java_file)
                                carry_forward_tests[custom_id]["passed"].append(java_file)
                                
                                if staged_file:
                                    test_counts[custom_id]["executed"] += _count_test_methods(staged_file)
                                    rel_path = staged_file.relative_to(Path(staging_root) / "LLMTest")
                                    dest_path = good_tests_dir / rel_path
                                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.copy(staged_file, dest_path / java_file)
                            else:
                                cat = err_info.get("category", "?") if err_info else "?"
                                safe_print(f"  → {java_file}... ✗ ({failure_type or cat})")
                                failure_count += 1
                                
                                # Categorize failure
                                failure_entry = {
                                    "file": java_file,
                                    "failure_type": failure_type,
                                    "error_category": cat,
                                    "error_info": err_info
                                }
                                
                                if failure_type == "compilation_failure":
                                    compilation_failures.append(failure_entry)
                                elif failure_type == "test_failure":
                                    test_failures.append(failure_entry)
                                else:
                                    other_failures.append(failure_entry)
                                
                                per_test_status["failed"].append(java_file)
                                carry_forward_tests[custom_id]["failed"].append(java_file)
                                
                    except Exception as exc:
                        with results_lock:
                            safe_print(f"  → {java_file}... ✗ (Exception: {exc})")
                            failure_count += 1
                            
                            failure_entry = {
                                "file": java_file,
                                "failure_type": "exception",
                                "error_category": "exception",
                                "error_info": {"message": str(exc)}
                            }
                            other_failures.append(failure_entry)
                            
                            per_test_status["failed"].append(java_file)
                            carry_forward_tests[custom_id]["failed"].append(java_file)

            if per_test_status["passed"]:
                carry_forward_instances.add(custom_id)

            # Update results incrementally
            results[custom_id] = {
                "tests": per_test_status,
                "test_counts": test_counts[custom_id],
                "failure_breakdown": {
                    "compilation_failures": compilation_failures,
                    "test_failures": test_failures,
                    "other_failures": other_failures
                },
                "summary": {
                    "total_passed": len(per_test_status["passed"]),
                    "total_failed": len(per_test_status["failed"]),
                    "compilation_failure_count": len(compilation_failures),
                    "test_failure_count": len(test_failures),
                    "other_failure_count": len(other_failures)
                }
            }

            # Save merged state back to JSON
            TRANSPLANT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            
            # Calculate overall statistics
            total_compilation_failures = sum(len(r.get("failure_breakdown", {}).get("compilation_failures", [])) for r in results.values())
            total_test_failures = sum(len(r.get("failure_breakdown", {}).get("test_failures", [])) for r in results.values())
            total_other_failures = sum(len(r.get("failure_breakdown", {}).get("other_failures", [])) for r in results.values())
            
            TRANSPLANT_OUTPUT.write_text(json.dumps({
                "results": results,
                "carry_forward_instances": list(carry_forward_instances),
                "carry_forward_tests": carry_forward_tests,
                "test_counts": test_counts,
                "global_summary": {
                    "total_success": success_count,
                    "total_failure": failure_count,
                    "compilation_failures": total_compilation_failures,
                    "test_failures": total_test_failures,
                    "other_failures": total_other_failures
                }
            }, indent=2), encoding="utf-8")

            safe_print(f"[INFO] Saved results for {custom_id}")

    # Print summary
    total_generated = sum(v["generated"] for v in test_counts.values())
    total_executed = sum(v["executed"] for v in test_counts.values())
    
    # Calculate failure breakdown
    total_compilation_failures = sum(len(r.get("failure_breakdown", {}).get("compilation_failures", [])) for r in results.values())
    total_test_failures = sum(len(r.get("failure_breakdown", {}).get("test_failures", [])) for r in results.values())
    total_other_failures = sum(len(r.get("failure_breakdown", {}).get("other_failures", [])) for r in results.values())
    
    safe_print(f"\n{'='*80}")
    safe_print(f"✓ COMPLETE")
    safe_print(f"Successes: {success_count}, Failures: {failure_count}")
    safe_print(f"")
    safe_print(f"Failure Breakdown:")
    safe_print(f"  - Compilation failures: {total_compilation_failures}")
    safe_print(f"  - Test failures: {total_test_failures}")
    safe_print(f"  - Other failures: {total_other_failures}")
    safe_print(f"")
    safe_print(f"Generated tests: {total_generated}, Executed tests: {total_executed}")
    safe_print(f"{'='*80}\n")
    
    for cid, counts in test_counts.items():
        safe_print(f"  {cid}: generated={counts['generated']} executed={counts['executed']}")


if __name__ == "__main__":
    main()