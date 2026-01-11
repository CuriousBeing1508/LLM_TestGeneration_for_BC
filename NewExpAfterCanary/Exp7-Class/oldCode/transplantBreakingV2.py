import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from common import parse_package_summary, classify_compilation_error, LOG_DIR_BATCH_BRE

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
PRE_RESULTS_PATH = "/Volumes/Rachna-HD/Exp7BatchResults/pre/transplant_results_final_pre.json"
BREAKING_OUTPUT = Path("/Volumes/Rachna-HD/Exp7BatchResults/breaking/transplant_results_final_breaking.json")
CSV_SUMMARY_OUTPUT = Path("/Volumes/Rachna-HD/Exp7BatchResults/breaking/transplant_results_final_breaking_summary.csv")
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp7Batch/GPT4o")
MODEL_NAME = ABC_ROOT.name  # e.g., GPT4o

pkg_info = parse_package_summary(SUMMARY_PATH)

results = {}
success_count = 0
failure_count = 0
failure_categories = Counter()
csv_rows = []

carry_forward_instances = set()
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})


def _abc_has_any_file(custom_id: str) -> bool:
    d = ABC_ROOT / custom_id
    if not d.is_dir():
        return False
    for p in d.rglob("*"):
        if p.is_file():
            return True
    return False


def run_test_in_isolation(image_tag: str, custom_id: str, test_root: str,
                          good_tests_dir: str, java_file: str):
    """
    Run a single test in isolation by mounting only that test file.
    Parse Surefire logs for this specific test class.
    """
    scratch_dir = Path(f"/tmp/llm_exec_breaking/{custom_id}/scratch/{java_file}")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_pkg_dir = scratch_dir / "LLMTest"
    scratch_pkg_dir.mkdir(parents=True, exist_ok=True)

    # Copy only this java file from good_tests dir
    found = None
    for root, _, files in os.walk(good_tests_dir):
        if java_file in files:
            src_path = Path(root) / java_file
            rel_path = Path(root).relative_to(Path(good_tests_dir))
            dest_path = scratch_pkg_dir / rel_path
            dest_path.mkdir(parents=True, exist_ok=True)
            shutil.copy(src_path, dest_path / java_file)
            found = src_path
            break
    if not found:
        print(f"[WARN] Could not find good test {java_file} for {custom_id}")
        return False, None, ""

    log_path = LOG_DIR_BATCH_BRE / f"{custom_id}_{java_file}_breaking_exec.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    container_mount = f"{test_root}/LLMTest"
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{scratch_pkg_dir}:{container_mount}:ro",
        image_tag
    ]

    log_lines = [f"[INFO] Running isolated BREAKING test {java_file} for {custom_id} using {image_tag}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        log_lines.append(proc.stdout)
        log_lines.append(proc.stderr)

        # 🔹 Check Surefire output for this specific test
        test_class = java_file.replace(".java", "")
        pattern = rf"Running .*{test_class}.*?Tests run: (\d+), Failures: (\d+), Errors: (\d+)"
        m = re.search(pattern, proc.stdout, flags=re.DOTALL)
        if m:
            failures = int(m.group(2))
            errors = int(m.group(3))
            success = (failures == 0 and errors == 0)
        else:
            success = proc.returncode == 0 and "BUILD SUCCESS" in proc.stdout

    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        success = False

    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    err_info = classify_compilation_error(log_text)

    return success, err_info, str(log_path)


def main():
    global success_count, failure_count

    # Load pre results
    pre_data = json.loads(Path(PRE_RESULTS_PATH).read_text(encoding="utf-8"))
    carry_forward_tests.update(pre_data.get("carry_forward_tests", {}))
    carry_forward_instances.update(pre_data.get("carry_forward_instances", []))

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            if not commit:
                continue

            print(f"[DEBUG] Checking custom_id={custom_id}, commit={commit}")

            if not _abc_has_any_file(custom_id):
                print(f"  -> skipped (no abc files in ABC_ROOT)")
                continue

            if custom_id not in carry_forward_instances:
                print(f"  -> skipped (not in carry_forward_instances)")
                continue

            test_root, real_package = pkg_info.get((custom_id, "breaking"), (None, None))
            if not test_root or not real_package:
                print(f"  -> skipped (missing test_root/package)")
                continue

            good_tests_dir = Path(f"/tmp/llm_exec/{custom_id}/{MODEL_NAME}/LLMTest")
            if not good_tests_dir.exists():
                print(f"  -> skipped (no good_tests dir)")
                continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
            per_test_status = {"passed": [], "failed": []}

            # Run only those that passed in pre
            for java_file in carry_forward_tests[custom_id]["passed"]:
                success, err_info, log_path = run_test_in_isolation(
                    image_tag, custom_id, test_root, str(good_tests_dir), java_file
                )

                if success:
                    print(f"[INFO] Breaking test PASSED for {custom_id}/{java_file}")
                    success_count += 1
                    per_test_status["passed"].append(java_file)
                    csv_rows.append({
                        "custom_id": custom_id,
                        "test": java_file,
                        "result": "pass",
                        "failure_reason": "",
                        "error_category": "",
                        "log_path": log_path,
                    })
                else:
                    print(f"[ERROR] Breaking test FAILED for {custom_id}/{java_file}")
                    failure_count += 1
                    category, reason = "unknown", ""
                    if err_info:
                        category = str(err_info.get("category", "unknown"))
                        reason = str(err_info.get("reason", err_info.get("message", "")))
                    per_test_status["failed"].append(java_file)
                    csv_rows.append({
                        "custom_id": custom_id,
                        "test": java_file,
                        "result": "fail",
                        "failure_reason": reason,
                        "error_category": category,
                        "log_path": log_path,
                    })

            results[custom_id] = {"tests": per_test_status}

    # Write JSON
    BREAKING_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    BREAKING_OUTPUT.write_text(json.dumps({
        "results": results,
        "summary": {
            "total_pass": success_count,
            "total_fail": failure_count
        }
    }, indent=2), encoding="utf-8")

    # Write CSV
    CSV_SUMMARY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["custom_id", "test", "result", "failure_reason", "error_category", "log_path"]
    with open(CSV_SUMMARY_OUTPUT, "w", newline="", encoding="utf-8") as fcsv:
        writer = csv.DictWriter(fcsv, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"[SUMMARY] Breaking stage: Successes={success_count}, Failures={failure_count}")
    print(f"[INFO] CSV summary saved to {CSV_SUMMARY_OUTPUT}")


if __name__ == "__main__":
    main()
