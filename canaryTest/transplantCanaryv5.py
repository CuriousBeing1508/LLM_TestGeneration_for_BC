import os
import csv
import json
import shutil
import subprocess
from pathlib import Path
from collections import Counter
from common import parse_package_summary, classify_compilation_error, LOG_CANARY_DIR

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
TRANSPLANT_OUTPUT = Path("/Volumes/Rachna-HD/CanaryResults/transplant_results_simple.json")
CSV_SUMMARY_OUTPUT = Path("/Volumes/Rachna-HD/CanaryResults/transplant_results_simple_summary.csv")
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp3/GPT4o")

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}
success_count = 0
failure_count = 0
failure_categories = Counter()
csv_rows = []

def _hello_world_java(pkg, cls="HelloWorldTest") -> str:
    return f"""\
package {pkg};

import org.junit.Test;
import static org.junit.Assert.*;

public class {cls} {{
    @Test
    public void testHello() {{
        String msg = "Hello World";
        assertEquals("Hello World", msg);
    }}
}}"""

def run_canary(image_tag, custom_id, image_type, test_root):
    log_path = LOG_CANARY_DIR / f"{custom_id}_{image_type}.log"
    tmp_root = Path(f"/tmp/llm_exec/{custom_id}/{image_type}")
    shutil.rmtree(tmp_root, ignore_errors=True)

    dest_dir = tmp_root / "LLMTest" / custom_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    java_file = dest_dir / "HelloWorldTest.java"
    java_file.write_text(_hello_world_java(f"LLMTest.{custom_id}"))

    container_mount = f"{test_root}/LLMTest"
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{tmp_root}/LLMTest:{container_mount}:ro",
        image_tag
    ]

    with log_path.open("w", encoding="utf-8", buffering=1) as logf:
        logf.write(f"[INFO] Running {image_tag}\n")
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
        exit_code = proc.returncode

    success = (exit_code == 0)
    err_info = classify_compilation_error(log_path.read_text())
    return success, err_info, str(log_path)

def main():
    global success_count, failure_count

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            print(f"\n[{i+1}] {custom_id}")

            if not commit:
                print("❌ Missing commit. Skipping.")
                continue

            if not (ABC_ROOT / custom_id).is_dir():
                print("❌ No files under ABC. Skipping.")
                continue

            test_root, _ = pkg_info.get((custom_id, "pre"), (None, None))
            if not test_root:
                print("❌ No test root. Skipping.")
                continue

            pre_img = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
            brk_img = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
            results.setdefault(custom_id, {})

            for image_type, image in [("pre", pre_img), ("breaking", brk_img)]:
                success, err, log = run_canary(image, custom_id, image_type, test_root)
                status = "pass" if success else "fail"
                print(f"  → {image_type}: {status}")
                row_data = {
                    "custom_id": custom_id,
                    "image": image_type,
                    "result": status,
                    "failure_reason": "" if success else str(err.get("reason", "")),
                    "error_category": "" if success else str(err.get("category", "")),
                    "log_path": log
                }
                csv_rows.append(row_data)
                results[custom_id][image_type] = row_data
                if success:
                    success_count += 1
                else:
                    failure_count += 1
                    failure_categories[row_data["error_category"]] += 1

    TRANSPLANT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    TRANSPLANT_OUTPUT.write_text(json.dumps(results, indent=2))

    with open(CSV_SUMMARY_OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["custom_id", "image", "result", "failure_reason", "error_category", "log_path"])
        writer.writeheader()
        writer.writerows(csv_rows)

    print("\n✅ Done.")
    print(f"Success: {success_count} | Fail: {failure_count}")
    if failure_categories:
        print("Failure categories:")
        for cat, count in failure_categories.items():
            print(f"  - {cat}: {count}")
    print(f"CSV: {CSV_SUMMARY_OUTPUT}")

if __name__ == "__main__":
    main()
