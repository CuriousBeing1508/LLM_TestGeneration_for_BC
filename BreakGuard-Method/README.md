# BreakGuard-Method

Execution pipeline for the BreakGuard breaking-change experiments with
the METHOD prompting context (the LLM was given the enclosing method(s) as additional context when generating each test).

NOTE: This package starts at the **execution** step of the study. It assumes the
earlier steps - static analysis of each BUMP instance, prompt construction,
and prompting the LLM - have already been done and their outputs are
available on disk (see "Expected data layout" below). This package only
does the 5-phase compile/execute/merge/breaking pipeline that turns those
already-generated LLM test files into pass/fail breaking-change verdicts.

## Pipeline

Each model run goes through 5 phases, always in this order:

1. `scripts/phase1_compile_pre.py` - compile every LLM-generated test against
   the **PRE** (pre-upgrade) codebase in Docker.
2. `scripts/phase2_execute_pre.py` - execute the tests that compiled, against
   the same PRE codebase, to find which ones already pass before the upgrade.
3. `scripts/phase3_merge_pre.py` - merge phases 1+2 into a carry-forward list
   of tests that compiled *and* passed on PRE (only these are meaningful
   breaking-change probes).
4. `scripts/execute_breaking_single_module.py` - run the carry-forward tests
   against the **BREAKING** (post-upgrade) codebase, for single-module Maven
   projects.
5. `scripts/execute_breaking_multi_module.py` - same, for multi-module Maven
   projects (uses the full reactor build instead of a single `javac` + test).
6. `scripts/phase4_merge_breaking.py` - merge phases 4+5 into one combined
   `transplant_results_breaking.json` (single- and multi-module instances
   never overlap, so this is a straight merge, not a filter).

Every phase script saves its results **incrementally, per BUMP instance**,
and skips instances it already has a saved result for - so re-running any
phase (or the whole pipeline) after an interruption resumes automatically
instead of starting over. Docker execution is classified as compile/test failures. 

## Prerequisites

- Python 3.9+ (standard library only, see `requirements.txt`)
- Docker Desktop / Docker Engine running, `docker` on PATH
- The pre-generated LLM test outputs for this context (see below)

## Expected data layout

Point `--data-root` (or the `DATA_ROOT` env var) at a folder shaped like:

```
<DATA_ROOT>/
  FilteredDataset/
    Exp6LLMOutput/
      GPT4o/                 <custom_id>/*.txt (or *_prompt.txt) LLM outputs
      Qwen3_480b_cloud/      <custom_id>/*.txt
      GPT_OSS_120b/          <custom_id>/*.txt
```

The small metadata files every phase needs (`updated_FinalBUMP_Instances_with_TestRunner.csv`,
`package_structure_summary.txt`, `multi_module_instances.json`) are
bundled in `data/ConfigFiles/` in this package. They're identical across
all three context packages (Minimal/Method/Class) and across all three
models to describe the dataset's project/package structure.

## Running

The easiest way to run a single model is the matching wrapper script - it
hardcodes the correct `--model` value so there's nothing to mistype:

```bash
./run_gpt4o.sh <Path to the dataset root where config is stored>
./run_qwen3-480b.sh <Path to the dataset root where config is stored>
./run_gpt-oss-120b.sh <Path to the dataset root where config is stored>
```

To call `run_experiment.py` directly for one model:

```bash
python run_experiment.py --model gpt4o --data-root <Path to the dataset root where config is stored>
```
This will automatically run the experiment for all context variant. 

Run all three models back to back:

```bash
python run_experiment.py --model all --data-root <Path to the dataset root where config is stored>
```

Run only a subset of stages (e.g. resume from BREAKING after PRE already
completed):

```bash
python run_experiment.py --model gpt4o --data-root <Path to the dataset root where config is stored> \
    --stages breaking-single breaking-multi
```

Or run a single phase script directly (each one takes the same flags):

```bash
python scripts/phase1_compile_pre.py --model gpt4o --data-root <Path to the dataset root where config is stored>
```

`--model` accepts `gpt4o`, `qwen3-480b`, or `gpt-oss-120b`.

By default, results/logs are written back under `--data-root`. To put them
somewhere else (e.g. a drive with more free space than where the input
data lives), pass `--results-root` separately:


## Output layout: results and logs live on DATA_ROOT, not in this package

Per-instance Docker logs are large (hundreds of MB per model/context), so
nothing under `results/` or `logs/` is written inside this git-tracked
package. Instead, everything for a given model lands under:

```
<results-root>/<results-namespace>/<ModelResults>/Exp6BatchResults/
  pre/
    compile_results_pre.json
    execute_results_pre.json
    transplant_results_final_pre.json
    logs/                                    per-test compile/execute logs
  bre/
    transplant_results_breaking_single_module.json   raw output of stage 4 (single-module)
    transplant_results_breaking_multi_module.json    raw output of stage 5 (multi-module)
    transplant_results_breaking.json                 <- final combined result (stage 6)
    logs/                                    per-test breaking-stage logs
  run_experiment.log                         orchestrator log (all phases)
```
