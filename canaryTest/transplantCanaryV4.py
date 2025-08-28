import os
import csv
import json
import shutil
import subprocess
from pathlib import Path
from collections import Counter
from common import parse_package_summary, classify_compilation_error, LOG_CANARY_DIR
# trying to execute exactly as sanity check without manually calling the mvn or anything..
# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
TRANSPLANT_OUTPUT = Path("/Volumes/Rachna-HD/CanaryResults/transplant_results_final_execv2.json")
CSV_SUMMARY_OUTPUT = Path("/Volumes/Rachna-HD/CanaryResults/transplant_results_final_exec_summaryv2.csv")

# Folder with per-custom_id subfolders to gate processing (aka "ABC").
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp3/GPT4o")

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

# Counters / aggregation
success_count = 0
failure_count = 0
failure_categories = Counter()
csv_rows = []  # rows for the summary CSV

def _extract_error_fields(err_info):
    """
    Normalize classify_compilation_error output into (category, reason) strings.
    Accepts dicts (preferred) or any printable value.
    """
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

def _hello_world_java(package_decl: str, class_name: str = "HelloWorldTest") -> str:
    return f"""\
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

def run_canary_with_bind_mount(image_tag: str, image_type: str, custom_id: str, test_root: str):
    """
    Run baseline 'docker run IMAGE', then run again with a bind-mounted LLMTest/<custom_id>/HelloWorldTest.java.
    We DO NOT exec mvn directly; we rely on the image's default entrypoint/CMD to behave identically in both runs.
    image_type: 'pre' | 'breaking' (used for filenames and reporting)
    """
    log_path = LOG_CANARY_DIR / f"{custom_id}_{image_type}_canary_exec.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_lines = [
        f"[INFO] Running bind-mount canary for {custom_id} ({image_type})",
        f"[INFO] Image: {image_tag}",
        f"[INFO] test_root: {test_root}"
    ]

    # --- (1) Baseline sanity: docker run IMAGE ---
    try:
        baseline = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", image_tag],
            capture_output=True, text=True
        )
        log_lines.append("\n[BASELINE stdout]\n" + baseline.stdout)
        if baseline.stderr.strip():
            log_lines.append("\n[BASELINE stderr]\n" + baseline.stderr)
        baseline_ok = (baseline.returncode == 0)
        log_lines.append(f"[BASELINE RESULT] {'OK' if baseline_ok else 'FAILED'} (exit={baseline.returncode})")
    except Exception as e:
        baseline_ok = False
        log_lines.append(f"[EXCEPTION during baseline] {e}")

    # --- (2) After transplant (bind-mount) ---
    # Prepare host temp dir: /tmp/llm_exec/<custom_id>/<image_type>/LLMTest/<custom_id>/HelloWorldTest.java
    tmp_root = Path(f"/tmp/llm_exec/{custom_id}/{image_type}")
    llmtest_root = tmp_root / "LLMTest"
    dest_pkg_dir = llmtest_root / custom_id
    shutil.rmtree(tmp_root, ignore_errors=True)
    dest_pkg_dir.mkdir(parents=True, exist_ok=True)

    package_decl = f"LLMTest.{custom_id}"
    class_name = "HelloWorldTest"
    java_file = dest_pkg_dir / f"{class_name}.java"
    java_file.write_text(_hello_world_java(package_decl=package_decl, class_name=class_name), encoding="utf-8")

    # Bind-mount LLMTest at <test_root>/LLMTest inside container (read-only)
    container_mount_point = f"{test_root}/LLMTest"
    docker_cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{llmtest_root}:{container_mount_point}:ro",
        image_tag
    ]

    log_lines.append(f"[INFO] After-transplant bind-mount: {llmtest_root} -> {container_mount_point} (ro)")
    try:
        after = subprocess.run(docker_cmd, capture_output=True, text=True)
        log_lines.append("\n[AFTER-TRANSPLANT stdout]\n" + after.stdout)
        if after.stderr.strip():
            log_lines.append("\n[AFTER-TRANSPLANT stderr]\n" + after.stderr)
        success = (after.returncode == 0)
        log_lines.append(f"[AFTER-TRANSPLANT RESULT] {'OK' if success else 'FAILED'} (exit={after.returncode})")
    except Exception as e:
        success = False
        log_lines.append(f"[EXCEPTION during after-transplant] {e}")

    # Persist logs and classify
    log_content = "\n".join(log_lines)
    log_path.write_text(log_content)
    error_info = classify_compilation_error(log_content)

    # Overall success is the after-transplant result; baseline is informational.
    return success, error_info, str(log_path), image_tag, baseline_ok

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
                # record once with an explicit failure
                err_category = "missing_test_root"
                err_reason = "No test_root found for (custom_id, 'pre') in package summary"
                results[custom_id] = {
                    "pre": {"status": "fail", "baseline": "n/a", "error": err_reason, "log": ""},
                    "breaking": {"status": "fail", "baseline": "n/a", "error": err_reason, "log": ""},
                }
                failure_count += 2
                failure_categories[err_category] += 1
                csv_rows.append({
                    "custom_id": custom_id,
                    "image": "pre",
                    "baseline": "n/a",
                    "result": "fail",
                    "failure_reason": err_reason,
                    "error_category": err_category,
                    "log_path": "",
                })
                csv_rows.append({
                    "custom_id": custom_id,
                    "image": "breaking",
                    "baseline": "n/a",
                    "result": "fail",
                    "failure_reason": err_reason,
                    "error_category": err_category,
                    "log_path": "",
                })
                continue

            # Image tags
            pre_image = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
            brk_image = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"

            # Ensure we have an object for this custom_id
            results.setdefault(custom_id, {})

            # Run PRE
            pre_success, pre_err, pre_log, _, pre_baseline = run_canary_with_bind_mount(
                image_tag=pre_image, image_type="pre", custom_id=custom_id, test_root=test_root
            )

            if pre_success:
                success_count += 1
                results[custom_id]["pre"] = {"status": "success", "baseline": "ok" if pre_baseline else "fail", "log": pre_log}
                csv_rows.append({
                    "custom_id": custom_id,
                    "image": "pre",
                    "baseline": "ok" if pre_baseline else "fail",
                    "result": "pass",
                    "failure_reason": "",
                    "error_category": "",
                    "log_path": pre_log,
                })
            else:
                failure_count += 1
                cat, reason = _extract_error_fields(pre_err)
                failure_categories[cat] += 1
                results[custom_id]["pre"] = {"status": "fail", "baseline": "ok" if pre_baseline else "fail",
                                             "error": pre_err, "log": pre_log}
                csv_rows.append({
                    "custom_id": custom_id,
                    "image": "pre",
                    "baseline": "ok" if pre_baseline else "fail",
                    "result": "fail",
                    "failure_reason": reason,
                    "error_category": cat,
                    "log_path": pre_log,
                })

            # Run BREAKING
            brk_success, brk_err, brk_log, _, brk_baseline = run_canary_with_bind_mount(
                image_tag=brk_image, image_type="breaking", custom_id=custom_id, test_root=test_root
            )

            if brk_success:
                success_count += 1
                results[custom_id]["breaking"] = {"status": "success", "baseline": "ok" if brk_baseline else "fail", "log": brk_log}
                csv_rows.append({
                    "custom_id": custom_id,
                    "image": "breaking",
                    "baseline": "ok" if brk_baseline else "fail",
                    "result": "pass",
                    "failure_reason": "",
                    "error_category": "",
                    "log_path": brk_log,
                })
            else:
                failure_count += 1
                cat, reason = _extract_error_fields(brk_err)
                failure_categories[cat] += 1
                results[custom_id]["breaking"] = {"status": "fail", "baseline": "ok" if brk_baseline else "fail",
                                                  "error": brk_err, "log": brk_log}
                csv_rows.append({
                    "custom_id": custom_id,
                    "image": "breaking",
                    "baseline": "ok" if brk_baseline else "fail",
                    "result": "fail",
                    "failure_reason": reason,
                    "error_category": cat,
                    "log_path": brk_log,
                })

    # Write the detailed JSON
    TRANSPLANT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    TRANSPLANT_OUTPUT.write_text(json.dumps(results, indent=2))
    print(f"[INFO] ✅ Canary execution complete. Results saved to {TRANSPLANT_OUTPUT}")

    # Write CSV summary
    CSV_SUMMARY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["custom_id", "image", "baseline", "result", "failure_reason", "error_category", "log_path"]
    with open(CSV_SUMMARY_OUTPUT, "w", newline="") as fcsv:
        writer = csv.DictWriter(fcsv, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    # Print final counts
    distinct_failure_categories = len([c for c in failure_categories if c])
    print(f"[SUMMARY] ✅ Successes: {success_count}")
    print(f"[SUMMARY] ❌ Failures: {failure_count}")
    print(f"[SUMMARY] 🧩 Distinct failure categories: {distinct_failure_categories}")
    if failure_categories:
        print("[SUMMARY] Failure category breakdown:")
        for cat, cnt in failure_categories.most_common():
            print(f"  - {cat}: {cnt}")

    print(f"[INFO] 📄 CSV summary saved to {CSV_SUMMARY_OUTPUT}")

if __name__ == "__main__":
    main()
