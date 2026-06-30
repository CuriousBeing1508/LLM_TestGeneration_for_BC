#!/usr/bin/env python3
"""
Script: execute_breaking_single_module.py
Description: Breaking stage execution for SINGLE-MODULE projects only
             - Uses EXACT SAME transplant logic as PRE script
             - Parallel test execution (4 tests per instance)
             - Detects compilation errors BEFORE execution
             - Auto container cleanup, resume capability
Approach: javac + surefire:test
Timeout: 600 seconds (10 minutes) per test
"""

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

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE, SECONDARY_DRIVE

# Thread-safe locks
results_lock = threading.Lock()
print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    """Thread-safe print function."""
    with print_lock:
        print(*args, **kwargs)

# # === CONFIG GPT===
# CSV_PATH = PRIMARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
# SUMMARY_PATH = PRIMARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
# PRE_RESULTS_PATH = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/transplant_results_final_pre.json"
# BREAKING_OUTPUT = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/transplant_results_breaking_single_module.json"
# MULTI_MODULE_LIST = PRIMARY_DRIVE / "ConfigFiles/multi_module_instances.json"
# ABC_ROOT = PRIMARY_DRIVE / "FilteredDataset/Exp3LLMOutput/GPT4o"



# # === CONFIG Qwen===
# CSV_PATH = SECONDARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
# SUMMARY_PATH = SECONDARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
# PRE_RESULTS_PATH = SECONDARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/transplant_results_final_pre.json"
# BREAKING_OUTPUT = SECONDARY_DRIVE / "Qwen480Results/Exp6BatchResults/bre/transplant_results_breaking_single_module.json"
# MULTI_MODULE_LIST = SECONDARY_DRIVE / "ConfigFiles/multi_module_instances.json"
# ABC_ROOT = SECONDARY_DRIVE / "FilteredDataset/Exp6LLMOutput/Qwen3_480b_cloud"



# === CONFIG GPT-OSS===
CSV_PATH = SECONDARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = SECONDARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
PRE_RESULTS_PATH = SECONDARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/transplant_results_final_pre.json"
BREAKING_OUTPUT = SECONDARY_DRIVE / "GPTOSSResults/Exp6BatchResults/bre/transplant_results_breaking_single_module.json"
MULTI_MODULE_LIST = SECONDARY_DRIVE / "ConfigFiles/multi_module_instances.json"
ABC_ROOT = SECONDARY_DRIVE / "FilteredDataset/Exp6LLMOutput/GPT_OSS_120b"

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

success_count = 0
failure_count = 0
compilation_error_count = 0
test_failure_count = 0
transplant_issue_count = 0
build_failure_count = 0

carry_forward_instances = set()
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})
multi_module_instances = set()


def _load_multi_module_list():
    """Load list of multi-module instances to skip."""
    if not MULTI_MODULE_LIST.exists():
        return set()
    
    try:
        data = json.loads(MULTI_MODULE_LIST.read_text(encoding="utf-8"))
        multi_list = set(data.keys())
        safe_print(f"[INFO] Loaded {len(multi_list)} multi-module instances to skip")
        return multi_list
    except Exception as e:
        safe_print(f"[ERROR] Failed to load multi-module list: {e}")
        return set()


def _abc_has_any_file(custom_id: str) -> bool:
    d = ABC_ROOT / custom_id
    if not d.is_dir():
        return False
    for p in d.rglob("*"):
        if p.is_file():
            return True
    return False


def _sanitize_class_name(name: str) -> str:
    """Sanitize class name to be valid Java identifier."""
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
    """Convert .txt filename to .java filename and class name."""
    base = txt_name
    if base.endswith("_prompt.txt"):
        base = base[:-len("_prompt.txt")]
    elif base.endswith(".txt"):
        base = base[:-len(".txt")]
    base = _sanitize_class_name(base)
    return f"{base}.java", base


