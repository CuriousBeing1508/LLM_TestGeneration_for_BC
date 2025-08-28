import os
import csv
import json
import shutil
import subprocess
from pathlib import Path
from collections import Counter
from common import parse_package_summary, classify_compilation_error, LOG_CANARY_DIR
# Extension of v3

# This is the best performing script, also improved the sanity failure cases by increasing the timeout time. 
# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
TRANSPLANT_OUTPUT = Path("/Volumes/Rachna-HD/CanaryResults/transplant_results_final_execv2.json")
CSV_SUMMARY_OUTPUT = Path("/Volumes/Rachna-HD/CanaryResults/transplant_results_final_exec_summaryv2.csv")
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp3/GPT4o")

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

success_count = 0
failure_count = 0
failure_categories = Counter()
csv_rows = []


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


def validate_image_runs(image_tag):
    """Sanity check: Run the image to ensure it builds and runs tests on startup."""
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", image_tag],
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0 or "BUILD SUCCESS" in result.stdout
    except Exception as e:
        print(f"[WARN] ❌ Image sanity check failed: {e}")
        return False


def run_canary_in_container(image_tag, custom_id, test_root, java_code):
    log_path = LOG_CANARY_DIR / f"{custom_id}_canary_exec.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_root = Path(f"/tmp/llm_exec/{custom_id}")
    shutil.rmtree(tmp_root, ignore_errors=True)
    dest_dir = tmp_root / "LLMTest" / custom_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    java_file = dest_dir / "HelloWorldTest.java"
    java_file.write_text(java_code)

    container_mount = f"{test_root}/LLMTest"
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{tmp_root}/LLMTest:{container_mount}:ro",
        image_tag
    ]

    log_lines = [f"[INFO] Running container for {custom_id} with image {image_tag}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        log_lines.append(proc.stdout)
        log_lines.append(proc.stderr)
        success = proc.returncode == 0 and "BUILD SUCCESS" in proc.stdout
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        success = False

    log_path.write_text("\n".join(log_lines))
    error_info = classify_compilation_error("\n".join(log_lines))
    return success, error_info, str(log_path), image_tag


def main():
    global success_count, failure_count, failure_categories

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            if not commit:
                continue

            if not _abc_has_any_file(custom_id):
                print(f"[SKIP] 🚫 No files under {ABC_ROOT}/{custom_id}; skipping.")
                continue

            test_root, _ = pkg_info.get((custom_id, "pre"), (None, None))
            if not test_root:
                err_category = "missing_test_root"
                err_reason = "No test_root found for (custom_id, 'pre') in package summary"
                failure_count += 1
                failure_categories[err_category] += 1
                csv_rows.append({
                    "custom_id": custom_id,
                    "result": "fail",
                    "failure_reason": err_reason,
                    "error_category": err_category,
                    "log_path": "",
                })
                results[custom_id] = {"status": err_category}
                continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"

            # 🧪 Sanity check: does image run without mounting anything?
            if not validate_image_runs(image_tag):
                print(f"[SKIP] ❌ Docker image {image_tag} fails sanity check. Skipping {custom_id}.")
                failure_count += 1
                failure_categories["invalid_docker_image"] += 1
                csv_rows.append({
                    "custom_id": custom_id,
                    "result": "fail",
                    "failure_reason": "Docker image fails to start or test",
                    "error_category": "invalid_docker_image",
                    "log_path": "",
                })
                results[custom_id] = {"status": "invalid_docker_image"}
                continue

            package_decl = f"LLMTest.{custom_id}"
            class_name = "HelloWorldTest"
            java_code = f"""\
package {package_decl};

import org.junit.Test;
import static org.junit.Assert.*;

public class {class_name} {{
    @Test
    public void testHello() {{
        String msg = "Hello World";
        assertEquals("Hello World", msg);
    }}
}}"""

            success, err_info, log_path, used_image_tag = run_canary_in_container(
                image_tag, custom_id, test_root, java_code
            )

            if success:
                print(f"[INFO] ✅ Canary test passed for {custom_id}")
                results[custom_id] = {"canary_status": "success"}
                success_count += 1
                csv_rows.append({
                    "custom_id": custom_id,
                    "result": "pass",
                    "failure_reason": "",
                    "error_category": "",
                    "log_path": log_path,
                })
            else:
                print(f"[ERROR] ❌ Canary test failed for {custom_id}")
                category, reason = _extract_error_fields(err_info)
                results[custom_id] = {
                    "canary_status": {
                        "error": err_info,
                        "log": log_path
                    }
                }
                failure_count += 1
                failure_categories[category] += 1
                csv_rows.append({
                    "custom_id": custom_id,
                    "result": "fail",
                    "failure_reason": reason,
                    "error_category": category,
                    "log_path": log_path,
                })

    TRANSPLANT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    TRANSPLANT_OUTPUT.write_text(json.dumps(results, indent=2))
    print(f"[INFO] ✅ Canary execution complete. Results saved to {TRANSPLANT_OUTPUT}")

    CSV_SUMMARY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["custom_id", "result", "failure_reason", "error_category", "log_path"]
    with open(CSV_SUMMARY_OUTPUT, "w", newline="") as fcsv:
        writer = csv.DictWriter(fcsv, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"[SUMMARY] ✅ Successes: {success_count}")
    print(f"[SUMMARY] ❌ Failures: {failure_count}")
    print(f"[SUMMARY] 🧩 Distinct failure categories: {len([c for c in failure_categories if c])}")
    if failure_categories:
        print("[SUMMARY] Failure category breakdown:")
        for cat, cnt in failure_categories.most_common():
            print(f"  - {cat}: {cnt}")

    print(f"[INFO] 📄 CSV summary saved to {CSV_SUMMARY_OUTPUT}")


if __name__ == "__main__":
    main()
