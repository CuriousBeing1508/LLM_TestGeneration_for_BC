"""
For every DETECTED instance (from oracle_types_detected_success.json), reports
which oracle/assert methods it used, plus the exception types involved:
  - bump_bc_errors      : the ground-truth exception types the breaking change
                          actually produces (from BUMP).
  - llm_detected_errors : the exception types the LLM-generated test actually
                          caught/reported.
Cross-referenced from detected_bc_errortype_coverage.csv (columns Bump_bc_errors,
llm_detected_errors), joined on (model, context_variant, custom_id).

Also writes an aggregate summary: for each (context, model) pair, how many
distinct instances used each oracle/assert method at least once.

Each instance's detail also carries "assert_calls": the flat list of every
assert call in that instance (method, predicate, message, line), reusing the
predicate/message already extracted by predicate_extractor.py.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

ERRORTYPE_CSV = PRIMARY_DRIVE / "RQResultsForPaper/RQ2/detected_bc_errortype_coverage.csv"
OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "success_cases"
ORACLE_JSON = OUTPUT_DIR / "oracle_types_detected_success.json"

INSTANCE_DETAIL_OUT = OUTPUT_DIR / "instance_oracle_vs_errortypes_success_cases.json"
SUMMARY_OUT = OUTPUT_DIR / "method_instance_contribution_summary_successful_cases.txt"

# detected_bc_errortype_coverage.csv model labels -> labels used in oracle_types_detected_success.json
MODEL_LABELS = {
    "GPT-4o": "GPT4o",
    "GPT-OSS-120b": "GPTOSS",
    "Qwen-480B": "Qwen3-coder",
}


def load_errortype_lookup(csv_path):
    df = pd.read_csv(csv_path)
    df["model"] = df["model"].map(MODEL_LABELS)
    lookup = {}
    for _, row in df.iterrows():
        key = (row["context_variant"], row["model"], str(row["custom_id"]).strip())
        lookup[key] = {
            "bump_bc_errors": None if pd.isna(row["bump_bc_errors"]) else row["bump_bc_errors"],
            "llm_detected_errors": None if pd.isna(row["llm_detected_errors"]) else row["llm_detected_errors"],
        }
    return lookup


def methods_used_in_instance(instance_data):
    """Union of assert method names used anywhere in this instance's test files."""
    methods = set()
    for test_file in instance_data.values():
        for test_method in test_file.values():
            for call in test_method.get("assert_calls", []):
                methods.add(call["method"])
    return sorted(methods)


def assert_calls_in_instance(instance_data):
    """Flat list of every assert call in this instance, each with its
    already-extracted predicate/message (see predicate_extractor.py)."""
    calls = []
    for test_file in instance_data.values():
        for test_method in test_file.values():
            for call in test_method.get("assert_calls", []):
                calls.append({
                    "method": call["method"],
                    "predicate": call.get("predicate"),
                    "message": call.get("message"),
                    "line": call.get("line"),
                })
    return calls


def main():
    oracle_data = json.load(open(ORACLE_JSON, encoding="utf-8"))
    errortype_lookup = load_errortype_lookup(ERRORTYPE_CSV)

    instance_detail = {}
    # (context, model) -> method -> set of instances
    contribution = defaultdict(lambda: defaultdict(set))
    not_found = []

    for context, models in oracle_data.items():
        instance_detail.setdefault(context, {})
        for model, instances in models.items():
            instance_detail[context].setdefault(model, {})
            for instance, instance_data in instances.items():
                methods = methods_used_in_instance(instance_data)
                for m in methods:
                    contribution[(context, model)][m].add(instance)

                errortypes = errortype_lookup.get((context, model, instance))
                if errortypes is None:
                    not_found.append((context, model, instance))
                    errortypes = {"bump_bc_errors": None, "llm_detected_errors": None}

                instance_detail[context][model][instance] = {
                    "oracle_methods_used": methods,
                    "assert_calls": assert_calls_in_instance(instance_data),
                    **errortypes,
                }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(INSTANCE_DETAIL_OUT, "w", encoding="utf-8") as f:
        json.dump(instance_detail, f, indent=2)
    print(f"Wrote {INSTANCE_DETAIL_OUT}")

    with open(SUMMARY_OUT, "w", encoding="utf-8") as f:
        for (context, model), method_map in sorted(contribution.items()):
            f.write(f"=== {context} / {model} ===\n")
            for method, instances in sorted(method_map.items(), key=lambda kv: -len(kv[1])):
                f.write(f"  {method}: {len(instances)} instances\n")
            f.write("\n")
    print(f"Wrote {SUMMARY_OUT}")

    if not_found:
        print(f"{len(not_found)} instances had no matching row in {ERRORTYPE_CSV.name}:")
        for entry in not_found:
            print(f"  {entry}")


if __name__ == "__main__":
    main()
