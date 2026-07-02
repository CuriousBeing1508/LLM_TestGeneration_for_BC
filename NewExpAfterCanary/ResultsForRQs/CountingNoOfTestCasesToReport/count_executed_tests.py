"""
Counts @Test cases that actually got EXECUTED, per (model, context, instance).

Source of truth here: pre/execute_results_pre.json's "execution_results"
field - it records, per instance, every compiled test FILE that was run against
the PRE (non-breaking) commit, split into "passed" / "failed" lists.

"Executed" = executed AND passed on the pre (non-breaking) commit. 

A file only counts as "executed" if BOTH:
  1. it's in "passed", and
  2. its pre/logs/<instance>_<file>_execute.log has a parseable
     "Tests run: N, Failures: ..., Errors: ..." line (N is the test-case
     count for that file). Files that hit BUILD SUCCESS with no such line
     (surefire silently found 0 tests) are excluded.
"""
import csv
import json
import re
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

# (context_dir, model_label) -> path to that run's "pre" folder
# (holds execute_results_pre.json + logs/*_execute.log)
PRE_DIRS = {
    ("Exp3LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre",
    ("Exp3LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre",
    ("Exp3LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre",
    ("Exp6LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/pre",
    ("Exp6LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre",
    ("Exp6LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre",
    # Exp7 GPT4o results live under "Exp7BatchResultsOp2", not "Exp7BatchResults"
    ("Exp7LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre",
    ("Exp7LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre",
    ("Exp7LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre",
}

JSON_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "ExecutedTestCount"
AGGREGATE_CSV_PATH = Path(__file__).resolve().parent / "output" / "test_count_aggregate.csv"
NOT_VALID = "NOT_VALID"
TESTS_RUN_PATTERN = re.compile(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)")


# ---------------------------------------------------------------------------
# Step 0: sanity check the config before running anything else
# ---------------------------------------------------------------------------
def verify_paths():
    """Print which pre folders exist and whether execute_results_pre.json/logs are there."""
    for (context_dir, model_label), pre_dir in PRE_DIRS.items():
        json_path = pre_dir / "execute_results_pre.json"
        logs_dir = pre_dir / "logs"
        tag = "OK" if json_path.exists() and logs_dir.exists() else "MISSING"
        log_count = len(list(logs_dir.glob("*_execute.log"))) if logs_dir.exists() else 0
        print(f"{CONTEXTS[context_dir]:8} | {model_label:12} -> {tag} ({log_count} execute logs) | {pre_dir}")

    tag = "OK" if AGGREGATE_CSV_PATH.exists() else "MISSING"
    print(f"\nAggregate CSV to update -> {tag} | {AGGREGATE_CSV_PATH}")


# ---------------------------------------------------------------------------
# Step 1: read the existing aggregate CSV just to know which instances exist
# per (model, context) - it already has the canonical 89-instance list.
# ---------------------------------------------------------------------------
def load_instance_index(csv_path):
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    index = {}
    for row in rows:
        index.setdefault((row["Model"], row["Context"]), set()).add(row["Instance"])
    return index


# ---------------------------------------------------------------------------
# Step 2: how many test CASES ran for one test file, from its pre-stage log.
# Returns None if there's no genuine "Tests run:" line.
# ---------------------------------------------------------------------------
def count_test_cases(logs_dir, instance, test_file):
    log_path = logs_dir / f"{instance}_{test_file}_execute.log"
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    matches = TESTS_RUN_PATTERN.findall(text)
    if not matches:
        return None
    tests_run, _failures, _errors = (int(x) for x in matches[-1])
    return tests_run


# ---------------------------------------------------------------------------
# Step 3: scan one execute_results_pre.json -> per-instance file/case counts.
# Only counts files in "passed" (executed AND passed on the pre commit), and
# only if their log has a confirmed "Tests run:" line. Files in "failed"
# ---------------------------------------------------------------------------
def scan_pre_results(pre_dir):
    with open(pre_dir / "execute_results_pre.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    logs_dir = pre_dir / "logs"
    execution_results = data["execution_results"]

    summary = {}
    excluded_no_confirmation = 0
    for instance, idata in execution_results.items():
        candidate_files = list(idata.get("passed", []))
        file_count = 0
        case_total = 0
        for test_file in candidate_files:
            cases = count_test_cases(logs_dir, instance, test_file)
            if cases is None:
                excluded_no_confirmation += 1
                continue
            file_count += 1
            case_total += cases
        summary[instance] = {
            "executed_files": file_count,
            "executed_cases": case_total,
        }
    return summary, excluded_no_confirmation


# ---------------------------------------------------------------------------
# Step 4: build one row per known instance; NOT_VALID if it never reached
# the pre-execution stage (e.g. 0 compiled files).
# ---------------------------------------------------------------------------
def build_rows(pre_summary, instances, model_label, context_label):
    rows = []
    for instance in sorted(instances):
        if instance not in pre_summary:
            rows.append({
                "Model": model_label,
                "Context": context_label,
                "Instance": instance,
                "ExecutedTestFileCount": NOT_VALID,
                "ExecutedTestCount": NOT_VALID,
            })
        else:
            s = pre_summary[instance]
            rows.append({
                "Model": model_label,
                "Context": context_label,
                "Instance": instance,
                "ExecutedTestFileCount": s["executed_files"],
                "ExecutedTestCount": s["executed_cases"],
            })
    return rows


# ---------------------------------------------------------------------------
# Step 5: save the per-instance rows for one (context, model) pair as JSON
# ---------------------------------------------------------------------------
def save_json(rows, context_dir, model_label):
    JSON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = JSON_OUTPUT_DIR / f"{context_dir}_{model_label}_executed_test_counts_v2.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# Step 6: overwrite ONLY ExecutedTestFileCount/ExecutedTestCount in the
# existing aggregate CSV, keyed by (Model, Context, Instance). All other
# columns (including DetectedTestFileCount/DetectedBCTestCount) are untouched.
# ---------------------------------------------------------------------------
def update_execution_columns_in_csv(all_rows, csv_path):
    exec_by_key = {(r["Model"], r["Context"], r["Instance"]): r for r in all_rows}

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        existing_rows = list(csv.DictReader(f))
        fieldnames = list(existing_rows[0].keys())

    for row in existing_rows:
        key = (row["Model"], row["Context"], row["Instance"])
        result = exec_by_key.get(key)
        if result is None:
            continue
        row["ExecutedTestFileCount"] = result["ExecutedTestFileCount"]
        row["ExecutedTestCount"] = result["ExecutedTestCount"]

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
    total_excluded_no_confirmation = 0
    for (context_dir, model_label), pre_dir in PRE_DIRS.items():
        json_path = pre_dir / "execute_results_pre.json"
        if not json_path.exists():
            print(f"Skipping missing pre dir: {pre_dir}")
            continue

        context_label = CONTEXTS[context_dir]
        instances = instance_index.get((model_label, context_label), set())

        pre_summary, excluded_no_confirmation = scan_pre_results(pre_dir)
        total_excluded_no_confirmation += excluded_no_confirmation
        rows = build_rows(pre_summary, instances, model_label, context_label)
        json_path_out = save_json(rows, context_dir, model_label)
        print(f"Wrote {len(rows)} rows (excluded {excluded_no_confirmation} unconfirmed passes) -> {json_path_out}")
        all_rows.extend(rows)

    update_execution_columns_in_csv(all_rows, AGGREGATE_CSV_PATH)
    print(f"\nUpdated ExecutedTestFileCount/ExecutedTestCount -> {AGGREGATE_CSV_PATH}")
    print(f"Total excluded (no confirmed 'Tests run:' line): {total_excluded_no_confirmation}")


if __name__ == "__main__":
    verify_paths()
    # Once the printout above looks correct, comment out verify_paths()
    # and uncomment main() to actually generate the JSON + update the CSV.
    main()
