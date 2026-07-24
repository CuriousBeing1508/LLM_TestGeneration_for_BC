"""
Oracle type per test file / test case, for every DETECTED instance (i.e. the
same population as cross_reference_oracle_errortype_success_cases.py).

One row per (context_variant, model, instance, test_file, test_case) — finer
grain than export_instance_oracle_vs_errortypes_success_cases_csv.py, which
collapses all of an instance's test files/cases into one row.

Source: oracle_types_detected_success.json (built by fetch_oracle_type_for_success.py
from AssertAnalysisResults/<context>/<model>/detected_bre_details.json via
tree-sitter). Not re-derived here — just flattened to a finer grain.
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicate_extractor import CALL_SEP, format_call

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "success_cases"
INPUT_JSON = OUTPUT_DIR / "oracle_types_detected_success.json"
OUTPUT_CSV = OUTPUT_DIR / "oracle_type_per_testcase_detected.csv"


def main():
    data = json.load(open(INPUT_JSON, encoding="utf-8"))

    rows = []
    for context, models in data.items():
        for model, instances in models.items():
            for instance, test_files in instances.items():
                for test_file, test_cases in test_files.items():
                    for test_case, detail in test_cases.items():
                        calls = detail.get("assert_calls", [])
                        methods_used = sorted({c["method"] for c in calls})
                        rows.append({
                            "context_variant": context,
                            "model": model,
                            "instance": instance,
                            "test_file": test_file,
                            "test_case": test_case,
                            "num_assert_calls": len(calls),
                            "oracle_methods_used": "|".join(methods_used),
                            "has_trycatch_assert": any(c.get("in_trycatch") for c in calls),
                            "assert_calls": CALL_SEP.join(format_call(c) for c in calls),
                            "note": detail.get("note", ""),
                        })

    rows.sort(key=lambda r: (r["context_variant"], r["model"], r["instance"], r["test_file"], r["test_case"]))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "context_variant", "model", "instance", "test_file", "test_case",
            "num_assert_calls", "oracle_methods_used", "has_trycatch_assert",
            "assert_calls", "note",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
