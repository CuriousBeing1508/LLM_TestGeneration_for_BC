"""
Counts @Test annotations that survive compilation, per (model, context, instance).

Source of truth: the compile_results_pre.json log written by each experiment's
Phase1Compilationv1.py script. It already records, per instance:
  - files_generated           -> how many .txt test files were generated
  - files_compiled            -> how many of those compiled successfully
  - tests_in_generated_files  -> total @Test count across all generated files
  - tests_in_compiled_files   -> total @Test count across only the compiled files

This script:
  1. Verifies the configured compile_results_pre.json paths exist (run verify_paths() first).
  2. Writes one JSON file per (context, model) with one row per instance.
  3. Adds CompiledTestFileCount / CompiledTestCount columns to the existing
     test_count_aggregate.csv
"""
import csv
import json
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

# (context_dir, model_label) -> path to that run's compile_results_pre.json
COMPILE_RESULTS_PATHS = {
    ("Exp3LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/compile_results_pre.json",
    ("Exp3LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/compile_results_pre.json",
    ("Exp3LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/compile_results_pre.json",
    ("Exp6LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/pre/compile_results_pre.json",
    ("Exp6LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/compile_results_pre.json",
    ("Exp6LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/compile_results_pre.json",
    # Exp7 GPT4o results live under "Exp7BatchResultsOp2", not "Exp7BatchResults"
    ("Exp7LLMOutput", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/compile_results_pre.json",
    ("Exp7LLMOutput", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/compile_results_pre.json",
    ("Exp7LLMOutput", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/compile_results_pre.json",
}

JSON_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "CompiledTestCount"
AGGREGATE_CSV_PATH = Path(__file__).resolve().parent / "output" / "test_count_aggregate.csv"
NOT_COMPILED = "NOT_COMPILED"


# ---------------------------------------------------------------------------
# Step 0: sanity check the config before running anything else
# ---------------------------------------------------------------------------
def verify_paths():
    """Print which compile_results_pre.json files exist, and whether the aggregate CSV to update exists."""
    for (context_dir, model_label), path in COMPILE_RESULTS_PATHS.items():
        tag = "OK" if path.exists() else "MISSING"
        print(f"{CONTEXTS[context_dir]:8} | {model_label:12} -> {tag} | {path}")

    tag = "OK" if AGGREGATE_CSV_PATH.exists() else "MISSING"
    print(f"\nAggregate CSV to update -> {tag} | {AGGREGATE_CSV_PATH}")


# ---------------------------------------------------------------------------
# Step 1: load one compile_results_pre.json
# ---------------------------------------------------------------------------
def load_compile_results(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Step 2: build one row per instance (skip CSV rows with no generated files -
# those are instances outside our 89-instance filtered dataset)
# ---------------------------------------------------------------------------
def build_instance_rows(compile_data, model_label, context_label):
    rows = []
    for instance, counts in compile_data["file_counts"].items():
        if counts["files_generated"] == 0:
            continue
        test_counts = compile_data["test_counts"][instance]
        rows.append({
            "Model": model_label,
            "Context": context_label,
            "Instance": instance,
            "GeneratedTestFileCount": counts["files_generated"],
            "CompiledTestFileCount": counts["files_compiled"],
            "TestCount": test_counts["tests_in_compiled_files"],
        })
    return rows


# ---------------------------------------------------------------------------
# Step 3: save the per-instance rows for one (context, model) pair as JSON
# ---------------------------------------------------------------------------
def save_json(rows, context_dir, model_label):
    JSON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = JSON_OUTPUT_DIR / f"{context_dir}_{model_label}_compiled_test_counts.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# Step 4: add CompiledTestFileCount/CompiledTestCount columns to the existing
# aggregate CSV, keyed by (Model, Context, Instance). Existing columns and
# rows are preserved as-is.
# ---------------------------------------------------------------------------
def add_compiled_columns_to_csv(all_rows, csv_path):
    compiled_by_key = {(r["Model"], r["Context"], r["Instance"]): r for r in all_rows}

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        existing_rows = list(csv.DictReader(f))

    fieldnames = list(existing_rows[0].keys()) + ["CompiledTestFileCount", "CompiledTestCount"]

    for row in existing_rows:
        key = (row["Model"], row["Context"], row["Instance"])
        compiled = compiled_by_key.get(key)
        if compiled is None or compiled["CompiledTestFileCount"] == 0:
            row["CompiledTestFileCount"] = NOT_COMPILED
            row["CompiledTestCount"] = NOT_COMPILED
        else:
            row["CompiledTestFileCount"] = compiled["CompiledTestFileCount"]
            row["CompiledTestCount"] = compiled["TestCount"]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)


# ---------------------------------------------------------------------------
# Main: loop over every configured context x model, write JSON, then update CSV
# ---------------------------------------------------------------------------
def main():
    all_rows = []
    for (context_dir, model_label), path in COMPILE_RESULTS_PATHS.items():
        if not path.exists():
            print(f"Skipping missing file: {path}")
            continue

        compile_data = load_compile_results(path)
        rows = build_instance_rows(compile_data, model_label, CONTEXTS[context_dir])
        json_path = save_json(rows, context_dir, model_label)
        print(f"Wrote {len(rows)} rows -> {json_path}")
        all_rows.extend(rows)

    add_compiled_columns_to_csv(all_rows, AGGREGATE_CSV_PATH)
    print(f"Added CompiledTestFileCount/CompiledTestCount columns -> {AGGREGATE_CSV_PATH}")


if __name__ == "__main__":
    verify_paths()
    # Once the printout above looks correct, comment out verify_paths()
    # and uncomment main() to actually generate the JSON + update the CSV.
    main()
