# BreakGuard-Class

Execution pipeline for the BreakGuard breaking-change test experiments,the CLASS prompting context (the LLM was given the whole enclosing class as context when generating each test).

This package starts at the **execution** step of the study. It assumes the earlier steps - static analysis of each BUMP instance, prompt construction, and prompting the LLM - have already been done and their outputs are available on disk (see "Expected data layout" below). This package only does the 5-phase compile/execute/merge/breaking pipeline that turns those already-generated LLM test files into pass/fail breaking-change verdicts.

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

Every phase script saves its results **incrementally, per BUMP instance**, and skips instances it already has a saved result for - so re-running any phase (or the whole pipeline) after an interruption resumes automatically instead of starting over. Docker execution itself (commands, timeouts, classification of compile/test failures) is unchanged from the original experiment scripts; only how paths are configured has been cleaned up.

### Fix included: unfenced LLM output (mainly affects GPT-OSS-120b)

The original extractor only recognized a test wrapped in a markdown java code fence. GPT-OSS-120b very often returns raw Java with no fence at all (in this dataset, roughly 85-90% of its outputs are unfenced, vs. 0% for GPT4o and a few percent for Qwen3-480B) - those responses were silently treated as "no code found" and dropped before ever reaching compilation, which is why prior GPT-OSS run counts came in far below the expected number of generated files. `common.py`'s `extract_llm_java_block()` now falls back to the raw response when no fence is present (or an empty fence is found) and it still looks like Java, instead of discarding it.

## Prerequisites

- Python 3.9+ (standard library only, see `requirements.txt`)
- Docker Desktop / Docker Engine running, `docker` on PATH
- The pre-generated LLM test outputs for this context (see below)

## Expected data layout

Point `--data-root` (or the `DATA_ROOT` env var) at a folder shaped like:

```
<DATA_ROOT>/
  FilteredDataset/
    Exp7LLMOutput/
      GPT4o/                 <custom_id>/*.txt (or *_prompt.txt) LLM outputs
      Qwen3_480b_cloud/      <custom_id>/*.txt
      GPT_OSS_120b/          <custom_id>/*.txt
```

The small metadata files every phase needs (`updated_FinalBUMP_Instances_with_TestRunner.csv`, `package_structure_summary.txt`, `multi_module_instances.json`) are already bundled in `data/ConfigFiles/` in this package - they're identical across all three context packages (Minimal/Method/Class) and across all three
models, since they describe the dataset's project/package structure, not the LLM outputs.

## Running

The easiest way to run a single model is the matching wrapper script - it
hardcodes the correct `--model` value so there's nothing to mistype:

```bash
./run_gpt4o.sh /Volumes/Rachna-HD
./run_qwen3-480b.sh /Volumes/Rachna-HD
./run_gpt-oss-120b.sh /Volumes/Rachna-HD
```

Any extra arguments are passed straight through to `run_experiment.py`, e.g.:

```bash
./run_gpt-oss-120b.sh /Volumes/Rachna-HD --stages breaking-single breaking-multi
```

Equivalently, call `run_experiment.py` directly for one model:

```bash
python run_experiment.py --model gpt4o --data-root /Volumes/Rachna-HD
```

Run all three models back to back:

```bash
python run_experiment.py --model all --data-root /Volumes/Rachna-HD
```

Run only a subset of stages (e.g. resume from BREAKING after PRE already
completed):

```bash
python run_experiment.py --model gpt4o --data-root /Volumes/Rachna-HD \
    --stages breaking-single breaking-multi
```

Or run a single phase script directly (each one takes the same flags):

```bash
python scripts/phase1_compile_pre.py --model gpt4o --data-root /Volumes/Rachna-HD
```

`--model` accepts `gpt4o`, `qwen3-480b`, or `gpt-oss-120b`.

By default, results/logs are written back under `--data-root`. To put them somewhere else (e.g. a drive with more free space than where the input data lives), pass `--results-root` separately:

```bash
python run_experiment.py --model gpt4o --data-root /Volumes/Rachna-HD \
    --results-root /Volumes/RachnaPSSD
```

## Output layout: results and logs live on DATA_ROOT, not in this package

Per-instance Docker logs are large (hundreds of MB per model/context), so nothing under `results/` or `logs/` is written inside this git-tracked package. Instead, everything for a given model lands under:

```
<results-root>/<results-namespace>/<ModelResults>/Exp7BatchResults/
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

`transplant_results_breaking.json` is the one file to read for the final breaking-change verdicts - it merges the single-module and multi-module runs (which never overlap in which instances they cover) into one `results` dict and one `summary` block, with the per-type breakdown still available under `summary.single_module` / `summary.multi_module`.

`<results-root>` defaults to `--data-root`. `<results-namespace>` defaults to `Replication2` and exists specifically so a replication run's output can never collide with or overwrite the original experiment's results already sitting at `<data-root>/<ModelResults>/Exp7BatchResults/...` on the same drive - `<ModelResults>` is `GPTResults` / `Qwen480Results` / `GPTOSSResults` for `gpt4o` / `qwen3-480b` / `gpt-oss-120b` respectively, matching the original experiment's own folder naming. Override the namespace with `--results-namespace` (e.g. `Replication3`) if you run the pipeline more than once and want to keep each run's output separate.
