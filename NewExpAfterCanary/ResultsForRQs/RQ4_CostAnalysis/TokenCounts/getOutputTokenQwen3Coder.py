#!/usr/bin/env python3
"""
Count tokens in generated prompt .txt files using the Qwen tokenizer, across
multiple experiments (context variants). Outputs a single CSV report
(next to this script) with per-file counts plus per (model, context_variant,
bump_id) and per (model, context_variant, test_file) aggregates.
"""

import csv
import sys
from pathlib import Path
from statistics import mean

from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

# === CONFIG ===
OUTPUT_BASE = PRIMARY_DRIVE / "FilteredDataset"

# Map each experiment folder -> the context variant it represents.
# Structure expected: OUTPUT_BASE / <exp_folder>/<model> / <bump_id> / *.txt
# (the model, e.g. Qwen3_480b_cloud, is already baked into the exp_folder path below)
EXPERIMENTS = {
    "Exp3LLMOutput/Qwen3_480b_cloud": "minimal",
    "Exp6LLMOutput/Qwen3_480b_cloud": "method",
    "Exp7LLMOutput/Qwen3_480b_cloud": "class",
}

# CSV is written into the same folder as this script.
CSV_REPORT_PATH = Path(__file__).resolve().parent / "OutputTokenCombined_Qwen.csv"

# Qwen tokenizer — using the one from the same family from HF.
QWEN_MODEL_NAME = "Qwen/Qwen3-Coder-480B-A35B-Instruct"


def get_encoder():
    try:
        return AutoTokenizer.from_pretrained(QWEN_MODEL_NAME, trust_remote_code=True)
    except Exception as e:
        print(f"[ERROR] Could not load tokenizer for {QWEN_MODEL_NAME}: {e}")
        print(f"[INFO]  Make sure you have: pip install transformers")
        print(f"[INFO]  You may also need: pip install tiktoken sentencepiece")
        raise


ENC = get_encoder()


def count_tokens(text: str) -> int:
    """Count tokens in text using the Qwen tokenizer."""
    if not text:
        return 0
    try:
        return len(ENC.encode(text))
    except Exception as e:
        print(f"[WARNING] Token encoding error: {e}")
        return len(text) // 4  # rough fallback


def process_bump(bump_dir: Path):
    """Return (rows, token_counts) for one bump directory."""
    rows = []
    token_counts = []

    txt_files = sorted(bump_dir.glob("*.txt"))
    if not txt_files:
        print(f"[WARNING] No .txt files found in {bump_dir}")
        return rows, token_counts

    for txt_file in txt_files:
        try:
            content = txt_file.read_text(encoding="utf-8")
        except Exception as e:
            print(f"[ERROR] Could not read {txt_file}: {e}")
            continue

        tokens = count_tokens(content)
        token_counts.append(tokens)
        rows.append({"file_name": txt_file.name, "output_token": tokens})

    return rows, token_counts


def main():
    if not OUTPUT_BASE.exists():
        print(f"[ERROR] Output base does not exist: {OUTPUT_BASE}")
        return

    detail_rows = []          # one row per .txt (= per test file)
    bump_summary = {}         # (model, variant, bump_id) -> stats
    test_file_summary = {}    # (model, variant, test_file) -> [token counts]
    max_tokens = 0

    for exp_folder, context_variant in EXPERIMENTS.items():
        exp_root = OUTPUT_BASE / exp_folder
        if not exp_root.exists():
            print(f"[WARNING] Experiment folder missing, skipping: {exp_root}")
            continue

        # The model name (e.g. Qwen3_480b_cloud) is the last component of exp_folder.
        model = exp_root.name

        # Each subfolder of the experiment is a bump_id, containing *.txt files.
        for bump_dir in sorted(p for p in exp_root.iterdir() if p.is_dir()):
            bump_id = bump_dir.name
            rows, token_counts = process_bump(bump_dir)
            if not token_counts:
                continue

            for r in rows:
                detail_rows.append({
                    "model": model,
                    "context_variant": context_variant,
                    "bump_id": bump_id,
                    "file_name": r["file_name"],
                    "output_token": r["output_token"],
                })
                test_file_summary.setdefault(
                    (model, context_variant, r["file_name"]), []
                ).append(r["output_token"])
                print(f"{model}/{context_variant}/{bump_id}/"
                      f"{r['file_name']}: {r['output_token']} tokens")

            max_tokens = max(max_tokens, max(token_counts))

            bump_summary[(model, context_variant, bump_id)] = {
                "count": len(token_counts),
                "min": min(token_counts),
                "max": max(token_counts),
                "avg": round(mean(token_counts), 2),
                "total": sum(token_counts),
            }

    if not detail_rows:
        print("[ERROR] No data to write to CSV")
        return

    fieldnames = ["model", "context_variant", "bump_id", "file_name", "output_token"]
    CSV_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(CSV_REPORT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(detail_rows)

        # --- Per (model, context_variant, bump_id) aggregates ---
        writer.writerow({})
        writer.writerow({"model": "PER-BUMP AGGREGATES"})
        writer.writerow({
            "model": "model", "context_variant": "context_variant",
            "bump_id": "bump_id", "file_name": "metric", "output_token": "value",
        })
        for (model, variant, bump_id), stats in sorted(bump_summary.items()):
            writer.writerow({
                "model": model, "context_variant": variant, "bump_id": bump_id,
                "file_name": "num_files", "output_token": stats["count"],
            })
            writer.writerow({
                "model": model, "context_variant": variant, "bump_id": bump_id,
                "file_name": "min/max/avg/total",
                "output_token": f"{stats['min']}/{stats['max']}/"
                               f"{stats['avg']}/{stats['total']}",
            })

        # --- Per (model, context_variant, test_file) aggregates ---
        writer.writerow({})
        writer.writerow({"model": "PER-TEST-FILE AGGREGATES"})
        writer.writerow({
            "model": "model", "context_variant": "context_variant",
            "bump_id": "test_file", "file_name": "num_occurrences",
            "output_token": "min/max/avg/total",
        })
        for (model, variant, test_file), counts in sorted(test_file_summary.items()):
            writer.writerow({
                "model": model,
                "context_variant": variant,
                "bump_id": test_file,
                "file_name": len(counts),
                "output_token": f"{min(counts)}/{max(counts)}/"
                               f"{round(mean(counts), 2)}/{sum(counts)}",
            })

    print(f"\n[REPORT] Saved to: {CSV_REPORT_PATH}")
    print(f"[RESULT] Maximum token count across all prompts: {max_tokens}")
    print(f"[RESULT] Total files processed: {len(detail_rows)}")
    print(f"[RESULT] Per-bump groups: {len(bump_summary)}")
    print(f"[RESULT] Per-test-file groups: {len(test_file_summary)}")


if __name__ == "__main__":
    main()
