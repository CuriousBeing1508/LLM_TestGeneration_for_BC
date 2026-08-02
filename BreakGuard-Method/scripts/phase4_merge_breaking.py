#!/usr/bin/env python3
"""BREAKING merge: combine the single-module and multi-module BREAKING-stage
results into one final transplant_results_breaking.json per model.

execute_breaking_single_module.py and execute_breaking_multi_module.py each
process a disjoint set of instances (split by project type) and save their
own incrementally-resumable output file. This script does not re-run
anything - it just merges those two files into one combined result, the
same way phase3_merge_pre.py merges compile+execute into one PRE result.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cli import parse_common_args

_args, _paths = parse_common_args(
    "BREAKING merge: combine single-module + multi-module breaking results into one file"
)

SINGLE_INPUT = _paths["breaking_single_output"]
MULTI_INPUT = _paths["breaking_multi_output"]
FINAL_OUTPUT = _paths["breaking_output"]

SUMMARY_KEYS = [
    "total_pass",
    "total_fail",
    "compilation_errors",
    "test_failures_breaking_change",
    "build_failures_without_test_execution",
    "transplant_issues",
    "processed",
    "skipped",
]


def _load(path):
    if not path.exists():
        print(f"[WARN] Not found, treating as empty: {path}")
        return {"results": {}, "summary": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    print(f"\n{'=' * 80}")
    print("BREAKING MERGE: combining single-module + multi-module results")
    print(f"{'=' * 80}\n")

    single = _load(SINGLE_INPUT)
    multi = _load(MULTI_INPUT)

    single_results = single.get("results", {})
    multi_results = multi.get("results", {})

    overlap = set(single_results) & set(multi_results)
    if overlap:
        print(
            f"[WARN] {len(overlap)} custom_id(s) appear in BOTH single- and multi-module "
            f"results (unexpected - each instance should only run one way): {sorted(overlap)[:10]}"
        )

    combined_results = {**single_results, **multi_results}

    s_summary = single.get("summary", {})
    m_summary = multi.get("summary", {})
    combined_summary = {k: s_summary.get(k, 0) + m_summary.get(k, 0) for k in SUMMARY_KEYS}
    combined_summary["single_module"] = s_summary
    combined_summary["multi_module"] = m_summary

    FINAL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    FINAL_OUTPUT.write_text(
        json.dumps({"results": combined_results, "summary": combined_summary}, indent=2),
        encoding="utf-8",
    )

    print(f"[INFO] Single-module instances: {len(single_results)}")
    print(f"[INFO] Multi-module instances:  {len(multi_results)}")
    print(f"[INFO] Combined instances:      {len(combined_results)}")
    print(f"\nOVERALL: pass={combined_summary['total_pass']}  fail={combined_summary['total_fail']}")
    print(f"  breaking changes detected (test_failures_breaking_change): {combined_summary['test_failures_breaking_change']}")
    print(f"\nOUTPUT: {FINAL_OUTPUT}")


if __name__ == "__main__":
    main()
