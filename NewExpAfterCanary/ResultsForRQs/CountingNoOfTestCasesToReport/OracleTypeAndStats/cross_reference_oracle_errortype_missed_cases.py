"""
For every MISSED (H2 hypothesis: the broken API's class was loaded but the
LLM-generated test still passed) instance in oracle_types_missed_h2.json,
reports which oracle/assert methods it used, plus the exception types
involved:
  - bump_bc_errors      : the ground-truth exception types the breaking change
                          actually produces (from BUMP), looked up by
                          custom_id alone - it's a per-breaking-change
                          property, not per (model, context_variant).
  
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

BUMP_CSV = PRIMARY_DRIVE / "RQResultsForPaper/RQ2/BUMPErrorLogs/RQ4_resultsBUMP.csv"

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "missed_cases"
ORACLE_JSON = OUTPUT_DIR / "oracle_types_missed_h2.json"

INSTANCE_DETAIL_OUT = OUTPUT_DIR / "instance_oracle_vs_errortypes_missed_cases.json"
SUMMARY_OUT = OUTPUT_DIR / "method_instance_contribution_summary_missed_cases.txt"

_MAVEN_ONLY = {"MojoFailureException", "MojoExecutionException"}


def clean_bump_errors(exception_types_str):
    """Mirrors ErrorExtractor.extract_bump_errors in
    RQ2.2_ErrorTypComp/Exp7ClassErrorTypeComp.py."""
    if not exception_types_str or str(exception_types_str).strip() in ("", "nan"):
        return set()
    cleaned = set()
    for error in str(exception_types_str).split("|"):
        error = error.strip()
        if not error or error in _MAVEN_ONLY:
            continue
        cleaned.add(error.rsplit(".", 1)[-1])
    return cleaned


def load_bump_lookup(csv_path):
    df = pd.read_csv(csv_path)
    lookup = {}
    for _, row in df.iterrows():
        errors = clean_bump_errors(row["exception_types"])
        lookup[str(row["custom_id"]).strip()] = "|".join(sorted(errors)) if errors else None
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
    bump_lookup = load_bump_lookup(BUMP_CSV)

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

                if instance not in bump_lookup:
                    not_found.append((context, model, instance))

                instance_detail[context][model][instance] = {
                    "oracle_methods_used": methods,
                    "assert_calls": assert_calls_in_instance(instance_data),
                    "bump_bc_errors": bump_lookup.get(instance),
                    "llm_detected_errors": None,
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
        print(f"{len(not_found)} instances had no matching row in {BUMP_CSV.name}:")
        for entry in not_found:
            print(f"  {entry}")


if __name__ == "__main__":
    main()
