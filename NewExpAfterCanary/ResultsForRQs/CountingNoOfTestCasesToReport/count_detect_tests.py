"""
Counts @Test cases that got re-executed against the BREAKING commit, and how
many of those detected the breaking change (failed or errored), per
(model, context, instance).

Source of truth: the raw *.log files under each run's bre/logs folder
Each log ends with a line like:
    Tests run: 1, Failures: 1, Errors: 0
"DetectedBCTestCount" = Failures + Errors (a failure/error means the test
caught the breaking change). "DetectedTestFileCount" counts how many log
files (test files) had at least one detecting test, rather than summing
individual @Test cases.

If a log line has multiple "Tests run: ..." occurrences (surefire prints the
per-class line, then a final summary line), the LAST occurrence in the file is
used since it is the authoritative final summary.

If an instance has no log files at all in bre/logs, it never got re-run
against the breaking commit - its row is marked NOT_VALID instead of 0.

This script only writes DetectedTestFileCount / DetectedBCTestCount. It does
NOT touch ExecutedTestFileCount / ExecutedTestCount - those are written by
count_executed_tests.py from the pre-stage results (bre/logs only covers the
subset of instances carried forward from the pre stage, so it undercounts
"executed" - see that script's docstring).

This script:
  1. Verifies the configured bre/logs folders exist (run verify_paths() first).
  2. Writes one JSON file per (context, model) with one row per instance.
  3. Adds DetectedTestFileCount / DetectedBCTestCount columns to the existing
     test_count_aggregate.csv, without touching its other columns.
"""
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

# ---------------------------------------------------------------------------
# Config - edit here, then run verify_paths() before running main()
# ---------------------------------------------------------------------------
CONTEXTS = {
    "Exp3LLMOutput": "Minimal",
    "Exp6LLMOutput": "Method",
    "Exp7LLMOutput": "Class",
}

