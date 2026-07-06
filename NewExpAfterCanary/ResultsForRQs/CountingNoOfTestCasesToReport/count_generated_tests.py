"""
Counts @Test annotations in the LLM-generated test files under FilteredDataset.

Layout on disk:
    FilteredDataset/<ExpXLLMOutput>/<ModelDir>/<InstanceDir>/*.txt

Each .txt file holds one generated Java test class (inside a ```java fence).
This script:
  1. Verifies the configured paths exist (run verify_paths() first).
  2. Counts @Test per test class - recognizing both a bare `@Test` (imported)
     and a fully-qualified annotation (`@org.junit.Test`,
     `@org.junit.jupiter.api.Test`, ...), which the model sometimes emits
     instead of importing `Test`. Falls back to counting `public void
     testXxx()`-named methods for JUnit-3-style classes (`extends TestCase`),
     which use no annotation at all.
  3. Writes one JSON file per (context, model) with one row per test file.
  3. Aggregates those rows to one CSV with one row per (model, context, instance).
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
BASE_DIR = PRIMARY_DRIVE / "FilteredDataset"

# folder name -> friendly context label
CONTEXTS = {
    "Exp3LLMOutput": "Minimal",
    "Exp6LLMOutput": "Method",
    "Exp7LLMOutput": "Class",
}

# folder name 
MODEL_DIR_ALIASES = {
    "GPT4o": "GPT4o",
    "GPT_OSS_120b": "GPTOSS",
    "Qwen3_480b_cloud": "Qwen3-coder",
    "Qwen_480b_cloud": "Qwen3-coder",
}

OUTPUT_DIR = Path(__file__).resolve().parent / "output"/ "GeneratedTestCount"
# matches @Test and qualified forms like @org.junit.Test / @org.junit.jupiter.api.Test,
# but not @TestFactory/@ParameterizedTest (no word boundary right after "Test")
TEST_ANNOTATION_PATTERN = re.compile(r"@(?:[\w.]*\.)?Test\b")
JUNIT3_CLASS_PATTERN = re.compile(r"\bextends\s+TestCase\b")
JUNIT3_METHOD_PATTERN = re.compile(r"\bpublic\s+void\s+test\w*\s*\(")


# ---------------------------------------------------------------------------
# Step 0: sanity check the config before running anything else
# ---------------------------------------------------------------------------
def verify_paths():
    """Print which context/model folders exist and how many instance folders each has."""
    if not BASE_DIR.exists():
        print(f"MISSING base dir: {BASE_DIR}")
        return

    for context_dir, context_label in CONTEXTS.items():
        context_path = BASE_DIR / context_dir
        if not context_path.exists():
            print(f"MISSING context folder: {context_path}")
            continue

        for model_dir in sorted(p.name for p in context_path.iterdir() if p.is_dir()):
            model_label = MODEL_DIR_ALIASES.get(model_dir)
            model_path = context_path / model_dir
            instance_count = sum(1 for p in model_path.iterdir() if p.is_dir())
            tag = model_label if model_label else "SKIPPED (not in MODEL_DIR_ALIASES)"
            print(f"{context_label:8} | {model_dir:20} -> {instance_count:3} instances | {tag}")


# ---------------------------------------------------------------------------
# Step 1: count @Test annotations in a single generated file
# ---------------------------------------------------------------------------
def count_tests_in_file(file_path):
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    annotation_count = len(TEST_ANNOTATION_PATTERN.findall(text))
    if annotation_count > 0:
        return annotation_count
    if JUNIT3_CLASS_PATTERN.search(text):
        return len(JUNIT3_METHOD_PATTERN.findall(text))
    return 0


# ---------------------------------------------------------------------------
# Step 2: walk one model+context folder and build one row per test file
# ---------------------------------------------------------------------------
def parse_model_context_folder(model_path, model_label, context_label):
    rows = []
    for instance_dir in sorted(p for p in model_path.iterdir() if p.is_dir()):
        for test_file in sorted(instance_dir.glob("*.txt")):
            rows.append({
                "Model": model_label,
                "Context": context_label,
                "Instance": instance_dir.name,
                "TestFileName": test_file.name,
                "TestCount": count_tests_in_file(test_file),
            })
    return rows


# ---------------------------------------------------------------------------
# Step 3: save the per-file rows for one (context, model) pair as JSON
# ---------------------------------------------------------------------------
def save_json(rows, context_dir, model_dir):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{context_dir}_{model_dir}_test_counts.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# Step 4: aggregate all rows to one CSV, summed per (model, context, instance)
# ---------------------------------------------------------------------------
def aggregate_to_csv(all_rows, out_path):
    totals = {}
    file_counts = {}
    for row in all_rows:
        key = (row["Model"], row["Context"], row["Instance"])
        totals[key] = totals.get(key, 0) + row["TestCount"]
        file_counts[key] = file_counts.get(key, 0) + 1

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Model", "Context", "Instance", "TestFileCount", "TotalTestCount"])
        for (model, context, instance), total in sorted(totals.items()):
            key = (model, context, instance)
            writer.writerow([model, context, instance, file_counts[key], total])


# ---------------------------------------------------------------------------
# Main: loop over every configured context x model, write JSON + final CSV
# ---------------------------------------------------------------------------
def main():
    all_rows = []
    for context_dir, context_label in CONTEXTS.items():
        context_path = BASE_DIR / context_dir
        if not context_path.exists():
            print(f"Skipping missing context folder: {context_path}")
            continue

        for model_dir, model_label in MODEL_DIR_ALIASES.items():
            model_path = context_path / model_dir
            if not model_path.exists():
                continue

            rows = parse_model_context_folder(model_path, model_label, context_label)
            json_path = save_json(rows, context_dir, model_dir)
            print(f"Wrote {len(rows)} rows -> {json_path}")
            all_rows.extend(rows)

    csv_path = OUTPUT_DIR / "test_count_aggregate.csv"
    aggregate_to_csv(all_rows, csv_path)
    print(f"Wrote aggregate CSV -> {csv_path}")


if __name__ == "__main__":
    # verify_paths()  # dry-run check, already confirmed correct
    main()
