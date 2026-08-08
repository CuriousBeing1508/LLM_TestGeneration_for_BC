#!/usr/bin/env python3
"""
Targeted re-run of two fixes for GPT4o and Qwen (Exp7 / Class context).

Fix 1 - classpath: phase1_compile_pre.py / phase2_execute_pre.py / execute_breaking_single_module.py
in BreakGuard-Class used to build the test classpath manually via a separate
`mvn dependency:build-classpath` call spliced into `javac -cp ...`. That call could
silently fail (stderr discarded, no exit-code check), leaving javac with an empty
classpath and misleading "package X does not exist" errors. The scripts now use plain
`mvn test-compile` / `mvn test -Dtest=FQN` instead, which has no separate classpath step
to fail. See BreakGuard-Class/scripts/phase1_compile_pre.py and phase2_execute_pre.py.

Fix 2 - false-positive PASS on PRE: phase2_execute_pre.py used to treat "BUILD SUCCESS
but no 'Running {fqn}' line in the output" as a PASS. That's a test that never actually
executed - surefire silently matched nothing. The breaking-stage script checks the
identical condition and correctly calls it transplant_issue (a failure). Confirmed against
the GPT-OSS run: BBC07/BBC94's entire compiled test sets only "passed" PRE via this
fallback, and 100% of them showed up as transplant_issue at the breaking stage - i.e. they
never ran on PRE either. phase2_execute_pre.py now classifies this the same way
(failure_type="transplant_issue"), so a test only counts as passed - and only gets carried
forward to breaking - if it was actually observed to run.

This script does NOT touch:
  - The original GPT4o (`GPTResults/Exp7BatchResultsOp2`) or Qwen (`Qwen480Results/Exp7BatchResults`)
    result data on disk - only reads from it.
  - The real `Replication2` namespace, which is reserved for the full GPT-OSS-120b rerun
    currently in progress (and any future full gpt4o/qwen reruns).

It writes ONLY into a separate `Replication2-Verify40` namespace, so results can be
inspected and compared before anyone decides whether to fold them into anything real.

Instance selection: these 40 (gpt4o) / 38 (qwen) instances were flagged by grepping the
ORIGINAL compile logs for signs of the classpath bug - either (a) `org.junit...does not
exist` (a dependency that should always resolve), or (b) 3+ distinct "package X does not
exist" errors in a single file (broad, not-just-one-import loss, the empirical signature
of a totally empty classpath vs. a genuine single wrong/hallucinated import). 36 of the 38
instances are identical between the two models, which is itself evidence this is a
per-project/per-Docker-image issue rather than per-run random flakiness.

Usage:
  # Print the plan without running anything (default, safe):
  python3 run_targeted_verification.py

  # Actually seed the isolated namespace and run phase1 -> phase2 -> phase3
  # for both models (only after the current GPT-OSS run is done - this uses the
  # same Docker daemon and will compete for resources with anything else running):
  python3 run_targeted_verification.py --execute
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path("/Users/rachnaraj/Documents/LLM_TestGeneration_for_BC/BreakGuard-Class")
DATA_ROOT = Path("/Volumes/Rachna-HD")
RESULTS_ROOT = Path("/Volumes/Rachna-HD")
NAMESPACE = "Replication2-Verify40"  # isolated - never the real "Replication2"

GPT4O_TARGETS = {
    'BBC05', 'BBC10', 'BBC16', 'BBC22', 'BBC24', 'BBC31', 'BBC35', 'BBC44', 'BBC49',
    'BBC51', 'BBC52', 'BBC53', 'BBC60', 'BBC70', 'BBC74', 'BBC78', 'BBC79', 'BBC80',
    'BBC81', 'BBC82', 'BBC83', 'BBC99', 'BBC101', 'BBC104', 'BBC107', 'BBC111',
    'BBC118', 'BBC119', 'BBC121', 'BBC124', 'BBC144', 'BBC153', 'BBC155', 'BBC158',
    'BBC168', 'BBC170', 'BBC179', 'BBC182', 'BBC184', 'BBC186',
}
QWEN_TARGETS = {
    'BBC05', 'BBC10', 'BBC16', 'BBC22', 'BBC24', 'BBC31', 'BBC35', 'BBC44',
    'BBC51', 'BBC52', 'BBC53', 'BBC60', 'BBC70', 'BBC74', 'BBC78', 'BBC79', 'BBC80',
    'BBC81', 'BBC82', 'BBC83', 'BBC99', 'BBC104', 'BBC107', 'BBC111',
    'BBC118', 'BBC119', 'BBC121', 'BBC124', 'BBC144', 'BBC153', 'BBC155', 'BBC158',
    'BBC168', 'BBC170', 'BBC179', 'BBC182', 'BBC184', 'BBC186',
}

MODELS = {
    "gpt4o": {
        "targets": GPT4O_TARGETS,
        "results_dirname": "GPTResults",
        "source_pre_dir": DATA_ROOT / "GPTResults" / "Exp7BatchResultsOp2" / "pre",
    },
    "qwen3-480b": {
        "targets": QWEN_TARGETS,
        "results_dirname": "Qwen480Results",
        "source_pre_dir": DATA_ROOT / "Qwen480Results" / "Exp7BatchResults" / "pre",
    },
}

CONTEXT_BATCH_DIRNAME = "Exp7BatchResults"


def dest_pre_dir(model):
    return RESULTS_ROOT / NAMESPACE / MODELS[model]["results_dirname"] / CONTEXT_BATCH_DIRNAME / "pre"


def seed_and_patch(model):
    """Fresh-copy the ORIGINAL compile/execute results for `model` into the isolated
    namespace, then un-mark the target instances as processed so the fixed scripts'
    existing resume/skip logic reprocesses ONLY those instances. Idempotent - always
    starts from the untouched source, so safe to re-run from scratch."""
    src = MODELS[model]["source_pre_dir"]
    dst = dest_pre_dir(model)
    dst.mkdir(parents=True, exist_ok=True)

    for fname in ("compile_results_pre.json", "execute_results_pre.json"):
        src_file = src / fname
        if not src_file.exists():
            print(f"[WARN] {model}: source file missing, skipping: {src_file}")
            continue
        shutil.copy(src_file, dst / fname)

    targets = MODELS[model]["targets"]

    # --- patch compile_results_pre.json ---
    compile_path = dst / "compile_results_pre.json"
    if compile_path.exists():
        d = json.loads(compile_path.read_text())
        removed = 0
        for cid in targets:
            if cid in d.get("processed_instances", []):
                d["processed_instances"].remove(cid)
                removed += 1
            d.get("compilation_results", {}).pop(cid, None)
            if cid in d.get("file_counts", {}):
                d["file_counts"][cid]["files_compiled"] = 0
            if cid in d.get("test_counts", {}):
                d["test_counts"][cid]["tests_in_compiled_files"] = 0
        compile_path.write_text(json.dumps(d, indent=2))
        print(f"[{model}] compile_results_pre.json: un-marked {removed}/{len(targets)} target instances")

    # --- patch execute_results_pre.json ---
    execute_path = dst / "execute_results_pre.json"
    if execute_path.exists():
        d = json.loads(execute_path.read_text())
        removed = 0
        for cid in targets:
            if cid in d.get("processed_instances", []):
                d["processed_instances"].remove(cid)
                removed += 1
            d.get("execution_results", {}).pop(cid, None)
            if cid in d.get("carry_forward_instances", []):
                d["carry_forward_instances"].remove(cid)
            d.get("carry_forward_tests", {}).pop(cid, None)
        execute_path.write_text(json.dumps(d, indent=2))
        print(f"[{model}] execute_results_pre.json: un-marked {removed}/{len(targets)} target instances")


def run_phase(script_name, model):
    cmd = [
        sys.executable, str(REPO_ROOT / "scripts" / script_name),
        "--model", model,
        "--data-root", str(DATA_ROOT),
        "--results-root", str(RESULTS_ROOT),
        "--results-namespace", NAMESPACE,
    ]
    print(f"\n{'='*80}\nRunning: {' '.join(cmd)}\n{'='*80}")
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)


def report_transplant_issues(model):
    execute_path = dest_pre_dir(model) / "execute_results_pre.json"
    if not execute_path.exists():
        print(f"[{model}] no execute_results_pre.json found, skipping transplant_issue check")
        return
    d = json.loads(execute_path.read_text())
    targets = MODELS[model]["targets"]
    found = []
    for cid in targets:
        for entry in d.get("execution_results", {}).get(cid, {}).get("failure_breakdown", {}).get("execution_failures", []):
            if entry.get("failure_type") == "transplant_issue":
                found.append((cid, entry.get("file")))
    print(f"\n[{model}] transplant_issue among {len(targets)} target instances: {len(found)}")
    for cid, f in found:
        print(f"    {cid} / {f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true",
                         help="Actually seed + run. Without this flag, only prints the plan.")
    parser.add_argument("--models", nargs="+", choices=list(MODELS.keys()), default=list(MODELS.keys()),
                         help="Which model(s) to process (default: both).")
    args = parser.parse_args()

    print(f"Namespace: {NAMESPACE} (isolated - not the real Replication2)")
    for model in args.models:
        print(f"\n{model}: {len(MODELS[model]['targets'])} target instances")
        print(f"  source: {MODELS[model]['source_pre_dir']}")
        print(f"  dest:   {dest_pre_dir(model)}")

    if not args.execute:
        print("\n[DRY RUN] Nothing was seeded or executed. Pass --execute to actually run.")
        return

    for model in args.models:
        seed_and_patch(model)

    for model in args.models:
        run_phase("phase1_compile_pre.py", model)
        run_phase("phase2_execute_pre.py", model)
        run_phase("phase3_merge_pre.py", model)

    for model in args.models:
        report_transplant_issues(model)

    print(f"\n{'='*80}\nDONE. Results under: {RESULTS_ROOT / NAMESPACE}\n{'='*80}")


if __name__ == "__main__":
    main()