# (context_dir, model_label) -> path to that run's bre/logs folder
BRE_LOGS_DIRS = {
    ("Exp3LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/logs",
    ("Exp3LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/bre/logs",
    ("Exp3LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/bre/logs",
    ("Exp6LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/bre/logs",
    ("Exp6LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/bre/logs",
    ("Exp6LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/bre/logs",
    # Exp7 GPT4o results live under "Exp7BatchResultsOp2", not "Exp7BatchResults"
    ("Exp7LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/bre/logs",
    ("Exp7LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/bre/logs",
    ("Exp7LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/bre/logs",
}

JSON_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "DetectedTestCount"
AGGREGATE_CSV_PATH = Path(__file__).resolve().parent / "output" / "test_count_aggregate.csv"
NOT_VALID = "NOT_VALID"
TESTS_RUN_PATTERN = re.compile(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)")


# ---------------------------------------------------------------------------
# Step 0: sanity check the config before running anything else
# ---------------------------------------------------------------------------
def verify_paths():
    """Print which bre/logs folders exist and how many .log files each has."""
    for (context_dir, model_label), logs_dir in BRE_LOGS_DIRS.items():
        if logs_dir.exists():
            log_count = len(list(logs_dir.glob("*.log")))
            print(f"{CONTEXTS[context_dir]:8} | {model_label:12} -> OK ({log_count} logs) | {logs_dir}")
        else:
            print(f"{CONTEXTS[context_dir]:8} | {model_label:12} -> MISSING | {logs_dir}")

    tag = "OK" if AGGREGATE_CSV_PATH.exists() else "MISSING"
    print(f"\nAggregate CSV to update -> {tag} | {AGGREGATE_CSV_PATH}")


# ---------------------------------------------------------------------------
# Step 1: read the existing aggregate CSV just to know which instances exist
# per (model, context) - it already has the canonical 89-instance list.
# ---------------------------------------------------------------------------
def load_instance_index(csv_path):
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    index = defaultdict(set)
    for row in rows:
        index[(row["Model"], row["Context"])].add(row["Instance"])
    return index


# ---------------------------------------------------------------------------
# Step 2: parse a single log file -> (tests_executed, tests_that_detected_bc)
# ---------------------------------------------------------------------------
def parse_log_file(log_path):
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    matches = TESTS_RUN_PATTERN.findall(text)
    if not matches:
        return 0, 0
    tests_run, failures, errors = (int(x) for x in matches[-1])
    return tests_run, failures + errors


# ---------------------------------------------------------------------------
# Step 3: scan one bre/logs folder, summing per-file results by instance
# (log filenames look like "BBC162_BBC162U1Test.java_breaking_single.log")
# ---------------------------------------------------------------------------
def scan_logs_dir(logs_dir):
    summary = defaultdict(lambda: {"executed": 0, "detected_bc": 0, "executed_files": 0, "detected_files": 0})
    for log_path in logs_dir.glob("*.log"):
        instance = log_path.name.split("_", 1)[0]
        executed, detected_bc = parse_log_file(log_path)
        summary[instance]["executed"] += executed
        summary[instance]["detected_bc"] += detected_bc
        if executed > 0:
            summary[instance]["executed_files"] += 1
        if detected_bc > 0:
            summary[instance]["detected_files"] += 1
    return summary


# ---------------------------------------------------------------------------
# Step 4: build one row per known instance; NOT_VALID if it has no logs at all
# ---------------------------------------------------------------------------
def build_rows(logs_summary, instances, model_label, context_label):
    rows = []
    for instance in sorted(instances):
        if instance not in logs_summary:
            rows.append({
                "Model": model_label,
                "Context": context_label,
                "Instance": instance,
                "ExecutedTestFileCount": NOT_VALID,
                "ExecutedTestCount": NOT_VALID,
                "DetectedTestFileCount": NOT_VALID,
                "DetectedBCTestCount": NOT_VALID,
            })
        else:
            s = logs_summary[instance]
            rows.append({
                "Model": model_label,
                "Context": context_label,
                "Instance": instance,
                "ExecutedTestFileCount": s["executed_files"],
                "ExecutedTestCount": s["executed"],
                "DetectedTestFileCount": s["detected_files"],
                "DetectedBCTestCount": s["detected_bc"],
            })
    return rows


# ---------------------------------------------------------------------------
# Step 5: save the per-instance rows for one (context, model) pair as JSON
# ---------------------------------------------------------------------------
def save_json(rows, context_dir, model_label):
    JSON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = JSON_OUTPUT_DIR / f"{context_dir}_{model_label}_detected_test_counts.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# Step 6: overwrite ONLY DetectedTestFileCount/DetectedBCTestCount in the
# existing aggregate CSV, keyed by (Model, Context, Instance). All other
# columns (including ExecutedTestFileCount/ExecutedTestCount, written by
# count_executed_tests.py) are left untouched.
# ---------------------------------------------------------------------------
def update_detection_columns_in_csv(all_rows, csv_path):
    detect_by_key = {(r["Model"], r["Context"], r["Instance"]): r for r in all_rows}

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        existing_rows = list(csv.DictReader(f))
        fieldnames = list(existing_rows[0].keys())

    new_columns = ["DetectedTestFileCount", "DetectedBCTestCount"]
    for column in new_columns:
        if column not in fieldnames:
            fieldnames.append(column)

    for row in existing_rows:
        key = (row["Model"], row["Context"], row["Instance"])
        result = detect_by_key.get(key)
        for column in new_columns:
            row[column] = NOT_VALID if result is None else result[column]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)


# ---------------------------------------------------------------------------
# Main: loop over every configured context x model, write JSON, then update CSV
# ---------------------------------------------------------------------------
def main():
    instance_index = load_instance_index(AGGREGATE_CSV_PATH)

    all_rows = []
    for (context_dir, model_label), logs_dir in BRE_LOGS_DIRS.items():
        if not logs_dir.exists():
            print(f"Skipping missing logs dir: {logs_dir}")
            continue

        context_label = CONTEXTS[context_dir]
        instances = instance_index.get((model_label, context_label), set())

        logs_summary = scan_logs_dir(logs_dir)
        rows = build_rows(logs_summary, instances, model_label, context_label)
        json_path = save_json(rows, context_dir, model_label)
        print(f"Wrote {len(rows)} rows -> {json_path}")
        all_rows.extend(rows)

    update_detection_columns_in_csv(all_rows, AGGREGATE_CSV_PATH)
    print(f"Updated DetectedTestFileCount/DetectedBCTestCount columns -> {AGGREGATE_CSV_PATH}")


if __name__ == "__main__":
    verify_paths()
    # Once the printout above looks correct, comment out verify_paths()
    # and uncomment main() to actually generate the JSON + update the CSV.
    main()
