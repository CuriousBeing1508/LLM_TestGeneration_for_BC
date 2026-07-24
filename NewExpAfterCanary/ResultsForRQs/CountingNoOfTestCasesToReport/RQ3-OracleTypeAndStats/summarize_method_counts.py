"""
Walks an oracle_types_*.json output (from fetch_oracle_type_for_success.py /
fetch_oracle_type_for_missed.py) and counts how many times each assert method
name occurs across all assert_calls, then writes a sorted summary to a .txt file.
"""
import json
from collections import Counter
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

INPUT_FILES = [
    OUTPUT_DIR / "success_cases" / "oracle_types_detected_success.json",
    OUTPUT_DIR / "missed_cases" / "oracle_types_missed_h2.json",
]


def count_methods(data):
    counts = Counter()
    for context in data.values():
        for model in context.values():
            for instance in model.values():
                for test_file in instance.values():
                    for test_method in test_file.values():
                        for call in test_method.get("assert_calls", []):
                            counts[call["method"]] += 1
    return counts


def write_summary(counts, out_path):
    total = sum(counts.values())
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Total assert calls: {total}\n")
        f.write(f"Distinct methods: {len(counts)}\n\n")
        for method, n in counts.most_common():
            f.write(f"{method}: {n}\n")


def main():
    for input_path in INPUT_FILES:
        if not input_path.exists():
            print(f"Skipping missing input: {input_path}")
            continue
        data = json.load(open(input_path, encoding="utf-8"))
        counts = count_methods(data)
        out_path = input_path.parent / f"{input_path.stem}_method_counts.txt"
        write_summary(counts, out_path)
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
