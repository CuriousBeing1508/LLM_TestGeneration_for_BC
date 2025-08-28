import os
import csv
import json
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
LLM_BASE = Path("/Volumes/Rachna-HD/Dataset/LLMOutputClient/GPT4o")

OUT_DIR = Path("/Volumes/Rachna-HD/CanaryResultsWithActualTests")
OUT_DIR.mkdir(parents=True, exist_ok=True)

RESULT_JSON = OUT_DIR / "transplant_llm_results.json"
RESULT_CSV  = OUT_DIR / "transplant_llm_summary.csv"

# Where to collect Surefire reports and logs copied out of containers
SUREFIRE_OUT = OUT_DIR / "surefire_reports"
LOG_DIR      = OUT_DIR / "logs"
SUREFIRE_OUT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ========== Utility ==========
def clean_llm_code(lines):
    """Extract code inside ```java ... ``` blocks."""
    in_code = False
    code_lines = []

    for line in lines:
        if line.strip().startswith("```java"):
            in_code = True
            continue
        elif line.strip().startswith("```") and in_code:
            break
        if in_code:
            code_lines.append(line)
    return code_lines

def parse_package_summary(path):
    """
    Same structure as your scripts: returns {(custom_id, phase): (test_root, package_path)}
    """
    info = {}
    current_id = current_type = None
    test_root = package = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("===="):
                parts = line.split(" | ")
                if len(parts) >= 2:
                    current_id = parts[0].replace("====", "").strip()
                    current_type = parts[1].strip()
                    test_root = package = None
            elif line.startswith("Test root:"):
                test_root = line.split("Test root:")[1].strip()
            elif line.startswith("package:"):
                package = line.split("package:")[1].strip()
            if current_id and current_type and test_root and package:
                info[(current_id, current_type)] = (test_root, package)
                current_id = current_type = test_root = package = None
    return info

def write_log(custom_id, suffix, content):
    p = LOG_DIR / f"{custom_id}_{suffix}.log"
    p.write_text(content, encoding="utf-8", errors="ignore")
    return str(p)

def parse_surefire_dir(dir_path: Path):
    """
    Parse surefire XMLs and return:
      - per_class: {ClassName: {"tests": int, "failures": int, "errors": int, "skipped": int, "passed": int}}
      - totals:    {"tests":..., "failures":..., "errors":..., "skipped":..., "passed":...}
    """
    per_class = {}
    totals = Counter()
    if not dir_path.exists():
        return {}, {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "passed": 0}

    for xml_file in dir_path.glob("*.xml"):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            # surefire may use either <testsuite> root or <testsuite> children
            suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
            for ts in suites:
                cls = ts.attrib.get("name") or xml_file.stem
                tests = int(ts.attrib.get("tests", 0))
                failures = int(ts.attrib.get("failures", 0))
                errors = int(ts.attrib.get("errors", 0))
                skipped = int(ts.attrib.get("skipped", 0))
                passed = max(tests - failures - errors - skipped, 0)
                per_class[cls] = {"tests": tests, "failures": failures, "errors": errors, "skipped": skipped, "passed": passed}
                totals.update({"tests": tests, "failures": failures, "errors": errors, "skipped": skipped, "passed": passed})
        except Exception:
            # ignore malformed xml but keep going
            continue
    # make sure totals dict has all keys
    for k in ["tests", "failures", "errors", "skipped", "passed"]:
        totals.setdefault(k, 0)
    return per_class, dict(totals)

