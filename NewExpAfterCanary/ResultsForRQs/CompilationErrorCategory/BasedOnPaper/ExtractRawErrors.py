"""
Compilation Error Extractor (for manual/human categorization)
- Extracts the raw STDERR block + error count from each failed test's
  compile log — no regex-based category guessing.
- One CSV per model; one row per failing test; a blank "human_category"
  column is left for manual labeling later.
"""

import re
import json
import csv
import os
from pathlib import Path
from typing import Dict, List, Tuple

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

ERROR_LINE_RE   = re.compile(r"^.*?:\d+: error:", re.MULTILINE)
STDERR_BLOCK_RE = re.compile(r"=== STDERR ===\s*\n(.*?)\n\[RESULT\]", re.DOTALL)
SUMMARY_CNT_RE  = re.compile(r"^(\d+)\s+errors?\s*$", re.MULTILINE)
PACKAGE_RE      = re.compile(r"^Package:\s*(.+)$", re.MULTILINE)

# Excel caps a cell at 32,767 chars; a few logs (malformed/minified generated
# Java) have a single source line that size — truncate and point back at
# log_path rather than silently losing the tail in Excel/Sheets.
STDERR_MAX_CHARS = 30000


def extract_log_info(log_path: Path) -> Dict:
    result = {
        "instance": "",
        "test_name": "",
        "package": "",
        "error_count": 0,
        "stderr": "",
        "log_path": str(log_path),
    }

    # Filename: BBC01_BBC01U2Test.java_compile.log
    stem = log_path.name.replace("_compile.log", "")
    parts = stem.split("_", 1)
    if len(parts) == 2:
        result["instance"], result["test_name"] = parts

    try:
        log_content = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        result["stderr"] = "READ_ERROR"
        return result

    pkg_match = PACKAGE_RE.search(log_content)
    if pkg_match:
        result["package"] = pkg_match.group(1).strip()

    stderr_match = STDERR_BLOCK_RE.search(log_content)
    if stderr_match:
        result["stderr"] = stderr_match.group(1).strip()
    else:
        # Fallback: process likely crashed before writing the [RESULT] marker
        idx = log_content.find("=== STDERR ===")
        if idx != -1:
            result["stderr"] = log_content[idx + len("=== STDERR ==="):].strip()

    # Prefer javac's own "N error(s)" summary line; fall back to counting
    # "<file>:<line>: error:" occurrences if that line is missing (crash/truncation).
    summary_match = SUMMARY_CNT_RE.search(log_content)
    if summary_match:
        result["error_count"] = int(summary_match.group(1))
    else:
        result["error_count"] = len(ERROR_LINE_RE.findall(log_content))

    if len(result["stderr"]) > STDERR_MAX_CHARS:
        result["stderr"] = (
            result["stderr"][:STDERR_MAX_CHARS]
            + f"\n...[TRUNCATED, see log_path for full text]"
        )

    return result


# ============================================================
# Multi-model / multi-variant orchestration
# ============================================================

def load_compilation_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def process_variant(
    logs_dir: str,
    compilation_json: str,
    variant_tag: str,
) -> Tuple[List[Dict], Dict]:

    data = load_compilation_json(compilation_json)
    compile_results = data.get("compilation_results", {})

    total_generated = total_compiled = total_failed = 0
    failed_tests = []

    for instance, res in compile_results.items():
        file_counts      = res.get("file_counts", {})
        total_generated += file_counts.get("files_generated", 0)
        total_compiled  += file_counts.get("files_compiled", 0)
        for test_name in res.get("failed", {}):
            total_failed += 1
            failed_tests.append({"instance": instance, "test_name": test_name})

    print(f"  [{variant_tag}] generated={total_generated}  "
          f"compiled={total_compiled}  failed={total_failed}")

    logs_path = Path(logs_dir)
    all_results = []
    missing_logs = 0

    for ft in failed_tests:
        log_path = logs_path / f"{ft['instance']}_{ft['test_name']}_compile.log"
        if not log_path.exists():
            missing_logs += 1
            continue
        r = extract_log_info(log_path)
        r["variant"] = variant_tag
        all_results.append(r)

    if missing_logs:
        print(f"    NOTE: {missing_logs} failed tests had no compile log "
              f"(mostly error_category == 'no_code' in the compilation JSON — "
              f"LLM output had no parseable Java, so the compiler never ran — excluded here)")

    summary = {
        "variant": variant_tag,
        "total_generated": total_generated,
        "total_compiled": total_compiled,
        "total_failed": total_failed,
        "compilation_rate_pct": round(total_compiled / max(total_generated, 1) * 100, 1),
        "total_errors_in_failed": sum(r["error_count"] for r in all_results),
    }
    return all_results, summary


