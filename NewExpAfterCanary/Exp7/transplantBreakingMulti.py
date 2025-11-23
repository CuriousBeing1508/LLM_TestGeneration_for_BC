import os
import json
import re
import subprocess
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
BREAKING_OUTPUT = Path("/Volumes/Rachna-HD/Exp7BatchResults/breaking/transplant_results_final_breaking_multimodule.json")
MULTI_MODULE_LIST = Path("/Volumes/Rachna-HD/multi_module_instances.json")
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp7Batch/GPT4o")

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
                          first_module: str, first_module_package: str):
    """
    Run a single LLM test in the breaking Docker image.
    Creates test file directly inside the container - NO volume mounting!
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
    
    # Use first module's package for our test
    llm_package = f"{first_module_package}.LLMTest"
    final_code = _rewrite_package_and_class(cleaned, llm_package, class_base)
    fqn = f"{llm_package}.{test_class}"
    
    # Infer project root
    project_root = _infer_project_root(custom_id)
    
    # Target location inside container
    transplant_test_root = f"{project_root}/{first_module}/src/test/java"
    pkg_path_str = "/".join(llm_package.split("."))
    test_file_path = f"{transplant_test_root}/{pkg_path_str}/LLMTest/{java_file}"
    
    print(f"  [TRANSPLANT] {first_module} → {llm_package}")
    
    # Escape single quotes in code for shell
    escaped_code = final_code.replace("'", "'\\''")
    
    # Build shell command to create file and run tests inside container
    shell_cmd = f"""
