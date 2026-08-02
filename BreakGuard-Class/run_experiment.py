#!/usr/bin/env python3
"""Run the full 6-stage BreakGuard execution pipeline for one or more models.

This orchestrator does not change any Docker execution behaviour - it just
invokes, in order, the same phase scripts you would otherwise run by hand:

  1. scripts/phase1_compile_pre.py              compile LLM tests on PRE
  2. scripts/phase2_execute_pre.py              execute compiled tests on PRE
  3. scripts/phase3_merge_pre.py                merge -> carry-forward list
  4. scripts/execute_breaking_single_module.py  run carry-forward tests on BREAKING (single-module)
  5. scripts/execute_breaking_multi_module.py   run carry-forward tests on BREAKING (multi-module)
  6. scripts/phase4_merge_breaking.py           merge single+multi BREAKING results into one file

Each phase script already saves results incrementally per instance and
resumes automatically when rerun, so re-running this orchestrator after an
interruption (or a fresh clone where nothing has run yet) just picks up
where it left off - that resume/incremental-save logic is untouched here.

Results and per-instance logs are NOT written inside this package (they can
run into the hundreds of MB per model). By default they land under
<data-root>/Replication2/<ModelResults>/<ContextBatchResults>/{pre,bre}/,
mirroring the original experiment drive's own naming convention but kept
under a separate "Replication2" namespace so a replication run can never
collide with or overwrite the original experiment's results. Override with
--results-root (a different drive/folder) and/or --results-namespace.

Examples:
    python run_experiment.py --model gpt4o --data-root /Volumes/Rachna-HD
    python run_experiment.py --model all --data-root /Volumes/Rachna-HD
    DATA_ROOT=/Volumes/Rachna-HD python run_experiment.py --model qwen3-480b \
        --stages pre-compile pre-execute pre-merge
    python run_experiment.py --model gpt4o --data-root /Volumes/Rachna-HD \
        --results-root /Volumes/RachnaPSSD --results-namespace Replication3
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"
sys.path.insert(0, str(PACKAGE_ROOT))
import config as pkg_config
from cli import add_common_args, resolve_paths

# (script filename, human description, index)
STAGES = [
    ("phase1_compile_pre.py", "PRE Phase 1 - Compile"),
    ("phase2_execute_pre.py", "PRE Phase 2 - Execute"),
    ("phase3_merge_pre.py", "PRE Phase 3 - Merge"),
    ("execute_breaking_single_module.py", "BREAKING - Single-module"),
    ("execute_breaking_multi_module.py", "BREAKING - Multi-module"),
    ("phase4_merge_breaking.py", "BREAKING - Merge"),
]
STAGE_CHOICES = {
    "pre-compile": 0,
    "pre-execute": 1,
    "pre-merge": 2,
    "breaking-single": 3,
    "breaking-multi": 4,
    "breaking-merge": 5,
}


def run_stage(script_name, model, args, log_path):
    cmd = [sys.executable, str(SCRIPTS_DIR / script_name), "--model", model, "--data-root", str(args.data_root)]
    if args.results_root:
        cmd += ["--results-root", str(args.results_root)]
    cmd += ["--results-namespace", args.results_namespace]
    print(f"\n{'=' * 80}\n$ {' '.join(cmd)}\n{'=' * 80}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"\n\n===== {datetime.now().isoformat()} :: {script_name} :: model={model} =====\n")
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            print(line, end="")
            log_file.write(line)
        process.wait()
        return process.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser, include_model=False)
    parser.add_argument(
        "--model",
        required=True,
        choices=sorted(pkg_config.MODELS) + ["all"],
        help="Model to run, or 'all' to run every model in sequence.",
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=list(STAGE_CHOICES),
        default=list(STAGE_CHOICES),
        help="Subset of stages to run, in order (default: all six).",
    )
    args = parser.parse_args()

    if not args.data_root:
        sys.exit("ERROR: --data-root is required (or set the DATA_ROOT environment variable).")

    models = sorted(pkg_config.MODELS) if args.model == "all" else [args.model]
    selected_indices = sorted({STAGE_CHOICES[s] for s in args.stages})

    for model in models:
        print(f"\n{'#' * 80}\n# MODEL: {model}\n{'#' * 80}")
        model_args = argparse.Namespace(**{**vars(args), "model": model})
        paths = resolve_paths(model_args)
        log_path = paths["results_dir"] / "run_experiment.log"
        for idx in selected_indices:
            script_name, description = STAGES[idx]
            print(f"\n--- {description} ({model}) ---")
            rc = run_stage(script_name, model, model_args, log_path)
            if rc != 0:
                print(
                    f"\n[ABORTED] {description} failed for model={model} (exit code {rc}). "
                    f"See {log_path}",
                    file=sys.stderr,
                )
                if idx in (0, 1, 2):  # a failed PRE stage blocks BREAKING for this model
                    break
        print(f"\n[DONE] Finished pipeline for model={model}. Results in {paths['results_dir']}")


if __name__ == "__main__":
    main()