# ============================================================
# CSV writer
# ============================================================

def write_model_csv(
    model_tag: str,
    variants_data: Dict[str, Tuple[List[Dict], Dict]],
    output_path: str,
):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    fieldnames = [
        "context_variant", "instance", "test_file", "package",
        "error_count", "stderr", "human_category", "log_path",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for vt, (results, _smry) in variants_data.items():
            for r in results:
                w.writerow({
                    "context_variant": vt,
                    "instance":        r["instance"],
                    "test_file":       r["test_name"],
                    "package":         r["package"],
                    "error_count":     r["error_count"],
                    "stderr":          r["stderr"],
                    "human_category":  "",
                    "log_path":        r["log_path"],
                })

        w.writerow({})
        w.writerow({"context_variant": f"=== SUMMARY: {model_tag} ==="})
        for vt, (_, smry) in variants_data.items():
            w.writerow({
                "context_variant": vt,
                "instance": (
                    f"generated={smry['total_generated']}  "
                    f"compiled={smry['total_compiled']}  "
                    f"failed={smry['total_failed']}  "
                    f"compile_rate={smry['compilation_rate_pct']}%  "
                    f"total_errors={smry['total_errors_in_failed']}"
                ),
            })

    print(f"  CSV -> {output_path}")


# ============================================================
# Config
# ============================================================

MODELS = {
    "GPT4o": {
        "Class": {
            "logs_dir":         PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/compile_results_pre.json",
        },
        "Method": {
            "logs_dir":         PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/pre/compile_results_pre.json",
        },
        "Minimal": {
            "logs_dir":         PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/compile_results_pre.json",
        },
    },
    "Qwen-480B": {
        "Class": {
            "logs_dir":         PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/compile_results_pre.json",
        },
        "Method": {
            "logs_dir":         PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/compile_results_pre.json",
        },
        "Minimal": {
            "logs_dir":         PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/compile_results_pre.json",
        },
    },
    "GPTOSS-120B": {
        "Class": {
            "logs_dir":         PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/compile_results_pre.json",
        },
        "Method": {
            "logs_dir":         PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/compile_results_pre.json",
        },
        "Minimal": {
            "logs_dir":         PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/compile_results_pre.json",
        },
    },
}

OUTPUT_DIR = PRIMARY_DRIVE / "RQResultsForPaper/CompilErrorAnalysisResults/RawForHumanReview"


if __name__ == "__main__":
    for model_tag, variants_cfg in MODELS.items():
        print(f"\nProcessing model: {model_tag}")
        variants_data = {}

        for vt, cfg in variants_cfg.items():
            print(f"  Variant: {vt}")
            results, summary = process_variant(
                logs_dir=cfg["logs_dir"],
                compilation_json=cfg["compilation_json"],
                variant_tag=vt,
            )
            variants_data[vt] = (results, summary)

        out_csv = os.path.join(OUTPUT_DIR, f"raw_compilation_errors_{model_tag}.csv")
        write_model_csv(model_tag, variants_data, out_csv)

    print(f"\nDone. Outputs in: {OUTPUT_DIR}")
