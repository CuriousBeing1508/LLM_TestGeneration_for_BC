#!/usr/bin/env python3
"""BREAKING merge: combine the single-module and multi-module BREAKING-stage
results into one final transplant_results_breaking.json per model.
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


def _load(path):
    if not path.exists():
        print(f"[WARN] Not found, treating as empty: {path}")
        return {"results": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _test_file_counts(summary):
    """Normalize either schema (single-module's nested test_file_summary, or
    multi-module's old flat summary) into one flat dict of file-level counts."""
    if "build_failures" in summary and isinstance(summary["build_failures"], dict):
        bf = summary["build_failures"]
        compilation_errors = bf.get("compilation_errors", 0)
        build_failures_other = bf.get("other", 0)
    else:
        compilation_errors = summary.get("compilation_errors", 0)
        build_failures_other = summary.get("build_failures_without_test_execution", 0)
    return {
        "total_pass": summary.get("total_pass", 0),
        "total_fail": summary.get("total_fail", 0),
        "test_failures_breaking_change": summary.get("test_failures_breaking_change", 0),
        "transplant_issues": summary.get("transplant_issues", 0),
        "timeouts": summary.get("timeouts", 0),
        "compilation_errors": compilation_errors,
        "build_failures_other": build_failures_other,
    }


def _instances_with_breaking_change(results):
    return sum(
        1 for data in results.values()
        if data.get("summary", {}).get("test_failures_breaking_change", 0) > 0
    )


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

    s_tf = _test_file_counts(single.get("test_file_summary", single.get("summary", {})))
    m_tf = _test_file_counts(multi.get("summary", {}))

    test_file_summary = {
        "total_test_files_attempted": s_tf["total_pass"] + s_tf["total_fail"] + m_tf["total_pass"] + m_tf["total_fail"],
        "total_pass": s_tf["total_pass"] + m_tf["total_pass"],
        "total_fail": s_tf["total_fail"] + m_tf["total_fail"],
        "test_failures_breaking_change": s_tf["test_failures_breaking_change"] + m_tf["test_failures_breaking_change"],
        "transplant_issues": s_tf["transplant_issues"] + m_tf["transplant_issues"],
        "timeouts": s_tf["timeouts"] + m_tf["timeouts"],
        "build_failures": {
            "total": s_tf["compilation_errors"] + s_tf["build_failures_other"] + m_tf["compilation_errors"] + m_tf["build_failures_other"],
            "compilation_errors": s_tf["compilation_errors"] + m_tf["compilation_errors"],
            "other": s_tf["build_failures_other"] + m_tf["build_failures_other"],
        },
    }

    s_inst = single.get("instance_level_summary", {})
    total_instances_in_dataset = s_inst.get("total_instances_in_dataset", 0)
    s_processed = s_inst.get("instances_processed", len(single_results))
    m_processed = multi.get("summary", {}).get("processed", len(multi_results))
    instances_processed = s_processed + m_processed

    instance_level_summary = {
        "total_instances_in_dataset": total_instances_in_dataset,
        "instances_processed": instances_processed,
        "instances_skipped": max(total_instances_in_dataset - instances_processed, 0),
        "instances_with_breaking_change": _instances_with_breaking_change(single_results) + _instances_with_breaking_change(multi_results),
        "single_module": s_inst,
        "multi_module": multi.get("summary", {}),
    }

    FINAL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    FINAL_OUTPUT.write_text(
        json.dumps({
            "results": combined_results,
            "test_file_summary": test_file_summary,
            "instance_level_summary": instance_level_summary,
        }, indent=2),
        encoding="utf-8",
    )

    print(f"[INFO] Single-module instances: {len(single_results)}")
    print(f"[INFO] Multi-module instances:  {len(multi_results)}")
    print(f"[INFO] Combined instances:      {len(combined_results)}")
    print(f"\nOVERALL: pass={test_file_summary['total_pass']}  fail={test_file_summary['total_fail']}")
    print(f"  breaking changes detected (test_failures_breaking_change): {test_file_summary['test_failures_breaking_change']}")
    print(f"  transplant_issues: {test_file_summary['transplant_issues']}")
    print(f"\nOUTPUT: {FINAL_OUTPUT}")


if __name__ == "__main__":
    main()
