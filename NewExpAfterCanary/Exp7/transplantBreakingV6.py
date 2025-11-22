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

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
PRE_RESULTS_PATH = "/Volumes/Rachna-HD/Exp7BatchResults/pre/transplant_results_final_pre.json"
BREAKING_OUTPUT = Path("/Volumes/Rachna-HD/Exp7BatchResults/breaking/transplant_results_final_breaking.json")
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp7Batch/GPT4o")
MODEL_NAME = ABC_ROOT.name  # e.g., "GPT4o"

SCRATCH_BASE = Path("/Volumes/Rachna-HD/Exp6BatchResults/breaking/scratch")
REPORTS_BASE = Path("/Volumes/Rachna-HD/Exp6BatchResults/breaking/reports")

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

success_count = 0
failure_count = 0
failure_categories = Counter()

carry_forward_instances = set()
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})


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


def _determine_test_location(custom_id: str, stage: str, pkg_info: dict) -> tuple[str, str, str]:
    """
    Determine test root and package for a given instance.
    Returns: (test_root, llm_package, strategy)
    """
    
    # Try to get from pkg_info directly
    test_root, package = pkg_info.get((custom_id, stage), (None, None))
    
    if test_root and package:
        print(f"[INFO] Found: test_root={test_root}, package={package}")
        llm_package = f"{package}.LLMTest"
        return test_root, llm_package, "with_test_root"
    
    print(f"[WARN] Missing test_root/package for ({custom_id}, {stage})")
    
    # Try to find package from any stage for this custom_id
    if not package:
        for key, value in pkg_info.items():
            if key[0] == custom_id:
                _, potential_package = value
                if potential_package:
                    package = potential_package
                    print(f"[INFO] Found package from stage '{key[1]}': {package}")
                    break
    
    if not package:
        package = "llmtest"
        print(f"[WARN] No package found, using generic: {package}")
    
    # Collect test root samples from the same stage
    test_root_samples = [v[0] for k, v in pkg_info.items() if k[1] == stage and v[0]]
    
    if test_root_samples:
        print(f"[INFO] Analyzing {len(test_root_samples)} test root samples")
        
        # Strategy 1: Find a sample with matching package prefix
        package_prefix = package.split(".")[0] if "." in package else package
        for sample in test_root_samples:
            if package_prefix in sample or package in sample:
                print(f"[INFO] Found matching sample: {sample}")
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
                    print(f"[INFO] Inferred from pattern: {inferred_test_root}")
                    llm_package = f"{package}.LLMTest"
                    return inferred_test_root, llm_package, "inferred_test_root"
        
        # Strategy 3: Use first sample
        print(f"[WARN] Using first sample: {test_root_samples[0]}")
        inferred_test_root = test_root_samples[0]
    else:
        # No samples - generic fallback
        print(f"[WARN] No samples, using generic fallback")
        inferred_test_root = "/workspace/src/test/java"
    
    llm_package = f"{package}.LLMTest"
    print(f"[INFO] Final: test_root={inferred_test_root}, package={llm_package}")
    
    return inferred_test_root, llm_package, "inferred_test_root"


