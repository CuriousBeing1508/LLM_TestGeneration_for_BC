#!/usr/bin/env python3
"""
Script: compile_phase_pre.py
Description: Phase 1 - Compile all LLM-generated test files for PRE stage
             Uses Docker to compile tests, tracking which files compile successfully.
             Features: Incremental saving, resume from where stopped, smart skipping.
             Dynamic worker allocation based on instance size.
             Full console output, skips PMD/CheckStyle checks.
             Minimal container cleanup (only stuck containers).
Author: Fixed version - correct success/failure logic, minimal cleanup
"""

import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from common import parse_package_summary, classify_compilation_error, LOG_DIR_BATCH, clean_llm_code

# Threading for parallel execution
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
# Thread-safe locks
results_lock = threading.Lock()
print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    """Thread-safe print function for parallel execution."""
    with print_lock:
        print(*args, **kwargs)

def get_optimal_workers(num_tests, max_workers=8):
    """
    Dynamically allocate workers based on number of tests.
    Prevents waste on small instances, maximizes throughput on large ones.
    
    Args:
        num_tests: Number of test files to process
        max_workers: Maximum workers to allocate (default 8 for memory safety)
    
    Returns:
        Optimal number of workers (2, 4, or 8)
    """
    if num_tests <= 2:
        return 2  # 1-2 tests: use 2 workers
    elif num_tests <= 4:
        return 4  # 3-4 tests: use 4 workers
    else:
        return max_workers  # 5+ tests: use max workers (8)

# # === CONFIGURATION GPT4o===
# CSV_PATH = "/Volumes/Rachna-HD/ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
# SUMMARY_PATH = "/Volumes/Rachna-HD/ConfigFiles/package_structure_summary.txt"
# COMPILE_OUTPUT = Path("/Volumes/Rachna-HD/GPTResults/Exp3BatchResults/pre/compile_results_pre.json")
# ABC_ROOT = Path("/Volumes/Rachna-HD/FilteredDataset/Exp3LLMOutput/GPT4o")
# MODEL_NAME = ABC_ROOT.name


# === CONFIGURATION Qwen===
CSV_PATH = "/Volumes/RachnaPSSD/ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = "/Volumes/RachnaPSSD/ConfigFiles/package_structure_summary.txt"
COMPILE_OUTPUT = Path("/Volumes/RachnaPSSD/Qwen480Results/Exp3BatchResults/pre/compile_results_pre.json")
ABC_ROOT = Path("/Volumes/RachnaPSSD/FilteredDataset/Exp3LLMOutput/Qwen_480b_cloud")
MODEL_NAME = ABC_ROOT.name

# === TIMEOUT CONFIGURATION ===
COMPILE_TIMEOUT = 300  # 5 minutes for compilation

# Parse package info from summary file
pkg_info = parse_package_summary(SUMMARY_PATH)

# Compilation tracking
compile_success_count = 0
compile_failure_count = 0

# Per-instance results
compilation_results = {}
file_counts = defaultdict(lambda: {"files_generated": 0, "files_compiled": 0})
test_counts = defaultdict(lambda: {"tests_in_generated_files": 0, "tests_in_compiled_files": 0})

# Track processed instances for resume capability
processed_instances = set()


def cleanup_stale_containers():
    """Remove only truly stuck/exited containers (not running ones)."""
    try:
        safe_print(f"\n[CLEANUP] Checking for stuck Docker containers...")
        
        # List only exited/dead containers with our naming pattern
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=compile_", "--filter", "status=exited", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        container_names = result.stdout.strip().split('\n')
        container_names = [name for name in container_names if name and name.startswith('compile_')]
        
        if container_names:
            safe_print(f"[CLEANUP] Found {len(container_names)} stuck container(s)")
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
            safe_print(f"[CLEANUP] No stuck containers found ✓")
    except Exception as e:
        safe_print(f"[CLEANUP] Warning: Could not check for stuck containers: {e}")


def count_all_generated_files(abc_root: Path, bump_ids: list):
    """
    Count ALL .txt files in output folder (matches first script logic).
    This gives us the TRUE total of generated files.
    
    Returns:
        dict: {custom_id: {"files_generated": count, "file_list": [...]}}
        int: total count across all instances
    """
    all_files = {}
    total = 0
    
    safe_print(f"\n{'='*80}")
    safe_print(f"COUNTING ALL GENERATED FILES IN: {abc_root}")
    safe_print(f"{'='*80}")
    
    for custom_id in bump_ids:
        instance_folder = abc_root / custom_id
        if instance_folder.exists():
            txt_files = sorted([f.name for f in instance_folder.glob("*.txt")])
            count = len(txt_files)
            all_files[custom_id] = {
                "files_generated": count,
                "file_list": txt_files
            }
            total += count
            if count > 0:
                safe_print(f"  {custom_id}: {count} files")
        else:
            all_files[custom_id] = {
                "files_generated": 0,
                "file_list": []
            }
    
    safe_print(f"{'='*80}")
    safe_print(f"TOTAL FILES GENERATED: {total}")
    safe_print(f"{'='*80}\n")
    
    return all_files, total


