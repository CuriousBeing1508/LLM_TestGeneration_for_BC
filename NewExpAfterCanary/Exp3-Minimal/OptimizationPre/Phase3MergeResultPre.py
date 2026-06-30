import json
from pathlib import Path
from collections import defaultdict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE, SECONDARY_DRIVE

# # === CONFIG GPT ===
# COMPILE_INPUT = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/compile_results_pre.json"
# EXECUTE_INPUT = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/execute_results_pre.json"
# FINAL_OUTPUT = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/transplant_results_final_pre.json"


# === CONFIG Qwen===
COMPILE_INPUT = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/compile_results_pre.json"
EXECUTE_INPUT = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/execute_results_pre.json"
FINAL_OUTPUT = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/transplant_results_final_pre.json"


def main():
    print(f"\n{'='*80}")
    print(f"PHASE 3: MERGING COMPILATION + EXECUTION RESULTS")
    print(f"{'='*80}\n")

    # Load Phase 1 (Compilation)
    if not COMPILE_INPUT.exists():
        print(f"[ERROR] Compilation results not found: {COMPILE_INPUT}")
        return

    compile_data = json.loads(COMPILE_INPUT.read_text(encoding="utf-8"))
    compilation_results = compile_data.get("compilation_results", {})
    file_counts = compile_data.get("file_counts", {})
    test_counts = compile_data.get("test_counts", {})
    compile_summary = compile_data.get("summary", {})

    print(f"[INFO] Loaded Phase 1 (Compilation):")
    print(f"  Files generated: {compile_summary.get('total_files_generated', 0)}")
    print(f"  Files compiled: {compile_summary.get('total_files_compiled', 0)}")
    print(f"  Compilation failures: {compile_summary.get('total_files_failed_compilation', 0)}")
    print()

    # Load Phase 2 (Execution)
    if not EXECUTE_INPUT.exists():
        print(f"[ERROR] Execution results not found: {EXECUTE_INPUT}")
        return

    execute_data = json.loads(EXECUTE_INPUT.read_text(encoding="utf-8"))
    execution_results = execute_data.get("execution_results", {})
    exec_summary = execute_data.get("summary", {})

    print(f"[INFO] Loaded Phase 2 (Execution):")
    print(f"  Files executed: {exec_summary.get('total_files_executed', 0)}")
    print(f"  Files passed on PRE: {exec_summary.get('total_passed_on_pre', 0)}")
    print(f"  Files failed on PRE: {exec_summary.get('total_failed_on_pre', 0)}")
    print()

    # Merge results
    merged_results = {}
    carry_forward_instances = set()
    carry_forward_tests = execute_data.get("carry_forward_tests", {})

    for custom_id in compilation_results.keys():
        compile_info = compilation_results[custom_id]
        exec_info = execution_results.get(custom_id, {})

        compiled_files = compile_info.get("compiled", [])
        failed_compilation = compile_info.get("failed", {})
        
        passed_on_pre = exec_info.get("passed", [])
        failed_on_pre = exec_info.get("failed", [])

        # Merge into comprehensive result
        merged_results[custom_id] = {
            "compilation": {
                "compiled_successfully": compiled_files,
                "failed_compilation": failed_compilation,
                "compiled_count": len(compiled_files),
                "failed_count": len(failed_compilation)
            },
            "execution": {
                "passed_on_pre": passed_on_pre,
                "failed_on_pre": failed_on_pre,
                "passed_count": len(passed_on_pre),
                "failed_count": len(failed_on_pre)
            },
            "file_counts": file_counts.get(custom_id, {}),
            "test_counts": test_counts.get(custom_id, {}),
            "summary": {
                "files_generated": file_counts.get(custom_id, {}).get("files_generated", 0),
                "files_compiled": len(compiled_files),
                "files_executed": len(passed_on_pre) + len(failed_on_pre),
                "files_passed_on_pre": len(passed_on_pre),
                "compilation_success_rate": f"{(len(compiled_files) / file_counts.get(custom_id, {}).get('files_generated', 1) * 100):.2f}%",
                "execution_success_rate": f"{(len(passed_on_pre) / len(compiled_files) * 100) if compiled_files else 0:.2f}%",
                "overall_success_rate": f"{(len(passed_on_pre) / file_counts.get(custom_id, {}).get('files_generated', 1) * 100):.2f}%"
            }
        }

        if passed_on_pre:
            carry_forward_instances.add(custom_id)

    # Calculate global statistics
    total_files_generated = compile_summary.get('total_files_generated', 0)
    total_files_compiled = compile_summary.get('total_files_compiled', 0)
    total_files_failed_compilation = compile_summary.get('total_files_failed_compilation', 0)
    total_files_executed = exec_summary.get('total_files_executed', 0)
    total_passed_on_pre = exec_summary.get('total_passed_on_pre', 0)
    total_failed_on_pre = exec_summary.get('total_failed_on_pre', 0)

    total_tests_generated = compile_summary.get('total_tests_in_generated_files', 0)
    total_tests_compiled = compile_summary.get('total_tests_in_compiled_files', 0)

    # Save merged results
    FINAL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    FINAL_OUTPUT.write_text(json.dumps({
        "results": merged_results,
        "carry_forward_instances": list(carry_forward_instances),
        "carry_forward_tests": carry_forward_tests,
        "file_counts": file_counts,
        "test_counts": test_counts,
        "global_summary": {
            "total_files_generated": total_files_generated,
            "total_files_compiled": total_files_compiled,
            "total_files_failed_compilation": total_files_failed_compilation,
            "total_files_executed": total_files_executed,
            "total_files_passed_on_pre": total_passed_on_pre,
            "total_files_failed_on_pre": total_failed_on_pre,
            "total_tests_in_generated_files": total_tests_generated,
            "total_tests_in_compiled_files": total_tests_compiled,
            "compilation_success_rate": f"{(total_files_compiled / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%",
            "execution_success_rate": f"{(total_passed_on_pre / total_files_compiled * 100) if total_files_compiled > 0 else 0:.2f}%",
            "overall_success_rate": f"{(total_passed_on_pre / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%",
            "instances_with_passing_tests": len(carry_forward_instances)
        }
    }, indent=2), encoding="utf-8")

    # Print comprehensive summary
    print(f"\n{'='*80}")
    print(f"✓ FINAL RESULTS: PRE STAGE")
    print(f"{'='*80}")
    print(f"")
    print(f"CLASS TEST FILES:")
    print(f"  Files Generated: {total_files_generated}")
    print(f"  Files Compiled Successfully: {total_files_compiled} ({(total_files_compiled / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%)")
    print(f"  Files Failed Compilation: {total_files_failed_compilation}")
    print(f"")
    print(f"  Files Executed: {total_files_executed}")
    print(f"  Files PASSED on PRE: {total_passed_on_pre} ({(total_passed_on_pre / total_files_compiled * 100) if total_files_compiled > 0 else 0:.2f}% of compiled)")
    print(f"  Files FAILED on PRE: {total_failed_on_pre}")
    print(f"")
    print(f"@TEST METHODS:")
    print(f"  @Test methods in generated files: {total_tests_generated}")
    print(f"  @Test methods in compiled files: {total_tests_compiled}")
    print(f"")
    print(f"OVERALL METRICS:")
    print(f"  Compilation Success Rate: {(total_files_compiled / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%")
    print(f"  Execution Success Rate (of compiled): {(total_passed_on_pre / total_files_compiled * 100) if total_files_compiled > 0 else 0:.2f}%")
    print(f"  Overall Success Rate (generated → passed): {(total_passed_on_pre / total_files_generated * 100) if total_files_generated > 0 else 0:.2f}%")
    print(f"")
    print(f"INSTANCES:")
    print(f"  Instances with at least one passing test: {len(carry_forward_instances)}")
    print(f"")
    print(f"OUTPUT: {FINAL_OUTPUT}")
    print(f"{'='*80}\n")

    # Per-instance breakdown
    print(f"\nPer-instance breakdown:")
    print(f"{'Instance':<15} {'Generated':<10} {'Compiled':<10} {'Executed':<10} {'Passed PRE':<12}")
    print(f"{'-'*60}")
    for cid in sorted(merged_results.keys()):
        result = merged_results[cid]
        summary = result["summary"]
        print(f"{cid:<15} {summary['files_generated']:<10} {summary['files_compiled']:<10} "
              f"{summary['files_executed']:<10} {summary['files_passed_on_pre']:<12}")


if __name__ == "__main__":
    main()