def run_test_in_isolation(image_tag: str, custom_id: str, test_root: str,
                          package_decl: str, java_file: str, strategy: str):
    """
    Run a single LLM test in the breaking Docker image.
    Global solution that works for all scenarios.
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
        return False, None, ""
    
    cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
    if not cleaned:
        print(f"[ERROR] Empty after cleaning")
        return False, None, ""
    
    _, class_base = _to_java_filename(txt_path.name)
    final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)

    # Create package structure
    pkg_path = Path(*package_decl.split("."))
    out_file = scratch_dir / pkg_path / java_file
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(final_code, encoding="utf-8")

    # Setup logging
    log_path = LOG_DIR_BATCH_BRE / f"{custom_id}_{java_file}_breaking_exec.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    test_class = java_file.replace(".java", "")
    fqn = f"{package_decl}.{test_class}"
    
    # === Detect project structure ===
    module_name = None
    project_root = None
    test_root_parts = test_root.strip("/").split("/")
    
    if "src" in test_root_parts:
        src_index = test_root_parts.index("src")
        if src_index > 1:
            # Multi-module: /project/module/src/test/java
            module_name = test_root_parts[src_index - 1]
            project_root = "/" + "/".join(test_root_parts[:src_index - 1])
        elif src_index == 1:
            # Single module: /project/src/test/java
            project_root = "/" + test_root_parts[0]
        else:
            project_root = "/"
    
    # === Build Docker command ===
    
    if module_name and project_root and project_root != "/":
        # Multi-module project
        maven_cmd = (
            f"cd {project_root} && "
            f"mvn test-compile -pl {module_name} -am -q && "
            f"mvn surefire:test -pl {module_name} -Dtest={fqn} -DfailIfNoTests=false"
        )
        cmd = [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{scratch_dir}:{test_root}:ro",
            image_tag,
            "sh", "-c", maven_cmd
        ]
        project_type = f"multi-module (module: {module_name})"
    
    elif project_root and project_root != "/":
        # Single module project
        maven_cmd = (
            f"cd {project_root} && "
            f"mvn test-compile -q && "
            f"mvn surefire:test -Dtest={fqn} -DfailIfNoTests=false"
        )
        cmd = [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{scratch_dir}:{test_root}:ro",
            image_tag,
            "sh", "-c", maven_cmd
        ]
        project_type = "single-module"
    
    else:
        # Fallback: use default
        cmd = [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{scratch_dir}:{test_root}:ro",
            image_tag,
        ]
        project_type = "default (no custom maven cmd)"

    log_lines = [
        f"{'='*80}",
        f"Instance: {custom_id} | Test: {java_file}",
        f"Strategy: {strategy}",
        f"{'='*80}",
        f"Configuration:",
        f"  Package: {package_decl}",
        f"  FQN: {fqn}",
        f"  Test Class: {test_class}",
        f"  Test Root: {test_root}",
        f"  Project Type: {project_type}",
        f"  Project Root: {project_root or 'N/A'}",
        f"  Module: {module_name or 'N/A'}",
        f"  Image: {image_tag}",
        f"{'='*80}",
        f"File Structure:",
        f"  Mac scratch: {scratch_dir}",
        f"  Mac file: {out_file}",
        f"  Relative: {pkg_path}/{java_file}",
        f"  Container: {test_root}/{pkg_path}/{java_file}",
        f"{'='*80}",
        f"Docker Command:",
    ]
    
    for i, part in enumerate(cmd):
        if i < 8:
            log_lines.append(f"  [{i}] {part}")
        else:
            log_lines.append(f"  Maven: {part}")
    
    log_lines.append(f"{'='*80}")
    log_lines.append("")
    
    try:
        print(f"[INFO] Executing ({project_type})...")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        log_lines.append("=== STDOUT ===")
        log_lines.append(proc.stdout)
        log_lines.append("")
        log_lines.append("=== STDERR ===")
        log_lines.append(proc.stderr)
        log_lines.append("")
        log_lines.append(f"=== EXIT CODE: {proc.returncode} ===")
        log_lines.append("")
        
        # === Parse output ===
        stdout = proc.stdout
        stderr = proc.stderr
        combined = stdout + "\n" + stderr
        
        # Look for test execution
        test_ran = False
        
        if f"Running {fqn}" in combined or f"Running {test_class}" in combined:
            test_ran = True
            log_lines.append(f"[INFO] ✓ Found: 'Running {test_class}'")
        
        if test_ran:
            # Parse results
            pattern = rf"Running.*?{test_class}.*?Tests run:\s*(\d+).*?Failures:\s*(\d+).*?Errors:\s*(\d+)"
            m = re.search(pattern, combined, flags=re.DOTALL | re.IGNORECASE)
            
            if m:
                tests_run = int(m.group(1))
                failures = int(m.group(2))
                errors = int(m.group(3))
                
                log_lines.append(f"[RESULTS] Tests: {tests_run}, Failures: {failures}, Errors: {errors}")
                
                if tests_run > 0:
                    success = (failures == 0 and errors == 0)
                    if success:
                        log_lines.append(f"[SUCCESS] ✓✓✓ {test_class} PASSED ✓✓✓")
                    else:
                        log_lines.append(f"[FAILURE] ✗✗✗ {test_class} FAILED ✗✗✗")
                        if failures > 0:
                            log_lines.append(f"  - {failures} assertion failure(s)")
                        if errors > 0:
                            log_lines.append(f"  - {errors} error(s)")
                else:
                    log_lines.append("[ERROR] 0 tests ran")
                    success = False
            else:
                # Simpler pattern
                log_lines.append("[WARN] Could not parse with detailed pattern")
                simple_pattern = rf"{test_class}.*?Tests run:\s*(\d+)"
                sm = re.search(simple_pattern, combined, flags=re.DOTALL)
                if sm and int(sm.group(1)) > 0:
                    success = ("Failures: 0" in combined and "Errors: 0" in combined)
                    log_lines.append(f"[INFO] Simple parse: {'PASS' if success else 'FAIL'}")
                else:
                    log_lines.append("[ERROR] Cannot parse results")
                    success = False
        else:
            # Test didn't run
            log_lines.append(f"[ERROR] ✗ Test '{test_class}' NOT FOUND")
            log_lines.append(f"[INFO] Searched for: '{fqn}' and '{test_class}'")
            
            # Diagnose
            if "BUILD FAILURE" in combined:
                log_lines.append("[ERROR] Build failed")
                if "COMPILATION ERROR" in combined or "cannot find symbol" in combined:
                    log_lines.append("[ERROR] Compilation error")
                if module_name and "SKIPPED" in combined:
                    log_lines.append(f"[ERROR] Module '{module_name}' skipped")
            elif "No tests were executed" in combined or "No tests found" in combined:
                log_lines.append("[ERROR] Maven couldn't find test")
                log_lines.append(f"[CHECK] Package: {package_decl}")
                log_lines.append(f"[CHECK] Location: {test_root}/{pkg_path}/{java_file}")
            elif "Tests run:" in combined:
                log_lines.append("[WARN] Other tests ran, not ours")
                ran_tests = re.findall(r"Running ([\w\.]+)", combined)
                if ran_tests:
                    log_lines.append(f"[INFO] Tests that ran: {', '.join(ran_tests[:5])}")
            else:
                log_lines.append("[ERROR] Unknown - check output")
            
            success = False
            
    except subprocess.TimeoutExpired:
        log_lines.append("[ERROR] ⏱ Timed out (600s)")
        success = False
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        import traceback
        log_lines.append(traceback.format_exc())
        success = False

    # Save log
    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    
    err_info = classify_compilation_error(log_text)
    
    return success, err_info, str(log_path)


def main():
    global success_count, failure_count, results, carry_forward_instances, carry_forward_tests

    START_ID = 106
    END_ID = 190

    print(f"\n{'='*80}")
    print(f"Breaking Stage - LLM Test Execution")
    print(f"ID Range: {START_ID} to {END_ID}")
    print(f"{'='*80}\n")
    
    # Load pre results
    pre_data = json.loads(Path(PRE_RESULTS_PATH).read_text(encoding="utf-8"))
    carry_forward_tests.update(pre_data.get("carry_forward_tests", {}))
    carry_forward_instances.update(pre_data.get("carry_forward_instances", []))
    print(f"[INFO] Loaded {len(carry_forward_instances)} instances with passing tests\n")

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

    # Create directories
    SCRATCH_BASE.mkdir(parents=True, exist_ok=True)
    LOG_DIR_BATCH_BRE.mkdir(parents=True, exist_ok=True)
    
    processed_count = 0
    skipped_count = 0
    strategy_counts = Counter()

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

            print(f"\n{'='*80}")
            print(f"{custom_id} | Commit: {commit[:12]}...")
            print(f"{'='*80}")

            if custom_id not in carry_forward_instances:
                print(f"[SKIP] Not in carry_forward")
                skipped_count += 1
                continue
                
            if not _abc_has_any_file(custom_id):
                print(f"[SKIP] No ABC files")
                skipped_count += 1
                continue

            passed_tests = carry_forward_tests[custom_id]["passed"]
            if not passed_tests:
                print(f"[SKIP] No passing tests")
                skipped_count += 1
                continue

            # Get configuration
            test_root, package_decl, strategy = _determine_test_location(custom_id, "breaking", pkg_info)
            strategy_counts[strategy] += 1
            
            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
            per_test_status = {"passed": [], "failed": []}

            print(f"[INFO] Testing {len(passed_tests)} file(s)")
            
            for java_file in passed_tests:
                print(f"  → {java_file}...", end=" ", flush=True)
                
                success, err_info, log_path = run_test_in_isolation(
                    image_tag, custom_id, test_root, package_decl, java_file, strategy
                )
                
                if success:
                    print(f"✓ PASSED")
                    success_count += 1
                    per_test_status["passed"].append(java_file)
                else:
                    print(f"✗ FAILED")
                    failure_count += 1
                    per_test_status["failed"].append(java_file)

            results[custom_id] = {
                "tests": per_test_status,
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
                    "strategies": dict(strategy_counts)
                },
                "carry_forward_instances": list(carry_forward_instances),
                "carry_forward_tests": dict(carry_forward_tests)
            }
            BREAKING_OUTPUT.write_text(json.dumps(output_data, indent=2), encoding="utf-8")

            print(f"[SAVE] {processed_count}/{processed_count+skipped_count} | Pass: {success_count}, Fail: {failure_count}")

    print(f"\n{'='*80}")
    print(f"✓ EXECUTION COMPLETE")
    print(f"{'='*80}")
    print(f"Processed: {processed_count} instances")
    print(f"Skipped: {skipped_count} instances")
    print(f"Tests Passed: {success_count}")
    print(f"Tests Failed: {failure_count}")
    print(f"Success Rate: {success_count / (success_count + failure_count) * 100:.1f}%" if (success_count + failure_count) > 0 else "N/A")
    print(f"Strategies Used: {dict(strategy_counts)}")
    print(f"Output File: {BREAKING_OUTPUT}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()