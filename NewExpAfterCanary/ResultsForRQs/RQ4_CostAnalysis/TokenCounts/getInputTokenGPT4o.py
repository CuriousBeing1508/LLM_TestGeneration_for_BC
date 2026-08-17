#!/usr/bin/env python3
"""
Count tokens in the original prompt .txt files (the LLM input) using tiktoken,
across the minimal/method/class context variants. Adds an `input_token` column
to the existing OutputTokenCombined.csv (produced by getOutputTokenGPT4o.py),
joining on (context_variant, bump_id, file_name).
"""

import csv
import sys
from pathlib import Path

import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

# === CONFIG ===
OUTPUT_BASE = PRIMARY_DRIVE / "FilteredDataset"

# Prompt (LLM input) folders -> the context variant it represents.
# Structure: OUTPUT_BASE / <exp_folder> / <bump_id> / *.txt
EXPERIMENTS = {
    "Exp3Prompts": "minimal",
    "Exp6Prompts": "method",
    "Exp7Prompts": "class",
}

MODEL = "GPT4o"
CSV_REPORT_PATH = Path(__file__).resolve().parent / "OutputTokenCombined.csv"

ENC = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    """Count tokens in text using the GPT-4o tiktoken encoder."""
    if not text:
        return 0
    try:
        return len(ENC.encode(text, disallowed_special=()))
    except Exception as e:
        print(f"[WARNING] Token encoding error: {e}")
        return len(text) // 4  # rough fallback


def build_input_token_map():
    """Return {(context_variant, bump_id, file_name): input_token} for every prompt file."""
    input_tokens = {}
    for exp_folder, context_variant in EXPERIMENTS.items():
        exp_root = OUTPUT_BASE / exp_folder
        if not exp_root.exists():
            print(f"[WARNING] Prompt folder missing, skipping: {exp_root}")
            continue

        for bump_dir in sorted(p for p in exp_root.iterdir() if p.is_dir()):
            bump_id = bump_dir.name
            for txt_file in sorted(bump_dir.glob("*.txt")):
                try:
                    content = txt_file.read_text(encoding="utf-8")
                except Exception as e:
                    print(f"[ERROR] Could not read {txt_file}: {e}")
                    continue

                tokens = count_tokens(content)
                input_tokens[(context_variant, bump_id, txt_file.name)] = tokens
                print(f"{MODEL}/{context_variant}/{bump_id}/{txt_file.name}: {tokens} input tokens")

    return input_tokens


def main():
    if not CSV_REPORT_PATH.exists():
        print(f"[ERROR] CSV report not found: {CSV_REPORT_PATH}")
        print("[INFO]  Run getOutputTokenGPT4o.py first.")
        return

    input_tokens = build_input_token_map()
    if not input_tokens:
        print("[ERROR] No input prompt files found")
        return

    # --- Read the existing CSV, splitting detail rows from the aggregate sections ---
    with open(CSV_REPORT_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        detail_rows = []
        rest_rows = []
        in_detail = True
        for row in reader:
            if in_detail and not any(row):
                in_detail = False
            (detail_rows if in_detail else rest_rows).append(row)

    if "input_token" in header:
        input_col_idx = header.index("input_token")
    else:
        header.append("input_token")
        input_col_idx = len(header) - 1

    variant_idx = header.index("context_variant")
    bump_idx = header.index("bump_id")
    file_idx = header.index("file_name")

    missing = 0
    new_detail_rows = []
    for row in detail_rows:
        row = row + [""] * (len(header) - len(row))
        key = (row[variant_idx], row[bump_idx], row[file_idx])
        tokens = input_tokens.get(key)
        if tokens is None:
            missing += 1
            tokens = ""
        row[input_col_idx] = tokens
        new_detail_rows.append(row)

    new_rest_rows = [row + [""] * (len(header) - len(row)) if row else row for row in rest_rows]

    with open(CSV_REPORT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(new_detail_rows)
        writer.writerows(new_rest_rows)

    if missing:
        print(f"[WARNING] {missing} detail rows had no matching prompt file (input_token left blank)")

    print(f"\n[REPORT] Updated: {CSV_REPORT_PATH}")
    print(f"[RESULT] Prompt files processed: {len(input_tokens)}")
    print(f"[RESULT] Detail rows matched: {len(new_detail_rows) - missing}/{len(new_detail_rows)}")


if __name__ == "__main__":
    main()
