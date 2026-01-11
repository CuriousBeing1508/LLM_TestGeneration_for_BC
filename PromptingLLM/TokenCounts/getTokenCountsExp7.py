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
OUTPUT_ROOT = Path("/Volumes/RachnaPSSD/FilteredDataset/Exp6LLMOutput/GPT4o")
CSV_REPORT_PATH = OUTPUT_ROOT.parent / "OutputTokenExp6.csv"

# Choose encoding for GPT-4o
def get_encoder():
    try:
        # GPT-4o uses o200k_base encoding
        return tiktoken.get_encoding("o200k_base")
    except Exception:
        try:
            return tiktoken.encoding_for_model("gpt-4")
        except Exception:
            return tiktoken.get_encoding("cl100k_base")

ENC = get_encoder()

def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken."""
    if not text:
        return 0
    try:
        return len(ENC.encode(text, disallowed_special=()))
    except Exception as e:
        print(f"[WARNING] Token encoding error: {e}")
        # Fallback: rough estimate
        return len(text) // 4

def main():
    if not OUTPUT_ROOT.exists():
        print(f"[ERROR] Output root does not exist: {OUTPUT_ROOT}")
        return

    rows = []
    bump_summary = {}
    max_tokens = 0

    for bump_dir in sorted(OUTPUT_ROOT.iterdir()):
        if not bump_dir.is_dir():
            continue
        bump_id = bump_dir.name
        token_counts = []
        
        txt_files = list(bump_dir.glob("*.txt"))
        if not txt_files:
            print(f"[WARNING] No .txt files found in {bump_dir}")
            continue

        for txt_file in sorted(txt_files):
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

    if not rows:
        print("[ERROR] No data to write to CSV")
        return

    # Write CSV report
    CSV_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    with open(CSV_REPORT_PATH, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["bump_id", "file_name", "token_count"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

        # Add per-bump aggregates at the end
        writer.writerow({})
        writer.writerow({"bump_id": "AGGREGATES"})
        writer.writerow({"bump_id": "bump_id", "file_name": "metric", "token_count": "value"})
        for bump_id, stats in sorted(bump_summary.items()):
            writer.writerow({
                "bump_id": bump_id,
                "file_name": "num_files",
                "token_count": stats['count']
            })
            writer.writerow({
                "bump_id": bump_id,
                "file_name": "min/max/avg/total",
                "token_count": f"{stats['min']}/{stats['max']}/{stats['avg']}/{stats['total']}"
            })

    print(f"\n[REPORT] Saved to: {CSV_REPORT_PATH}")
    print(f"[RESULT] Maximum token count across all prompts: {max_tokens}")
    print(f"[RESULT] Total files processed: {len(rows)}")
    print(f"[RESULT] Total bumps processed: {len(bump_summary)}")

if __name__ == "__main__":
    main()