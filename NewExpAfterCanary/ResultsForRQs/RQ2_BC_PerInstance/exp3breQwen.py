import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import SECONDARY_DRIVE
# RQ1: How many instance detects BC?
# === CONFIG ===
BREAKING_RESULTS_PATH = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/bre/transplant_results_breaking_single_module.json"
OUTPUT_STATS_PATH = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/bre/exp3breRQ2Stats.json"

def main():
    if not BREAKING_RESULTS_PATH.exists():
        print(f"[ERROR] Breaking results file not found: {BREAKING_RESULTS_PATH}")
        return

    data = json.loads(BREAKING_RESULTS_PATH.read_text(encoding="utf-8"))
    results = data.get("results", {})

    failed_count = 0
    passed_count = 0
    both_pass_fail_count = 0
    total_failed_tests = 0
    total_passed_tests = 0
    per_instance = {}

    for cid, entry in results.items():
        tests = entry.get("tests", {})
        passed = tests.get("passed", [])
        failed = tests.get("failed", [])

        per_instance[cid] = {
            "passed": len(passed),
            "failed": len(failed)
        }

        if failed:
            failed_count += 1
        if passed:
            passed_count += 1
        if passed and failed:
            both_pass_fail_count += 1

        total_failed_tests += len(failed)
        total_passed_tests += len(passed)

    stats = {
        "total_instances": len(results),
        "instances_with_failed": failed_count,
        "instances_with_passed": passed_count,
        "instances_with_both_pass_and_fail": both_pass_fail_count,
        "total_failed_tests": total_failed_tests,
        "total_passed_tests": total_passed_tests,
        "per_instance": per_instance
    }

    # Save to file
    OUTPUT_STATS_PATH.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    print("[SUMMARY] Breaking Results Stats")
    print(f"  Total instances processed: {stats['total_instances']}")
    print(f"  Instances with failed tests: {stats['instances_with_failed']}")
    print(f"  Instances with passed tests: {stats['instances_with_passed']}")
    print(f"  Instances with both passed and failed tests: {stats['instances_with_both_pass_and_fail']}")
    print(f"  Total failed tests: {stats['total_failed_tests']}")
    print(f"  Total passed tests: {stats['total_passed_tests']}")
    print(f"[INFO] Stats saved to {OUTPUT_STATS_PATH}")

if __name__ == "__main__":
    main()
