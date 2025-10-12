#!/usr/bin/env python3
"""
Count tokens in generated prompt .txt files using tiktoken.
Outputs a CSV report with counts per bump_id and per file,
plus per-bump aggregates and the overall maximum token count.
"""

import csv
from pathlib import Path
import tiktoken
from statistics import mean

# === CONFIG ===
OUTPUT_ROOT = Path("/Volumes/Rachna-HD/GeneratedPromptsClientsExp7")
CSV_REPORT_PATH = OUTPUT_ROOT.parent / "PromptTokenReportExp7.csv"

# choose encoding for GPT-4o with fallbacks
def get_encoder():
    try:
        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        try:
            return tiktoken.get_encoding("o200k_base")
        except Exception:
            return tiktoken.get_encoding("cl100k_base")

ENC = get_encoder()

def count_tokens(text: str) -> int:
    return len(ENC.encode(text))

def main():
    rows = []
    bump_summary = {}
    max_tokens = 0

    for bump_dir in sorted(OUTPUT_ROOT.iterdir()):
        if not bump_dir.is_dir():
            continue
        bump_id = bump_dir.name
        token_counts = []
        for txt_file in sorted(bump_dir.glob("*.txt")):
            try:
                content = txt_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[ERROR] Could not read {txt_file}: {e}")
                continue

            tokens = count_tokens(content)
            token_counts.append(tokens)
            max_tokens = max(max_tokens, tokens)

            rows.append({
                "bump_id": bump_id,
                "file_name": txt_file.name,
                "token_count": tokens
            })
            print(f"{bump_id}/{txt_file.name}: {tokens} tokens")

        if token_counts:
            bump_summary[bump_id] = {
                "count": len(token_counts),
                "min": min(token_counts),
                "max": max(token_counts),
                "avg": round(mean(token_counts), 2),
                "total": sum(token_counts)
            }

    # Write CSV report
    with open(CSV_REPORT_PATH, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["bump_id", "file_name", "token_count"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

        # Add per-bump aggregates at the end
        writer.writerow({})
        writer.writerow({"bump_id": "AGGREGATES"})
        writer.writerow({"bump_id": "bump_id", "file_name": "num_files/min/max/avg/total"})
        for bump_id, stats in bump_summary.items():
            writer.writerow({
                "bump_id": bump_id,
                "file_name": f"{stats['count']} files",
                "token_count": f"min={stats['min']}, max={stats['max']}, avg={stats['avg']}, total={stats['total']}"
            })

    print(f"\n[REPORT] {CSV_REPORT_PATH}")
    print(f"[RESULT] Maximum token count across all prompts: {max_tokens}")

if __name__ == "__main__":
    main()
