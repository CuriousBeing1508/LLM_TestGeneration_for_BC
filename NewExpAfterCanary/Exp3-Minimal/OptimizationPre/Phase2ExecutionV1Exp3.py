#!/usr/bin/env python3
"""
Script: execute_phase_pre.py
Description: Phase 2 - Execute ONLY pre-compiled test files on PRE stage
             Loads compilation results from Phase 1, executes only successful compilations.
             Features: Incremental saving, resume capability, full console output, auto container cleanup.
             Skips PMD/CheckStyle checks.
Author: Optimized version with resume capability, verbose output, and stale container cleanup
"""

import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from common import parse_package_summary, LOG_DIR_BATCH, clean_llm_code

# Threading for parallel execution
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE, SECONDARY_DRIVE

# Thread-safe locks
results_lock = threading.Lock()
print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    """Thread-safe print function for parallel execution."""
    with print_lock:
        print(*args, **kwargs)

# # === CONFIGURATION GPT===

CSV_PATH = PRIMARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = PRIMARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
COMPILE_INPUT = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/compile_results_pre.json"
EXECUTE_OUTPUT = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/execute_results_pre.json"
ABC_ROOT = PRIMARY_DRIVE / "FilteredDataset/Exp3LLMOutput/GPT4o"

# CSV_PATH = PRIMARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
# SUMMARY_PATH = PRIMARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
# COMPILE_INPUT = PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/compile_results_pre.json"
# EXECUTE_OUTPUT = PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/execute_results_pre.json"
# ABC_ROOT = PRIMARY_DRIVE / "FilteredDataset/Exp7LLMOutput/GPT4o"
# MODEL_NAME = ABC_ROOT.name

# === CONFIGURATION Qwen3===
CSV_PATH = SECONDARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = SECONDARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
COMPILE_INPUT = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/compile_results_pre.json"
EXECUTE_OUTPUT = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/execute_results_pre.json"
ABC_ROOT = SECONDARY_DRIVE / "FilteredDataset/Exp3LLMOutput/Qwen_480b_cloud"

MODEL_NAME = ABC_ROOT.name

# # === CONFIGURATION Qwen3 Exp6===
# CSV_PATH = SECONDARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
# SUMMARY_PATH = SECONDARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
# COMPILE_INPUT = SECONDARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/compile_results_pre.json"
# EXECUTE_OUTPUT = SECONDARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/execute_results_pre.json"
# ABC_ROOT = SECONDARY_DRIVE / "FilteredDataset/Exp6LLMOutput/Qwen3_480b_cloud"
# MODEL_NAME = ABC_ROOT.name

# Parse package info
pkg_info = parse_package_summary(SUMMARY_PATH)

# Execution tracking
exec_success_count = 0
exec_failure_count = 0

# Results
execution_results = {}
carry_forward_instances = set()
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})

# Track processed instances for resume capability
processed_instances = set()

# File counts from compilation (loaded, not modified)
file_counts = defaultdict(lambda: {"files_generated": 0, "files_compiled": 0})
test_counts = defaultdict(lambda: {"tests_in_generated_files": 0, "tests_in_compiled_files": 0})


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
    """
    Convert .txt filename to .java filename and class name.
    Example: BBC10U1Test_prompt.txt -> (BBC10U1Test.java, BBC10U1Test)
    """
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
    
    This is CRITICAL for Java compilation:
    - Package declaration must match directory path
    - Example: package se.kth.core; must be in se/kth/core/ directory
    
    Args:
        code_text: Original LLM-generated code
        package_decl: Correct package from pkg_info
        class_name: Correct class name matching filename
    
    Returns:
        Corrected Java code with proper package and class name
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
        
        # List all containers with our naming pattern
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=exec_", "--format", "{{.Names}}"],
            capture_output=True,
            text=True
        )
        
        container_names = result.stdout.strip().split('\n')
        container_names = [name for name in container_names if name and name.startswith('exec_')]
        
        if container_names:
            safe_print(f"[CLEANUP] Found {len(container_names)} stale container(s)")
            removed_count = 0
            for name in container_names:
                try:
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
                    safe_print(f"[CLEANUP]    Removed: {name}")
                    removed_count += 1
                except Exception as e:
                    safe_print(f"[CLEANUP]    Failed to remove {name}: {e}")
            safe_print(f"[CLEANUP] Successfully removed {removed_count}/{len(container_names)} container(s)")
        else:
            safe_print(f"[CLEANUP] No stale containers found ")
    except Exception as e:
        safe_print(f"[CLEANUP] Warning: Could not check for stale containers: {e}")


