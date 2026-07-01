import csv
import json
import subprocess
from pathlib import Path
from collections import Counter
from common import parse_package_summary  # assumes your existing common.py is importable

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PRIMARY_DRIVE
# the idea is to find which all instances we are actually using because we are not generating tests if the project only uses some of the annotations or variable reference. we are using when an API method of a library is called in the client.
# ====== CONFIG (edit these paths only) ======
CSV_PATH_IN  = PRIMARY_DRIVE / "updated_FinalBUMP_Instances.csv"       # existing CSV, includes custom_id, breakingCommit
CSV_PATH_OUT = PRIMARY_DRIVE / "FinalBUMP_Instances_with_TestRunner_Status.csv"  # new CSV to write
# ===============================================

LOG_PATH = Path(CSV_PATH_OUT).with_suffix(".log")

SUMMARY_PATH = PRIMARY_DRIVE / "package_structure_summary.txt"  # for test_root lookup
ABC_ROOT     = PRIMARY_DRIVE / "GeneratedOutputClientsExp3/GPT4o"      # where you check for files
# ===========================================

# Load package info once (used to determine presence of test_root)
pkg_info = parse_package_summary(SUMMARY_PATH)

def _abc_has_any_file(custom_id: str) -> bool:
    """Does ABC_ROOT/custom_id contain at least one file (anywhere under it)?"""
    d = ABC_ROOT / custom_id
    if not d.is_dir():
        return False
    for p in d.rglob("*"):
        if p.is_file():
            return True
    return False

def validate_image_runs(image_tag: str, timeout: int = 120) -> bool:
    """
    Sanity check used in our logic:
    run the image (no mounts) and consider it OK if exit code == 0 OR 'BUILD SUCCESS' in stdout.
    """
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", image_tag],
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0 or ("BUILD SUCCESS" in result.stdout)
    except Exception:
        return False



def status_for_row(custom_id: str, commit: str) -> tuple[str, str]:
    """
    Return (status, reason) for this row.
    """
    # 1) missing commit
    if not commit:
        return "skipped", "missing_commit"

    # 2) no files in ABC root
    if not _abc_has_any_file(custom_id):
        return "skipped", "no_files"

    # 3) test_root presence from summary
    test_root, _ = pkg_info.get((custom_id, "pre"), (None, None))
    if not test_root:
        return "skipped", "no_test_root"

    # 4) docker sanity check
    image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
    if not validate_image_runs(image_tag):
        return "skipped", "docker_failed"

    # Otherwise: used
    return "used", "ok"




def main():
    # Read input CSV
    with open(CSV_PATH_IN, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        in_fields = reader.fieldnames or []

    # Prepare output header (append fields if not already present)
    out_fields = list(in_fields)
    if "status" not in out_fields:
        out_fields.append("status")
    if "skip_reason" not in out_fields:
        out_fields.append("skip_reason")

    # Process and annotate status
    counts = Counter()
    skip_reasons = Counter()
    updated_rows = []

    for row in rows:
        custom_id = (row.get("custom_id") or "").strip()
        commit    = (row.get("breakingCommit") or "").strip()

        status, reason = status_for_row(custom_id, commit)
        row["status"] = status
        row["skip_reason"] = (reason if status == "skipped" else "")

        updated_rows.append(row)
        counts[status] += 1
        if status == "skipped":
            skip_reasons[reason] += 1

        print(f"{custom_id or '(no id)'} -> {status} ({reason})")

    # Ensure output directory exists
    Path(CSV_PATH_OUT).parent.mkdir(parents=True, exist_ok=True)

    # Write output CSV
    with open(CSV_PATH_OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(updated_rows)

    # Prepare summary text
    lines = []
    lines.append("\nSummary:")
    lines.append(f"  used:    {counts.get('used', 0)}")
    lines.append(f"  skipped: {counts.get('skipped', 0)}")
    lines.append("  breakdown of skipped:")
    if skip_reasons:
        for reason, cnt in skip_reasons.items():
            lines.append(f"    {reason}: {cnt}")
    else:
        lines.append("    (none)")

    # Print summary
    print("\n".join(lines))
    print(f" Wrote CSV: {CSV_PATH_OUT}")

    # Write summary to log file
    try:
        with open(LOG_PATH, "w") as lf:
            lf.write("\n".join(lines) + "\n")
            lf.write(f"CSV_OUT: {CSV_PATH_OUT}\n")
        print(f"Wrote log: {LOG_PATH}")
    except Exception as e:
        print(f" Failed to write log {LOG_PATH}: {e}")


if __name__ == "__main__":
    main()
