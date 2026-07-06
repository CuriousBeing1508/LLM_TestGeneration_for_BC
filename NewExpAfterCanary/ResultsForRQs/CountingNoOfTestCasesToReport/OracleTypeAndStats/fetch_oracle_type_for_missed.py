"""
For every UNDETECTED (i.e. missed the breaking change) test file where the broken
API's class was nonetheless loaded/exercised, reports the raw assertion data for
each test method/case: which assert method was called, its exact call text, and
whether it's inside a try/catch.


Source of "which files count": investigate_undetected/investigation_<model>_<variant>.csv

Source of raw assertion data: AssertAnalysisResults/<context>/<model>/
{compiled_pre,executed_pre}_details.json (same tree-sitter extraction is used )
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicate_extractor import extract_predicate_and_message, resolve_call_text

ASSERT_BASE = PRIMARY_DRIVE / "AssertAnalysisResults"

# (model, context_variant) -> investigation CSV path, per investigate_undetected/*.csv
CSV_CONFIG = {
    ("GPT-4o", "Minimal"): PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/investigate_undetected/investigation_GPT-4o_Minimal.csv",
    ("GPT-4o", "Method"): PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/investigate_undetected/investigation_GPT-4o_Method.csv",
    ("GPT-4o", "Class"): PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/investigate_undetected/investigation_GPT-4o_Class.csv",
    ("Qwen-480B", "Minimal"): PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/investigate_undetected/investigation_Qwen-480B_Minimal.csv",
    ("Qwen-480B", "Method"): PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/investigate_undetected/investigation_Qwen-480B_Method.csv",
    ("Qwen-480B", "Class"): PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/investigate_undetected/investigation_Qwen-480B_Class.csv",
    ("GPTOSS-120B", "Minimal"): PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/investigate_undetected/investigation_GPTOSS-120B_Minimal.csv",
    ("GPTOSS-120B", "Method"): PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/investigate_undetected/investigation_GPTOSS-120B_Method.csv",
    ("GPTOSS-120B", "Class"): PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/investigate_undetected/investigation_GPTOSS-120B_Class.csv",
}

# context_variant in the investigation CSVs already matches the labels used on the
# detected/success side (Minimal/Method/Class). Model names need remapping to match.
MODEL_LABELS = {
    "GPT-4o": "GPT4o",
    "Qwen-480B": "Qwen3-coder",
    "GPTOSS-120B": "GPTOSS",
}
CONTEXT_DIRS = {
    "Minimal": "Exp3LLMOutput",
    "Method": "Exp6LLMOutput",
    "Class": "Exp7LLMOutput",
}
MODEL_DIRS = {
    "GPT-4o": "GPT4o",
    "Qwen-480B": "Qwen_480b_cloud",
    "GPTOSS-120B": "GPT_OSS_120b",
}

OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "missed_cases" / "oracle_types_missed_h2.json"

# I used to call the case "API call sight class called/not called as hypothesis so named it H1 : Not loaded, H2: class loaded"
def load_h2_rows(csv_path):
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()
    df["hypothesis"] = df["hypothesis"].astype(str).str.strip().str.upper()
    df["custom_id"] = df["custom_id"].astype(str).str.strip()
    df["java_file"] = df["java_file"].astype(str).str.strip()
    h2 = df[df["hypothesis"] == "H2"][["custom_id", "java_file"]].drop_duplicates()
    return list(h2.itertuples(index=False, name=None))


def load_file_index(context_variant, model):
    """(instance, java_file) -> file_record, pooling compiled_pre + executed_pre
    (both contain the same tree-sitter assert extraction; a file only needs to be
    in one of them since H2 files necessarily compiled and ran)."""
    context_dir = CONTEXT_DIRS[context_variant]
    model_dir = MODEL_DIRS[model]
    index = {}
    for fname in ("compiled_pre_details.json", "executed_pre_details.json"):
        path = ASSERT_BASE / context_dir / model_dir / fname
        if not path.exists():
            continue
        for record in json.load(open(path, encoding="utf-8")):
            parts = Path(record["path"]).parts
            instance = parts[parts.index("_java_files") - 1]
            index.setdefault((instance, record["file"]), record)
    return index


def build_test_method_entry(test_method, java_file_path):
    assert_calls = test_method["assert_calls"]
    if not assert_calls:
        return {
            "assert_calls": [],
            "note": "no explicit assertion in this test method",
        }

    entries = []
    for call in assert_calls:
        call_text, truncated = resolve_call_text(call, java_file_path)
        predicate, message = extract_predicate_and_message(call["method"], call.get("framework"), call_text)
        entry = {
            "method": call["method"],
            "call_text": call_text,
            "predicate": predicate,
            "message": message,
            "line": call["line"],
            "in_trycatch": call["in_trycatch"],
            "framework": call.get("framework"),
            "source": call.get("source"),
        }
        if truncated:
            entry["call_text_truncated"] = True
        entries.append(entry)

    return {"assert_calls": entries}


def build_test_file_entry(file_record):
    return {
        test_method["name"]: build_test_method_entry(test_method, file_record["path"])
        for test_method in file_record["test_methods"]
    }


def main():
    result = {}
    not_found = []

    for (model, context_variant), csv_path in CSV_CONFIG.items():
        if not Path(csv_path).exists():
            print(f"Skipping missing investigation CSV: {csv_path}")
            continue

        h2_pairs = load_h2_rows(csv_path)
        file_index = load_file_index(context_variant, model)
        model_label = MODEL_LABELS[model]

        result.setdefault(context_variant, {}).setdefault(model_label, {})

        for instance, java_file in h2_pairs:
            record = file_index.get((instance, java_file))
            if record is None:
                not_found.append((context_variant, model, instance, java_file))
                continue
            result[context_variant][model_label].setdefault(instance, {})[java_file] = \
                build_test_file_entry(record)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"Wrote {OUTPUT_PATH}")
    if not_found:
        print(f"{len(not_found)} (instance, file) pairs not found in assertion data:")
        for entry in not_found:
            print(f"  {entry}")


if __name__ == "__main__":
    main()
