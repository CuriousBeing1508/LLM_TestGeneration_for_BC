"""
Flattens instance_oracle_vs_errortypes_success_cases.json (produced by
cross_reference_oracle_errortype_success_cases.py) into a CSV: one row per
(context, model, instance), with oracle methods used and the
BUMP vs LLM-detected exception types side by side.
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicate_extractor import CALL_SEP, format_call

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "success_cases"
INPUT_JSON = OUTPUT_DIR / "instance_oracle_vs_errortypes_success_cases.json"
OUTPUT_CSV = OUTPUT_DIR / "instance_oracle_vs_errortypes_success_cases.csv"


def main():
    data = json.load(open(INPUT_JSON, encoding="utf-8"))

    rows = []
    for context, models in data.items():
        for model, instances in models.items():
            for instance, detail in instances.items():
                rows.append({
                    "context_variant": context,
                    "model": model,
                    "instance": instance,
                    "oracle_methods_used": "|".join(detail["oracle_methods_used"]),
                    "assert_calls": CALL_SEP.join(
                        format_call(c) for c in detail.get("assert_calls", [])
                    ),
                    "bump_bc_errors": detail["bump_bc_errors"] or "",
                    "llm_detected_errors": detail["llm_detected_errors"] or "",
                })

    rows.sort(key=lambda r: (r["context_variant"], r["model"], r["instance"]))

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "context_variant", "model", "instance",
            "oracle_methods_used", "assert_calls", "bump_bc_errors", "llm_detected_errors",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