def _extract_llm_java_block(text: str) -> str:
    """Extract Java code from markdown code block."""
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
    Rewrite package declaration and class name to match project structure.
    EXACT SAME logic as PRE script.
    """
    code = code_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    # Replace or inject package declaration
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


def cleanup_stale_containers():
    """Remove all stale execution containers from previous runs."""
    try:
        safe_print(f"\n[CLEANUP] Checking for stale Docker containers...")
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=breaking_single_", "--format", "{{.Names}}"],
            capture_output=True, text=True
        )
        
        container_names = result.stdout.strip().split('\n')
        container_names = [name for name in container_names if name and name.startswith('breaking_single_')]
        
        if container_names:
            safe_print(f"[CLEANUP] Found {len(container_names)} stale container(s)")
            removed_count = 0
            for name in container_names:
                try:
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
                    safe_print(f"[CLEANUP]   ✓ Removed: {name}")
                    removed_count += 1
                except Exception as e:
                    safe_print(f"[CLEANUP]   ✗ Failed to remove {name}: {e}")
            safe_print(f"[CLEANUP] Successfully removed {removed_count}/{len(container_names)} container(s)")
        else:
            safe_print(f"[CLEANUP] No stale containers found ✓")
    except Exception as e:
        safe_print(f"[CLEANUP] Warning: Could not check for stale containers: {e}")


def run_single_module_test(image_tag: str, custom_id: str, test_root: str,
                            package_decl: str, java_file: str, txt_path: Path):
    """
    Execute a test file on BREAKING stage.
    EXACT SAME transplant logic as PRE script execute_compiled_test function.
    
    Args:
        image_tag: Docker image for breaking stage
        custom_id: Instance ID (e.g., BBC04)
        test_root: Test root path (e.g., /recheck/src/test/java)
        package_decl: REAL package WITHOUT LLMTest (e.g., de.retest.recheck)
        java_file: Java filename (e.g., BBC04U3Test.java)
        txt_path: Path to source .txt file
    
    Returns:
        (success, err_info, log_path, result_type)
    """
    # Create minimal temp directory for THIS test only (SAME as PRE)
    temp_dir = Path(f"/tmp/breaking_single_{custom_id}_{java_file.replace('.java', '')}")
    
    # Generate unique container name for tracking (SAME as PRE)
    container_name = f"breaking_single_{custom_id}_{java_file.replace('.java', '')}_{os.getpid()}_{threading.get_ident()}"
    
    try:
        # Clean if exists from previous run
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        
        # Kill any existing container with same name
        try:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
        except:
            pass
        
        # === STEP 1: Extract and process LLM code (SAME as PRE) ===
        raw = txt_path.read_text(encoding="utf-8", errors="ignore")
        java_only = _extract_llm_java_block(raw)
        if not java_only:
            return False, {"category": "no_code", "reason": "No Java code block found"}, "", "compilation_error"
        
        cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
        if not cleaned:
            return False, {"category": "empty_code", "reason": "Empty after cleaning"}, "", "compilation_error"
        
        # === STEP 2: Fix package and class name (SAME as PRE) ===
        _, class_base = _to_java_filename(txt_path.name)
        final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)
        
        # === STEP 3: Create directory structure (EXACT SAME as PRE) ===
        # Create LLMTest organizer folder to keep our tests separate
        llm_test_dir = temp_dir / "LLMTest"
        pkg_path = Path(*package_decl.split("."))
        java_dir = llm_test_dir / pkg_path
        java_dir.mkdir(parents=True, exist_ok=True)
        
        # === STEP 4: Write Java file (SAME as PRE) ===
        java_file_path = java_dir / java_file
        java_file_path.write_text(final_code, encoding="utf-8")
        
        # === STEP 5: Setup logging ===
        log_path = LOG_DIR_BATCH_BRE / f"{custom_id}_{java_file}_breaking_single.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # === STEP 6: Detect project root (SAME as PRE) ===
        project_root = None
        test_root_parts = test_root.strip("/").split("/")
        if "src" in test_root_parts:
            src_index = test_root_parts.index("src")
            if src_index >= 1:
                project_root = "/" + "/".join(test_root_parts[:src_index])
        if not project_root or project_root == "/":
            project_root = "/workspace"
        
        test_class = java_file.replace(".java", "")
        fqn = f"{package_decl}.{test_class}"
        
        # === STEP 7: Build compile + execute command (EXACT SAME as PRE) ===
        # Skip PMD, CheckStyle, and Enforcer to avoid false failures
        exec_cmd = (
            f"cd {project_root} && "
            f"javac -cp \"target/classes:target/test-classes:"
            f"$(mvn dependency:build-classpath -q -DincludeScope=test -Dmdep.outputFile=/dev/stdout 2>/dev/null)\" "
            f"-d target/test-classes "
            f"{test_root}/{pkg_path.as_posix()}/{java_file} 2>&1 && "
            f"mvn surefire:test -Dtest={fqn} -DfailIfNoTests=false "
            f"-Dpmd.skip=true -Dcheckstyle.skip=true -Denforcer.skip=true"
        )
        
        # === STEP 8: Run Docker (EXACT SAME as PRE) ===
        # Mount LLMTest folder to test_root (hides original tests)
        cmd = [
            "docker", "run",
            "--name", container_name,
            "--rm",
            "--platform", "linux/amd64",
            "-v", f"{llm_test_dir}:{test_root}:ro",
            image_tag,
            "sh", "-c", exec_cmd
        ]
        
        log_lines = [
            "="*80,
            f"BREAKING STAGE: EXECUTION - {java_file} for {custom_id}",
            f"Container: {container_name}",
            "="*80,
            f"FQN: {fqn}",
            f"Package: {package_decl}",
            f"Project root: {project_root}",
            f"Test root: {test_root}",
            "="*80,
            f"Command: {exec_cmd}",
            "="*80,
            ""
        ]
        
        success = False
        result_type = "test_failure"
        
        try:
            # Run with timeout (600s = 10 minutes)
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
            
            # === STEP 9: Simplified classification ===
            # STEP 1: Check for COMPILATION ERROR
            if "COMPILATION ERROR" in combined or ("error:" in combined.lower() and "javac" in combined.lower()):
                success = False
                result_type = "compilation_error"
                failure_reason = "Test code failed to compile"
                log_lines.append(f"[✗] COMPILATION ERROR - {failure_reason}")
                
            # STEP 2: Did test EXECUTE? (look for "Running XTest" line)
            elif f"Running {fqn}" in combined or f"Running {test_class}" in combined:
                # Test EXECUTED - parse results
                pattern = rf"Tests run:\s*(\d+).*?Failures:\s*(\d+).*?Errors:\s*(\d+)"
                match = re.search(pattern, combined, flags=re.DOTALL | re.IGNORECASE)
                
                if match:
                    tests_run = int(match.group(1))
                    failures = int(match.group(2))
                    errors = int(match.group(3))
                    
                    log_lines.append(f"[RESULTS] Tests run: {tests_run}, Failures: {failures}, Errors: {errors}")
                    
                    if tests_run > 0:
                        if failures == 0 and errors == 0:
                            # Test PASSED
                            success = True
                            result_type = "pass"
                            failure_reason = None
                            log_lines.append("[✓] PASS - No breaking change detected")
                        else:
                            # Test FAILED - BREAKING CHANGE!
                            success = False
                            result_type = "test_failure_breaking_change"
                            failure_reason = f"Tests run: {tests_run}, Failures: {failures}, Errors: {errors}"
                            log_lines.append("[✗] BREAKING CHANGE - Test executed but failed")
                            log_lines.append("[INFO] BUILD FAILURE is expected when test fails")
                    else:
                        success = False
                        result_type = "test_failure_breaking_change"
                        failure_reason = "0 tests ran"
                        log_lines.append("[?] WARNING - 0 tests ran")
                else:
                    success = False
                    result_type = "test_failure_breaking_change"
                    failure_reason = "Could not parse test results"
                    log_lines.append("[?] Could not parse test results")
                    
            # STEP 3: Test did NOT execute - check BUILD status
            elif "BUILD SUCCESS" in stdout and proc.returncode == 0:
                # BUILD SUCCESS but test did not execute - TRANSPLANT ISSUE
                success = False  # Mark as failure (so it goes to failed list)
                result_type = "transplant_issue"
                failure_reason = "Test did not execute despite BUILD SUCCESS"
                log_lines.append("[⚠] TRANSPLANT ISSUE - BUILD SUCCESS but test did not execute")
                log_lines.append("[INFO] Not counted as breaking change")
                
            else:
                # BUILD FAILURE and test did not execute
                success = False
                result_type = "build_failure_without_test_execution"
                failure_reason = "BUILD FAILURE - Test could not execute"
                log_lines.append("[✗] BUILD FAILURE - Test did not execute")
                log_lines.append("[INFO] Build failed before test could run")
                    
        except subprocess.TimeoutExpired:
            log_lines.append("[ERROR] ✗ Timeout (600s) - Force killing container")
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=10)
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
                log_lines.append(f"[CLEANUP] ✓ Killed and removed container: {container_name}")
            except Exception as cleanup_err:
                log_lines.append(f"[CLEANUP] ✗ Failed to cleanup container: {cleanup_err}")
            result_type = "build_failure_without_test_execution"
            failure_reason = "Timeout - execution exceeded 600 seconds"
            success = False
        except Exception as e:
            log_lines.append(f"[EXCEPTION] ✗ {e}")
            import traceback
            log_lines.append(traceback.format_exc())
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
                log_lines.append(f"[CLEANUP] ✓ Removed container after exception: {container_name}")
            except Exception as cleanup_err:
                log_lines.append(f"[CLEANUP] ✗ Failed to cleanup container: {cleanup_err}")
            result_type = "build_failure_without_test_execution"
            failure_reason = f"Exception: {str(e)}"
            success = False
        
        log_lines.append("="*80)
        log_text = "\n".join(log_lines)
        log_path.write_text(log_text, encoding="utf-8")
        
        err_info = classify_compilation_error(log_text) if not success else None
        
        # Return detailed information
        return success, err_info, str(log_path), result_type, failure_reason if 'failure_reason' in locals() else None
        
    finally:
        # Always ensure container is removed
        try:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
        except:
            pass
        
        # Always remove temp directory
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except:
                pass


def process_test_execution(args):
    """Wrapper function to execute a single test in parallel."""
    (image_tag, custom_id, test_root, package_decl, java_file, txt_path) = args
    
    success, err_info, log_path, result_type, failure_reason = run_single_module_test(
        image_tag, custom_id, test_root, package_decl, java_file, txt_path
    )
    
    return (custom_id, java_file, success, err_info, result_type, failure_reason, log_path)


def main():
    global success_count, failure_count, compilation_error_count, test_failure_count, transplant_issue_count, build_failure_count
    global results, carry_forward_instances, carry_forward_tests, multi_module_instances

    START_ID = 1
    END_ID = 190
    MAX_WORKERS = 4

    safe_print(f"\n{'='*80}")
    safe_print(f"Breaking Stage - SINGLE-MODULE ONLY (PARALLEL EXECUTION)")
    safe_print(f"Transplant Logic: EXACT SAME as PRE script")
    safe_print(f"Approach: javac + surefire:test")
    safe_print(f"Timeout: 600 seconds (10 minutes) per test")
    safe_print(f"ID Range: {START_ID} to {END_ID}")
    safe_print(f"Max Parallel Workers: {MAX_WORKERS}")
    safe_print(f"{'='*80}\n")
    
    cleanup_stale_containers()
    
    multi_module_instances = _load_multi_module_list()
    
    # Load PRE results (carry forward from execute_results_pre.json)
    pre_data = json.loads(Path(PRE_RESULTS_PATH).read_text(encoding="utf-8"))
    carry_forward_tests.update(pre_data.get("carry_forward_tests", {}))
    carry_forward_instances.update(pre_data.get("carry_forward_instances", []))
    safe_print(f"[INFO] Loaded {len(carry_forward_instances)} instances with passing tests")
    safe_print(f"[INFO] Will skip {len(multi_module_instances)} multi-module instances\n")

    if BREAKING_OUTPUT.exists():
        try:
            existing = json.loads(BREAKING_OUTPUT.read_text(encoding="utf-8"))
            results = existing.get("results", {})
            
            for instance_id, instance_data in results.items():
                tests_data = instance_data.get("tests", {})
                passed = tests_data.get("passed", [])
                failed = tests_data.get("failed", [])
                
                success_count += len(passed)
                
                for fail_entry in failed:
                    failure_count += 1
                    if isinstance(fail_entry, dict):
                        rt = fail_entry.get("result_type")
                        if rt == "compilation_error":
                            compilation_error_count += 1
                        elif rt == "test_failure_breaking_change":
                            test_failure_count += 1
                        elif rt == "transplant_issue":
                            transplant_issue_count += 1
                        elif rt == "build_failure_without_test_execution":
                            build_failure_count += 1
            
            safe_print(f"[RESUME] Pass={success_count}, Fail={failure_count}")
            safe_print(f"  - Compilation Errors: {compilation_error_count}")
            safe_print(f"  - Test Failures (Breaking): {test_failure_count}")
            safe_print(f"  - Build Failures: {build_failure_count}")
            safe_print(f"  - Transplant Issues: {transplant_issue_count}\n")
        except Exception as e:
            safe_print(f"[WARN] Could not load existing results: {e}\n")

    LOG_DIR_BATCH_BRE.mkdir(parents=True, exist_ok=True)
    
    processed_count = 0
    skipped_count = 0

    try:
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

                if custom_id in results:
                    safe_print(f"[SKIP] {custom_id} - Already processed (resuming)")
                    skipped_count += 1
                    continue

                # SKIP MULTI-MODULE INSTANCES
                if custom_id in multi_module_instances:
                    safe_print(f"[SKIP] {custom_id} - Multi-module (use multi-module script)")
                    skipped_count += 1
                    continue

                # Get test_root and package from pkg_info (SAME as PRE)
                test_root, real_package = pkg_info.get((custom_id, "breaking"), (None, None))
                if not test_root or not real_package:
                    safe_print(f"[SKIP] {custom_id} - No test_root/package")
                    skipped_count += 1
                    continue

                safe_print(f"\n{custom_id} [SINGLE-MODULE] | {commit[:8]}")
                safe_print(f"[INFO] Package: {real_package}, Test Root: {test_root}")

                image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
                per_test_status = {"passed": [], "failed": []}

                # === FIND CORRESPONDING .TXT FILES FOR TESTS (SAME as PRE) ===
                src_dir = ABC_ROOT / custom_id
                test_files_info = []
                for java_file in passed_tests:
                    for txt_path in src_dir.rglob("*.txt"):
                        java_name, _ = _to_java_filename(txt_path.name)
                        if java_name == java_file:
                            test_files_info.append((java_file, txt_path))
                            break

                safe_print(f"[INFO] Testing {len(test_files_info)} file(s) in parallel...")
                
                # Prepare execution tasks
                test_tasks = []
                for java_file, txt_path in test_files_info:
                    task_args = (image_tag, custom_id, test_root, real_package, java_file, txt_path)
                    test_tasks.append(task_args)
                
                # Execute tests in parallel
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    future_to_test = {executor.submit(process_test_execution, task): task for task in test_tasks}
                    
                    for future in as_completed(future_to_test):
                        task = future_to_test[future]
                        try:
                            cid, java_file, success, err_info, result_type, failure_reason, log_path = future.result()
                            
                            with results_lock:
                                if success:
                                    safe_print(f"  → {java_file}... ✓ PASS")
                                    success_count += 1
                                    per_test_status["passed"].append(java_file)
                                else:
                                    failure_entry = {
                                        "file": java_file,
                                        "result_type": result_type,
                                        "failure_reason": failure_reason,
                                        "category": err_info.get("category", "unknown") if err_info else "unknown",
                                        "log_path": log_path
                                    }
                                    
                                    if result_type == "compilation_error":
                                        safe_print(f"  → {java_file}... ✗ COMPILATION ERROR")
                                        compilation_error_count += 1
                                    elif result_type == "test_failure_breaking_change":
                                        safe_print(f"  → {java_file}... ✗ BREAKING CHANGE")
                                        test_failure_count += 1
                                    elif result_type == "transplant_issue":
                                        safe_print(f"  → {java_file}... ⚠ TRANSPLANT ISSUE")
                                        transplant_issue_count += 1
                                    elif result_type == "build_failure_without_test_execution":
                                        safe_print(f"  → {java_file}... ✗ BUILD FAILURE")
                                        build_failure_count += 1
                                    else:
                                        safe_print(f"  → {java_file}... ✗ FAILED")
                                    
                                    failure_count += 1
                                    per_test_status["failed"].append(failure_entry)
                                    
                        except Exception as exc:
                            java_file = task[4]
                            with results_lock:
                                safe_print(f"  → {java_file}... ✗ EXCEPTION: {exc}")
                                failure_count += 1
                                build_failure_count += 1
                                per_test_status["failed"].append({
                                    "file": java_file,
                                    "result_type": "build_failure_without_test_execution",
                                    "failure_reason": f"Exception: {str(exc)}",
                                    "category": "exception",
                                    "log_path": ""
                                })

                results[custom_id] = {
                    "tests": per_test_status,
                    "type": "single-module",
                    "test_root": test_root,
                    "package": real_package,
                    "summary": {
                        "total_passed": len(per_test_status["passed"]),
                        "total_failed": len(per_test_status["failed"]),
                        "compilation_errors": sum(1 for f in per_test_status["failed"] 
                                                 if isinstance(f, dict) and f.get("result_type") == "compilation_error"),
                        "test_failures_breaking_change": sum(1 for f in per_test_status["failed"] 
                                                            if isinstance(f, dict) and f.get("result_type") == "test_failure_breaking_change"),
                        "build_failures": sum(1 for f in per_test_status["failed"] 
                                             if isinstance(f, dict) and f.get("result_type") == "build_failure_without_test_execution"),
                        "transplant_issues": sum(1 for f in per_test_status["failed"] 
                                                if isinstance(f, dict) and f.get("result_type") == "transplant_issue")
                    }
                }
                
                processed_count += 1

                BREAKING_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
                output_data = {
                    "results": results,
                    "summary": {
                        "total_pass": success_count,
                        "total_fail": failure_count,
                        "compilation_errors": compilation_error_count,
                        "test_failures_breaking_change": test_failure_count,
                        "build_failures_without_test_execution": build_failure_count,
                        "transplant_issues": transplant_issue_count,
                        "processed": processed_count,
                        "skipped": skipped_count
                    }
                }
                BREAKING_OUTPUT.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
                safe_print(f"[SAVED] Results for {custom_id}")

    except KeyboardInterrupt:
        safe_print(f"\n\n[INTERRUPTED] Saving progress...")
        BREAKING_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        output_data = {
            "results": results,
            "summary": {
                "total_pass": success_count,
                "total_fail": failure_count,
                "compilation_errors": compilation_error_count,
                "test_failures_breaking_change": test_failure_count,
                "build_failures_without_test_execution": build_failure_count,
                "transplant_issues": transplant_issue_count,
                "processed": processed_count,
                "skipped": skipped_count
            }
        }
        BREAKING_OUTPUT.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
        safe_print(f"[SAVED] Progress saved. Resume by running again.")
        cleanup_stale_containers()
        return

    finally:
        safe_print(f"\n[CLEANUP] Checking for temporary directories...")
        temp_pattern = Path("/tmp")
        cleaned_count = 0
        for temp_dir in temp_pattern.glob("breaking_single_*"):
            try:
                shutil.rmtree(temp_dir)
                cleaned_count += 1
            except:
                pass
        if cleaned_count > 0:
            safe_print(f"[CLEANUP] Removed {cleaned_count} temporary directories")
        
        cleanup_stale_containers()
        safe_print(f"[CLEANUP] Done\n")

    safe_print(f"\n{'='*80}")
    safe_print(f"✓ COMPLETE - SINGLE-MODULE")
    safe_print(f"Processed: {processed_count}")
    safe_print(f"Pass: {success_count}")
    safe_print(f"Fail: {failure_count}")
    safe_print(f"  - Compilation Errors: {compilation_error_count}")
    safe_print(f"  - Test Failures (Breaking Change): {test_failure_count}")
    safe_print(f"  - Build Failures: {build_failure_count}")
    safe_print(f"  - Transplant Issues: {transplant_issue_count}")
    safe_print(f"")
    safe_print(f"BREAKING CHANGES DETECTED: {test_failure_count}")
    safe_print(f"{'='*80}\n")


if __name__ == "__main__":
    main()