def save_results_incrementally():
    """
    Save current state to JSON file.
    This allows resuming if script is stopped.
    Thread-safe.
    """
    with results_lock:
        EXECUTE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        
        # Calculate totals from file_counts (preserved from compilation)
        total_files_generated = sum(v["files_generated"] for v in file_counts.values())
        total_files_compiled = sum(v["files_compiled"] for v in file_counts.values())
        total_tests_generated = sum(v["tests_in_generated_files"] for v in test_counts.values())
        total_tests_compiled = sum(v["tests_in_compiled_files"] for v in test_counts.values())
        
        output_data = {
            "execution_results": execution_results,
            "carry_forward_instances": list(carry_forward_instances),
            "carry_forward_tests": dict(carry_forward_tests),
            "processed_instances": list(processed_instances),
            "file_counts": dict(file_counts),
            "test_counts": dict(test_counts),
            "summary": {
                "total_files_generated": total_files_generated,
                "total_files_compiled": total_files_compiled,
                "total_files_executed": exec_success_count + exec_failure_count,
                "total_passed_on_pre": exec_success_count,
                "total_failed_on_pre": exec_failure_count,
                "compilation_success_rate": f"{(total_files_compiled / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%",
                "execution_success_rate": f"{(exec_success_count / (exec_success_count + exec_failure_count) * 100) if (exec_success_count + exec_failure_count) > 0 else 0:.2f}%",
                "total_tests_in_generated_files": total_tests_generated,
                "total_tests_in_compiled_files": total_tests_compiled
            }
        }
        
        EXECUTE_OUTPUT.write_text(json.dumps(output_data, indent=2), encoding="utf-8")