def _abc_has_any_file(custom_id: str) -> bool:
    """Check if custom_id directory has any files."""
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
    - Example: package se.abc.core; must be in se/abc/core/ directory
    
    Args:
        code_text: Original LLM-generated code
        package_decl: Correct package from pkg_info (e.g., "se.abc.assertgroup.core")
        class_name: Correct class name matching filename
    
    Returns:
        Corrected Java code with proper package and class name
    """
    code = code_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    # Replace or inject package declaration
    if re.search(r"^\s*package\s+[\w\.]+;\s*$", code, flags=re.MULTILINE):
        # LLM wrote a package - replace it with correct one
        code = re.sub(
            r"^\s*package\s+[\w\.]+;\s*$",
            f"package {package_decl};",
            code,
            count=1,
            flags=re.MULTILINE
        )
    else:
        # No package declaration - add it at the top
        code = f"package {package_decl};\n\n{code}"

    # Fix class name to match filename
    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)", r"\1" + class_name, code, count=1)

    return code


def _count_test_methods(java_path: Path) -> int:
    """Count @Test annotations in a Java file."""
    if not java_path.exists():
        return 0
    text = java_path.read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r'@Test\b', text))


def save_results_incrementally():
    """
    Save current state to JSON file.
    This allows resuming if script is stopped.
    Thread-safe.
    """
    with results_lock:
        COMPILE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        
        total_files_generated = sum(v["files_generated"] for v in file_counts.values())
        total_files_compiled = sum(v["files_compiled"] for v in file_counts.values())
        total_tests_generated = sum(v["tests_in_generated_files"] for v in test_counts.values())
        total_tests_compiled = sum(v["tests_in_compiled_files"] for v in test_counts.values())

        output_data = {
            "compilation_results": compilation_results,
            "file_counts": file_counts,
            "test_counts": test_counts,
            "processed_instances": list(processed_instances),
            "summary": {
                "total_files_generated": total_files_generated,
                "total_files_compiled": total_files_compiled,
                "total_files_failed_compilation": compile_failure_count,
                "total_tests_in_generated_files": total_tests_generated,
                "total_tests_in_compiled_files": total_tests_compiled,
                "compilation_success_rate": f"{(total_files_compiled / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%"
            }
        }
        
        COMPILE_OUTPUT.write_text(json.dumps(output_data, indent=2), encoding="utf-8")


def compile_test_in_docker(image_tag: str, custom_id: str, test_root: str,
                           java_file: str, package_decl: str, txt_path: Path):
    """
    Compile a single test file in Docker with timeout.
    Minimal cleanup - only kill on timeout.
    
    Process:
    1. Read .txt file
    2. Extract and clean Java code
    3. Fix package declaration and class name
    4. Create temp directory with LLMTest organizer folder
    5. Write Java file in correct package structure
    6. Mount to Docker and compile (skip PMD/CheckStyle)
    7. Show full Maven output in console
    8. Cleanup temp directory
    
    Returns:
        (success, err_info, log_path, test_method_count)
    """
    # Create minimal temp directory for THIS test only
    temp_dir = Path(f"/tmp/compile_{custom_id}_{java_file.replace('.java', '')}")
    
    # Generate unique container name for tracking
    container_name = f"compile_{custom_id}_{java_file.replace('.java', '')}_{int(time.time() * 1000)}"
    
    try:
        # Clean if exists from previous run
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        
        # === STEP 1: Extract and process LLM code ===
        raw = txt_path.read_text(encoding="utf-8", errors="ignore")
        java_only = _extract_llm_java_block(raw)
        if not java_only:
            return False, {"category": "no_code", "reason": "No Java code block found"}, "", 0
        
        cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
        if not cleaned:
            return False, {"category": "empty_code", "reason": "Empty after cleaning"}, "", 0
        
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
        test_method_count = _count_test_methods(java_file_path)
        
        # === STEP 5: Setup logging ===
        log_path = LOG_DIR_BATCH / f"{custom_id}_{java_file}_compile.log"
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
        
        # === STEP 7: Build compile command ===
        # Skip PMD, CheckStyle, and Enforcer to avoid false failures
        test_class = java_file.replace(".java", "")
        fqn = f"{package_decl}.{test_class}"
        
        compile_cmd = (
            f"cd {project_root} && "
            f"javac -cp \"target/classes:target/test-classes:"
            f"$(mvn dependency:build-classpath -q -DincludeScope=test -Dmdep.outputFile=/dev/stdout 2>/dev/null)\" "
            f"-d target/test-classes "
            f"{test_root}/{pkg_path.as_posix()}/{java_file}"
        )
        
        # === STEP 8: Run Docker ===
        # Mount LLMTest folder to test_root (hides original tests)
        cmd = [
            "docker", "run",
            "--name", container_name,
            "--rm",  # Auto-cleanup on normal exit
            "--platform", "linux/amd64",
            "-v", f"{llm_test_dir}:{test_root}:ro",
            image_tag,
            "sh", "-c", compile_cmd
        ]
        
        # Print header to console
        safe_print(f"\n{'='*80}")
        safe_print(f"PHASE 1: COMPILATION - {java_file} for {custom_id}")
        safe_print(f"Container: {container_name}")
        safe_print(f"{'='*80}")
        safe_print(f"Image: {image_tag}")
        safe_print(f"Package: {package_decl}")
        safe_print(f"Test methods: {test_method_count}")
        safe_print(f"FQN: {fqn}")
        safe_print(f"Timeout: {COMPILE_TIMEOUT}s")
        safe_print(f"{'='*80}")
        
        log_lines = [
            "="*80,
            f"PHASE 1: COMPILATION - {java_file} for {custom_id}",
            f"Container: {container_name}",
            "="*80,
            f"Image: {image_tag}",
            f"Package: {package_decl}",
            f"Test methods: {test_method_count}",
            f"Project root: {project_root}",
            f"Test root: {test_root}",
            f"Timeout: {COMPILE_TIMEOUT}s",
            "="*80,
            f"Command: {compile_cmd}",
            "="*80,
            ""
        ]
        
        success = False
        err_info = None
        
        try:
            # Real compilation execution with timeout
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT)
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
            
            # Check for compilation errors
            has_compilation_error = (
                "COMPILATION ERROR" in combined or
                "error:" in combined.lower() and "javac" in combined or
                "cannot find symbol" in combined or
                "package" in combined and "does not exist" in combined
            )
            
            if has_compilation_error:
                log_lines.append("[RESULT] ✗ Compilation FAILED")
                safe_print("[RESULT] ✗ Compilation FAILED")
                success = False
            elif proc.returncode == 0:
                log_lines.append("[RESULT] ✓ Compilation SUCCESS")
                safe_print("[RESULT] ✓ Compilation SUCCESS")
                success = True
            else:
                log_lines.append(f"[RESULT] ✗ Compilation FAILED (return code: {proc.returncode})")
                safe_print(f"[RESULT] ✗ Compilation FAILED (return code: {proc.returncode})")
                success = False
                
        except subprocess.TimeoutExpired:
            # Kill stuck container on timeout
            log_lines.append(f"[ERROR] ✗ Timeout after {COMPILE_TIMEOUT}s - Force killing container")
            safe_print(f"[ERROR] ✗ Timeout after {COMPILE_TIMEOUT}s - Force killing container")
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=10)
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
                log_lines.append(f"[CLEANUP] ✓ Killed and removed stuck container: {container_name}")
            except Exception as cleanup_err:
                log_lines.append(f"[CLEANUP] ✗ Failed to kill container: {cleanup_err}")
            err_info = {"category": "timeout", "reason": f"Compilation timeout ({COMPILE_TIMEOUT}s)"}
            success = False
        except Exception as e:
            log_lines.append(f"[EXCEPTION] {e}")
            safe_print(f"[EXCEPTION] {e}")
            success = False
            err_info = {"category": "exception", "reason": str(e)}
        
        log_lines.append("="*80)
        log_text = "\n".join(log_lines)
        log_path.write_text(log_text, encoding="utf-8")
        
        if not success and err_info is None:
            err_info = classify_compilation_error(log_text)
        
        return success, err_info, str(log_path), test_method_count
        
    finally:
        # Cleanup temp directory only
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                safe_print(f"[WARN] Failed to remove temp dir {temp_dir}: {e}")


def process_compilation_task(args):
    """Wrapper for parallel compilation."""
    (image_tag, custom_id, test_root, java_file, package_decl, txt_path) = args
    
    success, err_info, log_path, test_count = compile_test_in_docker(
        image_tag, custom_id, test_root, java_file, package_decl, txt_path
    )
    
    return (custom_id, java_file, success, err_info, log_path, test_count)


def main():
    global compile_success_count, compile_failure_count, processed_instances

    # === BATCH CONFIGURATION ===
    START_ID = 1
    END_ID = 190

    safe_print(f"\n{'='*80}")
    safe_print(f"PHASE 1: COMPILATION ONLY (PRE Stage) - DYNAMIC WORKERS")
    safe_print(f"Features: Incremental saving, resume capability, full output")
    safe_print(f"Features: Minimal cleanup (only stuck containers)")
    safe_print(f"Compile Timeout: {COMPILE_TIMEOUT}s per file")
    safe_print(f"ID Range: {START_ID} to {END_ID}")
    safe_print(f"Memory Safe for 16GB RAM")
    safe_print(f"{'='*80}\n")

    # Cleanup only stuck/exited containers from previous runs
    cleanup_stale_containers()

    # === STEP 1: COUNT ALL GENERATED FILES (LIKE FIRST SCRIPT) ===
    # Load ALL bump IDs from CSV first
    all_bump_ids = []
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            if custom_id:
                all_bump_ids.append(custom_id)
    
    # Count ALL files in the output folder (not just START_ID to END_ID)
    all_generated_files, total_generated = count_all_generated_files(ABC_ROOT, all_bump_ids)
    
    # Initialize file_counts with the ACTUAL generated counts
    for custom_id, file_info in all_generated_files.items():
        file_counts[custom_id]["files_generated"] = file_info["files_generated"]
        # files_compiled will be updated as we process

    # === LOAD EXISTING RESULTS FOR RESUME ===
    if COMPILE_OUTPUT.exists():
        try:
            existing = json.loads(COMPILE_OUTPUT.read_text(encoding="utf-8"))
            # Load compilation results but DON'T overwrite files_generated
            compilation_results.update(existing.get("compilation_results", {}))
            
            # Only update files_compiled from existing results
            existing_file_counts = existing.get("file_counts", {})
            for cid, counts in existing_file_counts.items():
                if cid in file_counts:
                    file_counts[cid]["files_compiled"] = counts.get("files_compiled", 0)
            
            test_counts.update(defaultdict(lambda: {"tests_in_generated_files": 0, "tests_in_compiled_files": 0},
                                          existing.get("test_counts", {})))
            processed_instances.update(set(existing.get("processed_instances", [])))
            
            # Recalculate global counts from loaded data
            compile_success_count = sum(v["files_compiled"] for v in file_counts.values())
            compile_failure_count = sum(v["files_generated"] - v["files_compiled"] for v in file_counts.values())
            
            safe_print(f"[RESUME] Loaded existing results:")
            safe_print(f"  Processed instances: {len(processed_instances)}")
            safe_print(f"  Files compiled: {compile_success_count}")
            safe_print(f"  Files failed: {compile_failure_count}")
            safe_print(f"[RESUME] Will skip already processed instances\n")
        except Exception as e:
            safe_print(f"[WARN] Failed to load existing JSON: {e}\n")

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

                if not _abc_has_any_file(custom_id):
                    safe_print(f"[SKIP] {custom_id} - No files found")
                    continue

                test_root, real_package = pkg_info.get((custom_id, "pre"), (None, None))
                if not test_root or not real_package:
                    safe_print(f"[SKIP] {custom_id} - No test_root/package")
                    continue

                # Find all .txt files for this instance
                src_dir = ABC_ROOT / custom_id
                txt_files = list(src_dir.rglob("*.txt"))
                
                num_tests = len(txt_files)
                safe_print(f"\n{custom_id} | {commit[:8]}")
                safe_print(f"[INFO] Found {num_tests} test file(s)")

                if not txt_files:
                    # Mark as processed even if no files
                    processed_instances.add(custom_id)
                    save_results_incrementally()
                    continue

                # Count @Test methods in generated files (do this ONCE upfront)
                if test_counts[custom_id]["tests_in_generated_files"] == 0:
                    for txt_path in txt_files:
                        try:
                            raw = txt_path.read_text(encoding="utf-8", errors="ignore")
                            test_count = len(re.findall(r'@Test\b', raw))
                            test_counts[custom_id]["tests_in_generated_files"] += test_count
                        except:
                            pass

                # === DYNAMIC WORKER ALLOCATION ===
                workers = get_optimal_workers(num_tests, max_workers=8)
                safe_print(f"[WORKERS] Allocating {workers} workers for {num_tests} tests")

                # Prepare file info and count statistics
                java_files_info = []
                for txt_path in txt_files:
                    java_filename, _ = _to_java_filename(txt_path.name)
                    java_files_info.append((java_filename, txt_path))

                compiled_files = []
                failed_files = {}

                image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"

                # Prepare compilation tasks
                compile_tasks = []
                for java_file, txt_path in java_files_info:
                    task_args = (image_tag, custom_id, test_root, java_file, real_package, txt_path)
                    compile_tasks.append(task_args)

                safe_print(f"[INFO] Compiling {len(compile_tasks)} file(s) in parallel with {workers} workers...")

                # Execute compilations in parallel with DYNAMIC workers
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    future_to_task = {executor.submit(process_compilation_task, task): task for task in compile_tasks}

                    for future in as_completed(future_to_task):
                        task = future_to_task[future]
                        java_file = task[3]

                        try:
                            cid, jf, success, err_info, log_path, test_count = future.result()

                            with results_lock:
                                # FIXED: Correct logic
                                if not success:
                                    safe_print(f"  → {java_file}... ✗ FAILED")
                                    compile_failure_count += 1
                                    failed_files[java_file] = {
                                        "error_category": err_info.get("category", "") if err_info else "",
                                        "error_info": err_info,
                                        "test_method_count": test_count,
                                        "log_path": log_path
                                    }
                                else:
                                    safe_print(f"  → {java_file}... ✓ COMPILED")
                                    compile_success_count += 1
                                    compiled_files.append(java_file)
                                    file_counts[custom_id]["files_compiled"] += 1
                                    test_counts[custom_id]["tests_in_compiled_files"] += test_count

                        except Exception as exc:
                            with results_lock:
                                safe_print(f"  → {java_file}... ✗ EXCEPTION: {exc}")
                                compile_failure_count += 1
                                failed_files[java_file] = {
                                    "error_category": "exception",
                                    "error_info": {"message": str(exc)},
                                    "test_method_count": 0,
                                    "log_path": ""
                                }

                # Store results for this instance
                compilation_results[custom_id] = {
                    "workers_used": workers,
                    "compiled": compiled_files,
                    "failed": failed_files,
                    "file_counts": file_counts[custom_id],
                    "test_counts": test_counts[custom_id]
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
        # Cleanup stuck containers on interrupt
        safe_print(f"\n[CLEANUP] Cleaning up stuck containers...")
        cleanup_stale_containers()
        return

    finally:
        # === FINAL CLEANUP: Remove any orphaned temp directories ===
        safe_print(f"\n[CLEANUP] Checking for temporary directories...")
        temp_pattern = Path("/tmp")
        cleaned_count = 0
        for temp_dir in temp_pattern.glob("compile_*"):
            try:
                shutil.rmtree(temp_dir)
                cleaned_count += 1
            except:
                pass
        if cleaned_count > 0:
            safe_print(f"[CLEANUP] Removed {cleaned_count} temporary directories")
        
        # === FINAL CLEANUP: Remove stuck containers ===
        safe_print(f"[CLEANUP] Final container cleanup...")
        cleanup_stale_containers()
        safe_print(f"[CLEANUP] Done\n")

    # === FINAL SUMMARY ===
    total_files_generated = sum(v["files_generated"] for v in file_counts.values())
    total_files_compiled = sum(v["files_compiled"] for v in file_counts.values())
    total_tests_generated = sum(v["tests_in_generated_files"] for v in test_counts.values())
    total_tests_compiled = sum(v["tests_in_compiled_files"] for v in test_counts.values())

    safe_print(f"\n{'='*80}")
    safe_print(f"✓ PHASE 1 COMPLETE: COMPILATION WITH DYNAMIC WORKERS")
    safe_print(f"")
    safe_print(f"INSTANCES:")
    safe_print(f"  Total processed: {len(processed_instances)}")
    safe_print(f"")
    safe_print(f"CLASS TEST FILES:")
    safe_print(f"  Files Generated: {total_files_generated}")
    safe_print(f"  Files Compiled Successfully: {total_files_compiled}")
    safe_print(f"  Files Failed Compilation: {compile_failure_count}")
    safe_print(f"  Compilation Success Rate: {(total_files_compiled / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%")
    safe_print(f"")
    safe_print(f"@TEST METHODS:")
    safe_print(f"  @Test methods in generated files: {total_tests_generated}")
    safe_print(f"  @Test methods in compiled files: {total_tests_compiled}")
    safe_print(f"")
    safe_print(f"OUTPUT: {COMPILE_OUTPUT}")
    safe_print(f"{'='*80}\n")

if __name__ == "__main__":
    main()