set -e
# Create directory structure
mkdir -p {transplant_test_root}/{pkg_path_str}/LLMTest
# Write test file
cat > {test_file_path} << 'EOF'
{final_code}
EOF
# Run default command (which should run tests)
cd {project_root}
exec /bin/sh -c "$(cat /proc/1/cmdline | tr '\\0' ' ')" || mvn clean test
"""

    # Setup logging
    log_path = LOG_DIR_BATCH_BRE / f"{custom_id}_{java_file}_breaking_multi.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        image_tag,
        "sh", "-c", shell_cmd
    ]

    log_lines = [
        f"{'='*80}",
        f"Instance: {custom_id} | Test: {java_file}",
        f"{'='*80}",
        f"Configuration:",
        f"  Test Class: {test_class}",
        f"  FQN: {fqn}",
        f"  First Module: {first_module}",
        f"  First Module Package: {first_module_package}",
        f"  LLM Package: {llm_package}",
        f"{'='*80}",
        f"Paths:",
        f"  Project Root: {project_root}",
        f"  Test Location (in container): {test_file_path}",
        f"{'='*80}",
        f"Docker: {image_tag}",
        f"{'='*80}",
        f"",
    ]
    
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        stdout = proc.stdout
        stderr = proc.stderr
        combined = stdout + "\n" + stderr
        
        # Extract only our test's output
        our_test_lines = []
        in_our_test = False
        capture_lines = 0
        max_capture_lines = 20
        
        for line in combined.split("\n"):
            if f"Running {fqn}" in line or f"Running {test_class}" in line:
                in_our_test = True
                our_test_lines = [line]
                capture_lines = 0
            elif in_our_test:
                our_test_lines.append(line)
                capture_lines += 1
                
                if re.search(r"Tests run:\s*\d+.*?Failures:\s*\d+.*?Errors:\s*\d+", line):
                    max_capture_lines = min(5, max_capture_lines)
                
                if capture_lines >= max_capture_lines:
                    break
        
        # Log our test's output
        if our_test_lines:
            log_lines.append("=== OUR TEST OUTPUT (EXTRACTED) ===")
            log_lines.extend(our_test_lines)
            log_lines.append("")
            log_lines.append(f"[INFO] Extracted {len(our_test_lines)} lines")
        else:
            log_lines.append("=== OUR TEST NOT FOUND IN OUTPUT ===")
            log_lines.append(f"[ERROR] Test '{test_class}' not found in output")
            log_lines.append("")
            log_lines.append("Last 150 lines:")
            log_lines.append("")
            all_lines = combined.split("\n")
            log_lines.extend(all_lines[-150:])
        
        log_lines.append("")
        log_lines.append(f"=== EXIT CODE: {proc.returncode} ===")
        log_lines.append("")
        
        # Parse results
        test_executed = False
        success = False
        
        if f"Running {fqn}" in combined or f"Running {test_class}" in combined:
            test_executed = True
            log_lines.append(f"[✓] Test executed: {test_class}")
            
            # Parse results
            pattern = rf"Running.*?{test_class}.*?Tests run:\s*(\d+).*?Failures:\s*(\d+).*?Errors:\s*(\d+)"
            match = re.search(pattern, combined, flags=re.DOTALL | re.IGNORECASE)
            
            if match:
                tests_run = int(match.group(1))
                failures = int(match.group(2))
                errors = int(match.group(3))
                
                log_lines.append("")
                log_lines.append(f"[RESULTS]")
                log_lines.append(f"  Tests Run:  {tests_run}")
                log_lines.append(f"  Failures:   {failures}")
                log_lines.append(f"  Errors:     {errors}")
                log_lines.append("")
                
                if tests_run > 0:
                    success = (failures == 0 and errors == 0)
                    if success:
                        log_lines.append(f"[✓] PASS - No breaking change detected")
                    else:
                        log_lines.append(f"[✗] FAIL - Breaking change detected!")
                        if failures > 0:
                            log_lines.append(f"     → {failures} assertion failure(s)")
                        if errors > 0:
                            log_lines.append(f"     → {errors} error(s)")
                else:
                    log_lines.append(f"[?] Test found but 0 tests ran")
                    success = False
            else:
                log_lines.append(f"[?] Could not parse test results")
                success = False
        else:
            # Test did not execute
            log_lines.append(f"[✗] Test did not execute")
            log_lines.append("")
            
            # Diagnose
            if "COMPILATION ERROR" in combined or "cannot find symbol" in combined:
                log_lines.append("[DIAGNOSIS] Compilation error")
                for line in combined.split("\n"):
                    if "error:" in line.lower() and ".java" in line:
                        log_lines.append(f"  {line.strip()}")
                        break
            elif "BUILD FAILURE" in combined:
                log_lines.append("[DIAGNOSIS] Build failure")
                if "Could not resolve dependencies" in combined:
                    log_lines.append("  → Dependency resolution failed")
                elif "Failed to execute goal" in combined:
                    goal_match = re.search(r"Failed to execute goal.*?on project ([\w\-]+)", combined)
                    if goal_match:
                        log_lines.append(f"  → Failed on project: {goal_match.group(1)}")
            elif "No tests were executed" in combined or "No tests found" in combined:
                log_lines.append("[DIAGNOSIS] Test not found")
                log_lines.append(f"  Expected: {fqn}")
            else:
                log_lines.append("[DIAGNOSIS] Unknown")
            
            success = False
        
        log_lines.append("")
            
    except subprocess.TimeoutExpired:
        log_lines.append("[ERROR] Timeout (600s)")
        success = False
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        import traceback
        log_lines.append(traceback.format_exc())
        success = False

    # Save log
    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    
    # Classify error
    err_info = classify_compilation_error(log_text) if not success else None
    
    return success, err_info, str(log_path)


def main():
    global success_count, failure_count, results, carry_forward_instances, carry_forward_tests, multi_module_info, csv_data

    print(f"\n{'='*80}")
    print(f"BREAKING STAGE - Multi-Module Projects")
    print(f"Source: {MULTI_MODULE_LIST}")
    print(f"{'='*80}\n")
    
    # Load multi-module info
    multi_module_info = _load_multi_module_info()
    if not multi_module_info:
        print("[ERROR] No multi-module instances")
        return
    
    # Load CSV for commits
    csv_data = _load_csv_data()
    if not csv_data:
        print("[ERROR] Failed to load CSV")
        return
    
    # Load pre results
    pre_data = json.loads(Path(PRE_RESULTS_PATH).read_text(encoding="utf-8"))
    carry_forward_tests.update(pre_data.get("carry_forward_tests", {}))
    carry_forward_instances.update(pre_data.get("carry_forward_instances", []))
    print(f"[INFO] Loaded {len(carry_forward_instances)} instances from pre-stage\n")

    # Resume if exists
    if BREAKING_OUTPUT.exists():
        try:
            existing = json.loads(BREAKING_OUTPUT.read_text(encoding="utf-8"))
            results = existing.get("results", {})
            success_count = existing.get("summary", {}).get("total_pass", 0)
            failure_count = existing.get("summary", {}).get("total_fail", 0)
            print(f"[INFO] Resuming: Pass={success_count}, Fail={failure_count}\n")
        except Exception as e:
            print(f"[WARN] Resume failed: {e}\n")

    LOG_DIR_BATCH_BRE.mkdir(parents=True, exist_ok=True)
    
    processed_count = 0
    skipped_count = 0

    # Process each multi-module instance
    for custom_id in sorted(multi_module_info.keys()):
        # Get commit from CSV
        if custom_id not in csv_data:
            print(f"\n{custom_id} - [SKIP] Not in CSV")
            skipped_count += 1
            continue
        
        commit = csv_data[custom_id].get("breakingCommit", "").strip()
        if not commit:
            print(f"\n{custom_id} - [SKIP] No commit")
            skipped_count += 1
            continue

        first_module = multi_module_info[custom_id]["first_module"]
        first_module_pkg = multi_module_info[custom_id]["first_module_package"]
        
        print(f"\n{'='*80}")
        print(f"{custom_id}")
        print(f"  Module: {first_module}")
        print(f"  Package: {first_module_pkg}")
        print(f"  Commit: {commit[:12]}...")
        print(f"{'='*80}")

        if custom_id not in carry_forward_instances:
            print(f"[SKIP] Not in carry_forward")
            skipped_count += 1
            continue
            
        if not _abc_has_any_file(custom_id):
            print(f"[SKIP] No files")
            skipped_count += 1
            continue

        passed_tests = carry_forward_tests[custom_id]["passed"]
        if not passed_tests:
            print(f"[SKIP] No passing tests")
            skipped_count += 1
            continue

        image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
        per_test_status = {"passed": [], "failed": []}

        print(f"[INFO] Testing {len(passed_tests)} file(s)")
        
        for java_file in passed_tests:
            print(f"  → {java_file}...", end=" ", flush=True)
            
            success, err_info, log_path = run_test_in_isolation(
                image_tag, custom_id, java_file, first_module, first_module_pkg
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
            "first_module": first_module,
            "first_module_package": first_module_pkg
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
                "total_multi_module": len(multi_module_info)
            }
        }
        BREAKING_OUTPUT.write_text(json.dumps(output_data, indent=2), encoding="utf-8")

        print(f"[SAVE] {processed_count}/{len(multi_module_info)} | Pass: {success_count}, Fail: {failure_count}")

    print(f"\n{'='*80}")
    print(f"✓ COMPLETE")
    print(f"{'='*80}")
    print(f"Total: {len(multi_module_info)}")
    print(f"Processed: {processed_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Pass: {success_count}")
    print(f"Fail: {failure_count}")
    if success_count + failure_count > 0:
        print(f"Rate: {success_count / (success_count + failure_count) * 100:.1f}%")
    print(f"Output: {BREAKING_OUTPUT}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()