def execute_compiled_test(image_tag: str, custom_id: str, test_root: str,
                          java_file: str, package_decl: str, txt_path: Path):
    """
    Execute a pre-compiled test file (compile + run).
    NOW WITH: Automatic stale container cleanup
    
    Process:
    1. Read .txt file
    2. Extract and clean Java code
    3. Fix package declaration and class name
    4. Create temp directory with LLMTest organizer folder
    5. Write Java file in correct package structure
    6. Mount to Docker, compile and execute test (skip PMD/CheckStyle)
    7. Show full Maven output in console
    8. Parse results (pass/fail)
    9. Cleanup temp directory and container
    
    Returns:
        (success, log_path, failure_type)
    """
    # Create minimal temp directory for THIS test only
    temp_dir = Path(f"/tmp/execute_{custom_id}_{java_file.replace('.java', '')}")
    
    # === NEW: Generate unique container name for tracking ===
    container_name = f"exec_{custom_id}_{java_file.replace('.java', '')}_{os.getpid()}_{threading.get_ident()}"
    
    try:
        # Clean if exists from previous run
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        
        # === NEW: Kill any existing container with same name ===
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=10
            )
        except:
            pass  # Container doesn't exist, that's fine
        
        # === STEP 1: Extract and process LLM code ===
        raw = txt_path.read_text(encoding="utf-8", errors="ignore")
        java_only = _extract_llm_java_block(raw)
        if not java_only:
            return False, "", "no_code"
        
        cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
        if not cleaned:
            return False, "", "empty_code"
        
        # === STEP 2: Fix package and class name ===
        _, class_base = _to_java_filename(txt_path.name)
        final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)
        
        # === STEP 3: Create directory structure ===
        # Create LLMTest organizer folder to keep our tests separate
        llm_test_dir = temp_dir / "LLMTest"
        pkg_path = Path(*package_decl.split("."))
        java_dir = llm_test_dir / pkg_path
        java_dir.mkdir(parents=True, exist_ok=True)
        
        # === STEP 4: Write Java file ===
        java_file_path = java_dir / java_file
        java_file_path.write_text(final_code, encoding="utf-8")
        
        # === STEP 5: Setup logging ===
        log_path = LOG_DIR_BATCH / f"{custom_id}_{java_file}_execute.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # === STEP 6: Detect project root ===
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
        
        # === STEP 7: Build compile + execute command ===
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
        
        # === STEP 8: Run Docker ===
        # Mount LLMTest folder to test_root (hides original tests)
        # === MODIFIED: Add container name and explicit --rm ===
        cmd = [
            "docker", "run",
            "--name", container_name,  # NEW: Named container for tracking
            "--rm",  # Keep --rm for auto-cleanup on success
            "--platform", "linux/amd64",
            "-v", f"{llm_test_dir}:{test_root}:ro",
            image_tag,
            "sh", "-c", exec_cmd
        ]
        
        # Print header to console
        safe_print(f"\n{'='*80}")
        safe_print(f"PHASE 2: EXECUTION - {java_file} for {custom_id}")
        safe_print(f"Container: {container_name}")
        safe_print(f"{'='*80}")
        safe_print(f"FQN: {fqn}")
        safe_print(f"Package: {package_decl}")
        safe_print(f"{'='*80}")
        
        log_lines = [
            "="*80,
            f"PHASE 2: EXECUTION - {java_file} for {custom_id}",
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
        failure_type = None
        
        try:
            # === MODIFIED: Add explicit timeout (600s = 10 minutes) ===
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            stdout = proc.stdout
            stderr = proc.stderr
            combined = stdout + "\n" + stderr
            
            # Print full output to console (like original script)
            safe_print(stdout)
            if stderr:
                safe_print(stderr)
            
            log_lines.append("=== STDOUT ===")
            log_lines.append(stdout)
            log_lines.append("")
            log_lines.append("=== STDERR ===")
            log_lines.append(stderr)
            log_lines.append("")
            
            # === STEP 9: Parse test results ===
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
                        if not success:
                            failure_type = "test_failure"
                            log_lines.append("[RESULT]  Test FAILED on PRE")
                            safe_print("[RESULT]  Test FAILED on PRE")
                        else:
                            log_lines.append("[RESULT]  Test PASSED on PRE")
                            safe_print("[RESULT]  Test PASSED on PRE")
                    else:
                        log_lines.append("[RESULT]  No tests ran")
                        safe_print("[RESULT]  No tests ran")
                        failure_type = "no_tests_ran"
                else:
                    log_lines.append("[RESULT]  Could not parse test results")
                    safe_print("[RESULT]  Could not parse test results")
                    failure_type = "parse_error"
            else:
                if "BUILD SUCCESS" in stdout and proc.returncode == 0:
                    success = True
                    log_lines.append("[RESULT]  Test PASSED (BUILD SUCCESS)")
                    safe_print("[RESULT]  Test PASSED (BUILD SUCCESS)")
                else:
                    failure_type = "execution_failure"
                    log_lines.append("[RESULT]  Test did not execute")
                    safe_print("[RESULT]  Test did not execute")
                    
        except subprocess.TimeoutExpired:
            # === NEW: Force kill container on timeout ===
            log_lines.append("[ERROR]  Timeout (600s) - Force killing container")
            safe_print("[ERROR]  Timeout (600s) - Force killing container")
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=10)
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
                log_lines.append(f"[CLEANUP]  Killed and removed container: {container_name}")
            except Exception as cleanup_err:
                log_lines.append(f"[CLEANUP]  Failed to cleanup container: {cleanup_err}")
            failure_type = "timeout"
            success = False
        except Exception as e:
            log_lines.append(f"[EXCEPTION]  {e}")
            safe_print(f"[EXCEPTION]  {e}")
            # === NEW: Cleanup on exception ===
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
                log_lines.append(f"[CLEANUP]  Removed container after exception: {container_name}")
            except Exception as cleanup_err:
                log_lines.append(f"[CLEANUP]  Failed to cleanup container: {cleanup_err}")
            failure_type = "exception"
            success = False
        
        log_lines.append("="*80)
        log_text = "\n".join(log_lines)
        log_path.write_text(log_text, encoding="utf-8")
        
        return success, str(log_path), failure_type
        
    finally:
        # === NEW: Always ensure container is removed (belt and suspenders) ===
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=10
            )
        except:
            pass  # Already cleaned up or doesn't exist
        
        # === CLEANUP: Always remove temp directory ===
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                safe_print(f"[WARN] Failed to remove temp dir {temp_dir}: {e}")