def docker_run_with_transplant(image_tag, custom_id, phase, test_root, package_path, test_files_dir, class_names):
    """
    Transplant tests and run:
      - copy tests to {test_root}/{package_path.replace('.', '/')}/{custom_id}/
      - compile with mvn test-compile
      - run surefire:test for only the selected classes
      - always attempt to copy surefire reports out
    Returns dict with:
      - "compiled": True/False
      - "surefire_dir": Path or None
      - "log_path": str
      - "per_class": stats dict
      - "totals": totals dict
    """
    container_name = f"{custom_id.lower()}_{phase}_container"
    mount_target = "/llm_tests"
    out_target = f"/out_surefire/{custom_id}_{phase}"
    surefire_out_dir = SUREFIRE_OUT / f"{custom_id}_{phase}"
    # clean output dir for this run
    shutil.rmtree(surefire_out_dir, ignore_errors=True)
    surefire_out_dir.mkdir(parents=True, exist_ok=True)

    transplant_path = f"{test_root}/{package_path.replace('.', '/')}/{custom_id}"
    # Build -Dtest value with fully-qualified classes
    fq_classes = [f"{package_path}.{custom_id}.{cls.strip()}" for cls in class_names]
    dtest = ",".join(fq_classes)

    shell_lines = []
    shell_lines.append(f'mkdir -p "{transplant_path}"')
    # copy over each .java from mounted /llm_tests
    for cls in class_names:
        shell_lines.append(f'cp "{mount_target}/{cls}.java" "{transplant_path}/{cls}.java"')

    shell_lines.append(f'cd "{test_root}/../../.."')
    # compile
    # If compile fails, exit with code 11 and print marker so we can detect
    shell_lines.append('mvn -o -q test-compile || { echo "__COMPILE_FAILED__"; exit 11; }')
    # run tests (do not stop pipeline on failures), remember exit code, and always copy reports out
    if dtest:
        shell_lines.append(f'mvn -o -q surefire:test -Dtest="{dtest}"; TEST_EXIT=$?')
    else:
        shell_lines.append('echo "No tests provided"; TEST_EXIT=2')
    shell_lines.append(f'mkdir -p "{out_target}"')
    shell_lines.append(f'cp -r target/surefire-reports/* "{out_target}/" 2>/dev/null || true')
    shell_lines.append('echo "__TEST_EXIT__=${TEST_EXIT}"')
    # end with success so docker returns 0 unless compile failed
    cmd = [
        "docker", "run", "--rm",
        "--platform", "linux/amd64",
        "--name", container_name,
        "-v", f"{test_files_dir}:{mount_target}",
        "-v", f"{SUREFIRE_OUT.absolute()}:/out_surefire",
        "-v", f"{os.path.expanduser('~')}/.m2:/root/.m2",
        image_tag,
        "sh", "-c", " && ".join(shell_lines)
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        combined = (proc.stdout or "") + "\n\n" + (proc.stderr or "")
    except Exception as e:
        log_path = write_log(custom_id, phase, f"[EXCEPTION] {e}")
        return {"compiled": False, "surefire_dir": None, "log_path": log_path, "per_class": {}, "totals": {"tests":0,"failures":0,"errors":0,"skipped":0,"passed":0}}

    log_path = write_log(custom_id, phase, combined)

    # detect compile failure via marker or exit code 11 (our explicit exit)
    compiled = "__COMPILE_FAILED__" not in combined

    per_class, totals = ({}, {"tests":0,"failures":0,"errors":0,"skipped":0,"passed":0})
    if compiled:
        per_class, totals = parse_surefire_dir(surefire_out_dir)

    return {
        "compiled": compiled,
        "surefire_dir": surefire_out_dir if compiled else None,
        "log_path": log_path,
        "per_class": per_class,
        "totals": totals
    }

# ========== Main ==========
def main():
    pkg_info = parse_package_summary(SUMMARY_PATH)

    overall = {
        "pre_compile_success": 0,
        "pre_compile_failure": 0,
        "pre_tests_pass": 0,
        "pre_tests_fail": 0,
        "breaking_tests_pass": 0,
        "breaking_tests_fail": 0,
        "breaking_changes": 0
    }

    csv_rows = []
    results = {}

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            if not commit:
                continue

            results[custom_id] = {}

            # Locate LLM tests for this custom_id
            llm_dir = LLM_BASE / custom_id
            if not llm_dir.exists():
                results[custom_id]["status"] = "llm_dir_missing"
                csv_rows.append({"custom_id": custom_id, "phase":"pre", "compiled":"no", "tests_pass":0, "tests_fail":0, "breaks":0, "note":"llm_dir_missing"})
                continue

            # class name = file name (strip _prompt)
            prompt_files = sorted(llm_dir.glob("*_prompt.txt"))
            class_names = [p.stem.replace("_prompt", "") for p in prompt_files]
            if not class_names:
                results[custom_id]["status"] = "no_llm_tests"
                csv_rows.append({"custom_id": custom_id, "phase":"pre", "compiled":"no", "tests_pass":0, "tests_fail":0, "breaks":0, "note":"no_llm_tests"})
                continue

            # Prepare temp dir with cleaned Java for PRE (package = package_path.<custom_id>)
            pre_test_root, pre_package = pkg_info.get((custom_id, "pre"), (None, None))
            if not (pre_test_root and pre_package):
                results[custom_id]["status"] = "missing_package_info_pre"
                csv_rows.append({"custom_id": custom_id, "phase":"pre", "compiled":"no", "tests_pass":0, "tests_fail":0, "breaks":0, "note":"missing_package_info_pre"})
                continue

            temp_dir_pre = Path(f"/tmp/llm_tests/{custom_id}/pre")
            shutil.rmtree(temp_dir_pre, ignore_errors=True)
            temp_dir_pre.mkdir(parents=True, exist_ok=True)

            for cls in class_names:
                txt_file = llm_dir / f"{cls}_prompt.txt"
                if txt_file.exists():
                    lines = txt_file.read_text(encoding="utf-8", errors="ignore").splitlines(True)
                    cleaned = clean_llm_code(lines)
                    java_code = f"package {pre_package}.{custom_id};\n\n{''.join(cleaned)}"
                    (temp_dir_pre / f"{cls}.java").write_text(java_code, encoding="utf-8")

            # --- Run PRE image ---
            pre_image = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
            pre_run = docker_run_with_transplant(
                pre_image, custom_id, "pre",
                pre_test_root, pre_package,
                temp_dir_pre, class_names
            )

            results[custom_id]["pre"] = {
                "compiled": pre_run["compiled"],
                "log_path": pre_run["log_path"],
                "surefire_dir": str(pre_run["surefire_dir"]) if pre_run["surefire_dir"] else None,
                "per_class": pre_run["per_class"],
                "totals": pre_run["totals"]
            }

            if pre_run["compiled"]:
                overall["pre_compile_success"] += 1
                overall["pre_tests_pass"] += pre_run["totals"]["passed"]
                overall["pre_tests_fail"] += (pre_run["totals"]["failures"] + pre_run["totals"]["errors"])
            else:
                overall["pre_compile_failure"] += 1
                # can't run breaking without pre-pass classes
                csv_rows.append({"custom_id": custom_id, "phase":"pre", "compiled":"no", "tests_pass":0, "tests_fail":0, "breaks":0, "note":"compile_failed"})
                continue

            # Determine classes that fully passed on PRE (no failures/errors for that class)
            pre_passing_classes = []
            for cls, stat in pre_run["per_class"].items():
                # stat key 'name' could be FQCN; we expect Surefire <testsuite name="pkg.ClassName">
                simple = cls.split(".")[-1]
                if stat["failures"] == 0 and stat["errors"] == 0:
                    pre_passing_classes.append(simple)

            # If none passed, nothing to transplant to breaking
            if not pre_passing_classes:
                results[custom_id]["breaking"] = {
                    "transplanted": [],
                    "note": "no_pre_passing_classes"
                }
                csv_rows.append({"custom_id": custom_id, "phase":"pre", "compiled":"yes",
                                 "tests_pass": pre_run["totals"]["passed"],
                                 "tests_fail": pre_run["totals"]["failures"]+pre_run["totals"]["errors"],
                                 "breaks": 0, "note":"no_pre_passing_classes"})
                continue

            # Prepare BREAKING temp dir with only the pre-passing classes
            temp_dir_break = Path(f"/tmp/llm_tests/{custom_id}/breaking")
            shutil.rmtree(temp_dir_break, ignore_errors=True)
            temp_dir_break.mkdir(parents=True, exist_ok=True)
            for cls in pre_passing_classes:
                src = temp_dir_pre / f"{cls}.java"
                if src.exists():
                    shutil.copy(src, temp_dir_break / f"{cls}.java")

            # Package & root for breaking
            breaking_test_root, breaking_package = pkg_info.get((custom_id, "breaking"), (None, None))
            if not (breaking_test_root and breaking_package):
                results[custom_id]["breaking"] = {"transplanted": pre_passing_classes, "status": "missing_package_info_breaking"}
                csv_rows.append({"custom_id": custom_id, "phase":"breaking", "compiled":"no", "tests_pass":0, "tests_fail":0, "breaks":0, "note":"missing_package_info_breaking"})
                continue

            # --- Run BREAKING image only with the pre-passing classes ---
            breaking_image = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
            breaking_run = docker_run_with_transplant(
                breaking_image, custom_id, "breaking",
                breaking_test_root, breaking_package,
                temp_dir_break, pre_passing_classes
            )

            results[custom_id]["breaking"] = {
                "compiled": breaking_run["compiled"],
                "log_path": breaking_run["log_path"],
                "surefire_dir": str(breaking_run["surefire_dir"]) if breaking_run["surefire_dir"] else None,
                "per_class": breaking_run["per_class"],
                "totals": breaking_run["totals"],
                "transplanted": pre_passing_classes
            }

            # Count breaking changes: class passed on pre but has any failure/error on breaking
            breaking_fail_classes = set()
            for cls in pre_passing_classes:
                fq = f"{breaking_package}.{custom_id}.{cls}"
                # Surefire class names in XML are FQCN; try both
                candidates = [
                    fq,
                    f"{pre_package}.{custom_id}.{cls}",
                    cls
                ]
                stat = None
                for c in candidates:
                    if c in breaking_run["per_class"]:
                        stat = breaking_run["per_class"][c]
                        break
                if not stat:
                    # if no record, treat as failed to be conservative
                    breaking_fail_classes.add(cls)
                else:
                    if stat["failures"] > 0 or stat["errors"] > 0:
                        breaking_fail_classes.add(cls)

            overall["breaking_tests_pass"] += breaking_run["totals"]["passed"] if breaking_run["compiled"] else 0
            overall["breaking_tests_fail"] += (breaking_run["totals"]["failures"] + breaking_run["totals"]["errors"]) if breaking_run["compiled"] else 0

            breaks = len(breaking_fail_classes)
            overall["breaking_changes"] += breaks

            # CSV rows (one for pre, one for breaking)
            csv_rows.append({
                "custom_id": custom_id,
                "phase": "pre",
                "compiled": "yes",
                "tests_pass": results[custom_id]["pre"]["totals"]["passed"],
                "tests_fail": results[custom_id]["pre"]["totals"]["failures"] + results[custom_id]["pre"]["totals"]["errors"],
                "breaks": 0,
                "note": ""
            })
            csv_rows.append({
                "custom_id": custom_id,
                "phase": "breaking",
                "compiled": "yes" if breaking_run["compiled"] else "no",
                "tests_pass": results[custom_id]["breaking"]["totals"]["passed"] if breaking_run["compiled"] else 0,
                "tests_fail": results[custom_id]["breaking"]["totals"]["failures"] + results[custom_id]["breaking"]["totals"]["errors"] if breaking_run["compiled"] else 0,
                "breaks": breaks,
                "note": "breaking_run_compile_failed" if not breaking_run["compiled"] else ""
            })

    # Write JSON + CSV
    RESULT_JSON.write_text(json.dumps({"overall": overall, "results": results}, indent=2), encoding="utf-8")
    with open(RESULT_CSV, "w", newline="", encoding="utf-8") as fcsv:
        writer = csv.DictWriter(fcsv, fieldnames=["custom_id","phase","compiled","tests_pass","tests_fail","breaks","note"])
        writer.writeheader()
        writer.writerows(csv_rows)

    # Print overall summary
    print("[SUMMARY] === PRE IMAGE ===")
    print(f"  Compile success: {overall['pre_compile_success']}")
    print(f"  Compile failure: {overall['pre_compile_failure']}")
    print(f"  Testcases passed: {overall['pre_tests_pass']}")
    print(f"  Testcases failed: {overall['pre_tests_fail']}")
    print("[SUMMARY] === BREAKING IMAGE (only tests that passed on PRE) ===")
    print(f"  Testcases passed: {overall['breaking_tests_pass']}")
    print(f"  Testcases failed: {overall['breaking_tests_fail']}")
    print(f"  Breaking changes (classes that passed on PRE but failed on BREAKING): {overall['breaking_changes']}")
    print(f"[INFO] JSON saved: {RESULT_JSON}")
    print(f"[INFO] CSV  saved: {RESULT_CSV}")

if __name__ == "__main__":
    main()
