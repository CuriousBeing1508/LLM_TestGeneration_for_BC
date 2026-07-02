"""
Counts test files/cases that DETECTED the breaking change when re-run against
the breaking commit, per (model, context, instance).

Source of truth: each run's bre/transplant_results_breaking_single_module.json
"results"[instance]["tests"]["failed"] 
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

# (context_dir, model_label) -> path to that run's bre folder
# (holds transplant_results_breaking_single_module.json)
BRE_DIRS = {
    ("Exp3LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre",
    ("Exp3LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/bre",
    ("Exp3LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/bre",
    ("Exp6LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/bre",
    ("Exp6LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/bre",
    ("Exp6LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/bre",
    # Exp7 GPT4o results live under "Exp7BatchResultsOp2", not "Exp7BatchResults"
    ("Exp7LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/bre",
    ("Exp7LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/bre",
    ("Exp7LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/bre",
}

JSON_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "DetectedTestCount"
AGGREGATE_CSV_PATH = Path(__file__).resolve().parent / "output" / "test_count_aggregate.csv"
NOT_VALID = "NOT_VALID"
TESTS_RUN_PATTERN = re.compile(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)")
NO_CASE_DATA_FALLBACK = 1  # convention for build_failure_without_test_execution / transplant_issue


# ---------------------------------------------------------------------------
# Step 0: sanity check the config before running anything else
# ---------------------------------------------------------------------------
def verify_paths():
    """Print which bre folders have transplant_results_breaking_single_module.json."""
    for (context_dir, model_label), bre_dir in BRE_DIRS.items():
        json_path = bre_dir / "transplant_results_breaking_single_module.json"
        tag = "OK" if json_path.exists() else "MISSING"
        print(f"{CONTEXTS[context_dir]:8} | {model_label:12} -> {tag} | {json_path}")

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
# Step 2: how many test cases one "failed" entry represents.
# ---------------------------------------------------------------------------
def count_detected_cases(failed_entry):
    if failed_entry["result_type"] == "test_failure_breaking_change":
        match = TESTS_RUN_PATTERN.search(failed_entry["failure_reason"])
        if match:
            _tests_run, failures, errors = (int(x) for x in match.groups())
            return failures + errors, False
        return NO_CASE_DATA_FALLBACK, True  # shouldn't happen - all verified parseable
    return NO_CASE_DATA_FALLBACK, True


# ---------------------------------------------------------------------------
# Step 3: scan one transplant_results_breaking_single_module.json -> per-
# instance file/case counts. Every entry in "failed" counts as detected,
# regardless of result_type (test_failure_breaking_change,
# build_failure_without_test_execution, or transplant_issue).
# ---------------------------------------------------------------------------
def scan_transplant_results(bre_dir):
    with open(bre_dir / "transplant_results_breaking_single_module.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    summary = {}
    fallback_count = 0
    for instance, tdata in data["results"].items():
        failed = tdata["tests"]["failed"]
        case_total = 0
        for entry in failed:
            cases, used_fallback = count_detected_cases(entry)
            case_total += cases
            fallback_count += used_fallback
        summary[instance] = {
            "detected_files": len(failed),
            "detected_cases": case_total,
        }
    return summary, fallback_count


# ---------------------------------------------------------------------------
# Step 4: build one row per known instance; NOT_VALID if it never reached
# the bre stage at all (not in transplant_results "results").
# ---------------------------------------------------------------------------
def build_rows(transplant_summary, instances, model_label, context_label):
    rows = []
    for instance in sorted(instances):
        if instance not in transplant_summary:
            rows.append({
                "Model": model_label,
                "Context": context_label,
                "Instance": instance,
                "DetectedTestFileCount": NOT_VALID,
                "DetectedBCTestCount": NOT_VALID,
            })
        else:
            s = transplant_summary[instance]
            rows.append({
                "Model": model_label,
                "Context": context_label,
                "Instance": instance,
                "DetectedTestFileCount": s["detected_files"],
                "DetectedBCTestCount": s["detected_cases"],
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
    total_fallback = 0
    for (context_dir, model_label), bre_dir in BRE_DIRS.items():
        json_path = bre_dir / "transplant_results_breaking_single_module.json"
        if not json_path.exists():
            print(f"Skipping missing bre dir: {bre_dir}")
            continue

        context_label = CONTEXTS[context_dir]
        instances = instance_index.get((model_label, context_label), set())

        transplant_summary, fallback_count = scan_transplant_results(bre_dir)
        total_fallback += fallback_count
        rows = build_rows(transplant_summary, instances, model_label, context_label)
        json_path_out = save_json(rows, context_dir, model_label)
        print(f"Wrote {len(rows)} rows ({fallback_count} files with no real case data, "
              f"counted as {NO_CASE_DATA_FALLBACK}) -> {json_path_out}")
        all_rows.extend(rows)

    update_detection_columns_in_csv(all_rows, AGGREGATE_CSV_PATH)
    print(f"\nUpdated DetectedTestFileCount/DetectedBCTestCount columns -> {AGGREGATE_CSV_PATH}")
    print(f"Total files counted via the no-case-data fallback (build_failure_without_test_execution "
          f"/ transplant_issue): {total_fallback}")


if __name__ == "__main__":
    verify_paths()
    # Once the printout above looks correct, comment out verify_paths()
    # and uncomment main() to actually generate the JSON + update the CSV.
    main()