def process_execution_task(args):
    """Wrapper for parallel execution."""
    (image_tag, custom_id, test_root, java_file, package_decl, txt_path) = args
    
    success, log_path, failure_type = execute_compiled_test(
        image_tag, custom_id, test_root, java_file, package_decl, txt_path
    )
    
    return (custom_id, java_file, success, log_path, failure_type)


def main():
    global exec_success_count, exec_failure_count, processed_instances

    # === BATCH CONFIGURATION ===
    START_ID = 1
    END_ID = 190
    MAX_WORKERS = 4  # Lower parallelism for execution

    safe_print(f"\n{'='*80}")
    safe_print(f"PHASE 2: EXECUTION ONLY (PRE Stage)")
    safe_print(f"Features: Incremental saving, resume capability, full output")
    safe_print(f"Features: Auto container cleanup (no more stale containers!)")
    safe_print(f"Skips: PMD, CheckStyle, Enforcer (fast execution)")
    safe_print(f"ID Range: {START_ID} to {END_ID}")
    safe_print(f"Max Parallel Workers: {MAX_WORKERS}")
    safe_print(f"{'='*80}\n")

    # === NEW: Cleanup stale containers from previous runs ===
    cleanup_stale_containers()

    # === LOAD COMPILATION RESULTS FROM PHASE 1 ===
    if not COMPILE_INPUT.exists():
        safe_print(f"[ERROR] Compilation results not found: {COMPILE_INPUT}")
        safe_print(f"[ERROR] Please run compile_phase_pre.py first!")
        return

    compile_data = json.loads(COMPILE_INPUT.read_text(encoding="utf-8"))
    compilation_results = compile_data.get("compilation_results", {})
    compile_summary = compile_data.get("summary", {})
    
    # === CRITICAL: Load file_counts and test_counts from compilation ===
    compile_file_counts = compile_data.get("file_counts", {})
    compile_test_counts = compile_data.get("test_counts", {})
    
    # These are the GROUND TRUTH counts from compilation phase
    file_counts.update(defaultdict(lambda: {"files_generated": 0, "files_compiled": 0},
                                   compile_file_counts))
    test_counts.update(defaultdict(lambda: {"tests_in_generated_files": 0, "tests_in_compiled_files": 0},
                                   compile_test_counts))

    # Calculate totals from loaded data
    total_files_generated = sum(v["files_generated"] for v in file_counts.values())
    total_files_compiled = sum(v["files_compiled"] for v in file_counts.values())
    total_tests_generated = sum(v["tests_in_generated_files"] for v in test_counts.values())
    total_tests_compiled = sum(v["tests_in_compiled_files"] for v in test_counts.values())

    safe_print(f"[INFO] Loaded compilation results:")
    safe_print(f"  Total files generated: {total_files_generated}")
    safe_print(f"  Total files compiled: {total_files_compiled}")
    safe_print(f"  Total files failed compilation: {total_files_generated - total_files_compiled}")
    safe_print(f"  Total @Test methods in generated files: {total_tests_generated}")
    safe_print(f"  Total @Test methods in compiled files: {total_tests_compiled}")
    safe_print(f"\n")

    # === LOAD EXISTING EXECUTION RESULTS FOR RESUME ===
    if EXECUTE_OUTPUT.exists():
        try:
            existing = json.loads(EXECUTE_OUTPUT.read_text(encoding="utf-8"))
            execution_results.update(existing.get("execution_results", {}))
            carry_forward_instances.update(set(existing.get("carry_forward_instances", [])))
            carry_forward_tests.update(defaultdict(lambda: {"passed": [], "failed": []},
                                                   existing.get("carry_forward_tests", {})))
            processed_instances.update(set(existing.get("processed_instances", [])))
            
            # Recalculate execution counts
            for instance_result in execution_results.values():
                exec_success_count += len(instance_result.get("passed", []))
                exec_failure_count += len(instance_result.get("failed", []))
            
            safe_print(f"[RESUME] Loaded existing execution results:")
            safe_print(f"  Processed instances: {len(processed_instances)}")
            safe_print(f"  Files passed: {exec_success_count}")
            safe_print(f"  Files failed: {exec_failure_count}")
            safe_print(f"[RESUME] Will skip already processed instances\n")
        except Exception as e:
            safe_print(f"[WARN] Failed to load existing execution results: {e}\n")

    try:
        with open(CSV_PATH) as f:
            reader = csv.DictReader(f)
            for row in reader:
                custom_id = row["custom_id"].strip()

                # Extract numeric suffix (e.g., "BBC10" -> 10)
                match = re.search(r"(\d+)$", custom_id)
                if not match:
                    continue
                cid_num = int(match.group(1))
                if cid_num < START_ID or cid_num > END_ID:
                    continue

                # === SMART SKIP: Skip already processed instances ===
                if custom_id in processed_instances:
                    safe_print(f"[SKIP] {custom_id} - Already processed (resuming)")
                    continue

                commit = row["breakingCommit"].strip()
                if not commit:
                    continue

                # === CHECK IF THIS INSTANCE HAS COMPILED FILES ===
                if custom_id not in compilation_results:
                    safe_print(f"[SKIP] {custom_id} - No compilation results found")
                    processed_instances.add(custom_id)
                    save_results_incrementally()
                    continue

                compiled_files = compilation_results[custom_id].get("compiled", [])
                if not compiled_files:
                    safe_print(f"[SKIP] {custom_id} - No files compiled successfully")
                    processed_instances.add(custom_id)
                    save_results_incrementally()
                    continue

                # Get test_root and package from pkg_info
                test_root, real_package = pkg_info.get((custom_id, "pre"), (None, None))
                if not test_root or not real_package:
                    safe_print(f"[SKIP] {custom_id} - No test_root/package")
                    processed_instances.add(custom_id)
                    save_results_incrementally()
                    continue

                # === FIND CORRESPONDING .TXT FILES FOR COMPILED TESTS ===
                src_dir = ABC_ROOT / custom_id
                compiled_files_info = []
                for java_file in compiled_files:
                    for txt_path in src_dir.rglob("*.txt"):
                        java_name, _ = _to_java_filename(txt_path.name)
                        if java_name == java_file:
                            compiled_files_info.append((java_file, txt_path))
                            break

                image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"

                safe_print(f"\n{custom_id} | {commit[:8]}")
                safe_print(f"[INFO] Executing {len(compiled_files_info)} pre-compiled file(s)")

                passed_tests = []
                failed_tests = []
                test_failures = []
                execution_failures = []

                # Prepare execution tasks
                exec_tasks = []
                for java_file, txt_path in compiled_files_info:
                    task_args = (image_tag, custom_id, test_root, java_file, real_package, txt_path)
                    exec_tasks.append(task_args)

                safe_print(f"[INFO] Running {len(exec_tasks)} test(s) in parallel...")

                # Execute tests in parallel
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    future_to_task = {executor.submit(process_execution_task, task): task for task in exec_tasks}

                    for future in as_completed(future_to_task):
                        task = future_to_task[future]
                        java_file = task[3]

                        try:
                            cid, jf, success, log_path, failure_type = future.result()

                            with results_lock:
                                if success:
                                    safe_print(f"  → {java_file}...  PASSED on PRE")
                                    exec_success_count += 1
                                    passed_tests.append(java_file)
                                    carry_forward_tests[custom_id]["passed"].append(java_file)
                                else:
                                    safe_print(f"  → {java_file}...  FAILED ({failure_type})")
                                    exec_failure_count += 1
                                    failed_tests.append(java_file)
                                    carry_forward_tests[custom_id]["failed"].append(java_file)

                                    failure_entry = {
                                        "file": java_file,
                                        "failure_type": failure_type,
                                        "log_path": log_path
                                    }

                                    if failure_type == "test_failure":
                                        test_failures.append(failure_entry)
                                    else:
                                        execution_failures.append(failure_entry)

                        except Exception as exc:
                            with results_lock:
                                safe_print(f"  → {java_file}...  EXCEPTION: {exc}")
                                exec_failure_count += 1
                                failed_tests.append(java_file)
                                carry_forward_tests[custom_id]["failed"].append(java_file)
                                execution_failures.append({
                                    "file": java_file,
                                    "failure_type": "exception",
                                    "log_path": ""
                                })

                # Mark instance for carry-forward if it has passing tests
                if passed_tests:
                    carry_forward_instances.add(custom_id)

                # Store execution results for this instance
                execution_results[custom_id] = {
                    "passed": passed_tests,
                    "failed": failed_tests,
                    "failure_breakdown": {
                        "test_failures": test_failures,
                        "execution_failures": execution_failures
                    },
                    "summary": {
                        "total_passed": len(passed_tests),
                        "total_failed": len(failed_tests),
                        "test_failure_count": len(test_failures),
                        "execution_failure_count": len(execution_failures)
                    }
                }

                # Mark instance as processed
                processed_instances.add(custom_id)

                # === SAVE AFTER EACH INSTANCE (CRITICAL FOR RESUME) ===
                save_results_incrementally()
                safe_print(f"[SAVED] Results for {custom_id} (can resume from here)")

    except KeyboardInterrupt:
        safe_print(f"\n\n[INTERRUPTED] Ctrl+C detected - saving current progress...")
        save_results_incrementally()
        safe_print(f"[SAVED] Progress saved. You can resume by running this script again.")
        safe_print(f"[INFO] Processed {len(processed_instances)} instances before interruption")
        # Cleanup stale containers on interrupt
        safe_print(f"\n[CLEANUP] Cleaning up any running containers...")
        cleanup_stale_containers()
        return

    finally:
        # === FINAL CLEANUP: Remove any orphaned temp directories ===
        safe_print(f"\n[CLEANUP] Checking for temporary directories...")
        temp_pattern = Path("/tmp")
        cleaned_count = 0
        for temp_dir in temp_pattern.glob("execute_*"):
            try:
                shutil.rmtree(temp_dir)
                cleaned_count += 1
            except:
                pass
        if cleaned_count > 0:
            safe_print(f"[CLEANUP] Removed {cleaned_count} temporary directories")
        
        # === FINAL CLEANUP: Remove any remaining stale containers ===
        safe_print(f"[CLEANUP] Final container cleanup...")
        cleanup_stale_containers()
        safe_print(f"[CLEANUP] Done\n")

    # === FINAL SUMMARY ===
    safe_print(f"\n{'='*80}")
    safe_print(f" PHASE 2 COMPLETE: EXECUTION")
    safe_print(f"")
    safe_print(f"INSTANCES:")
    safe_print(f"  Total processed: {len(processed_instances)}")
    safe_print(f"  With passing tests: {len(carry_forward_instances)}")
    safe_print(f"")
    safe_print(f"FILE STATISTICS (FROM COMPILATION PHASE):")
    safe_print(f"  Files Generated: {total_files_generated}")
    safe_print(f"  Files Compiled: {total_files_compiled}")
    safe_print(f"  Files Failed Compilation: {total_files_generated - total_files_compiled}")
    safe_print(f"  Compilation Success Rate: {(total_files_compiled / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%")
    safe_print(f"")
    safe_print(f"EXECUTION RESULTS:")
    safe_print(f"  Files Executed: {exec_success_count + exec_failure_count}")
    safe_print(f"  Files PASSED on PRE: {exec_success_count}")
    safe_print(f"  Files FAILED on PRE: {exec_failure_count}")
    safe_print(f"  Execution Success Rate: {(exec_success_count / (exec_success_count + exec_failure_count) * 100) if (exec_success_count + exec_failure_count) > 0 else 0:.2f}%")
    safe_print(f"")
    safe_print(f"TEST METHOD STATISTICS:")
    safe_print(f"  @Test methods in generated files: {total_tests_generated}")
    safe_print(f"  @Test methods in compiled files: {total_tests_compiled}")
    safe_print(f"")
    safe_print(f"OUTPUT: {EXECUTE_OUTPUT}")
    safe_print(f"{'='*80}\n")


if __name__ == "__main__":
    main()