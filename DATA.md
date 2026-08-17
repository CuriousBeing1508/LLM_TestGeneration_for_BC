# Experiment Data Layout

This document describes how the experiment outputs referenced in the main
[README](README.md) - prompts, LLM-generated tests, execution logs, and
analysis results - are organized on disk. It accompanies the archive
published on Zenodo: https://zenodo.org/records/21078853.

## Experiments and models

Every result is produced by one of **3 prompting-context experiments**,
each run against **3 LLMs**:

| Experiment ID | Prompting context | Replication package |
|---|---|---|
| Exp3 | Minimal - smallest amount of surrounding code given to the LLM | `BreakGuard-Minimal/` |
| Exp6 | Method - LLM also given the enclosing method(s) | `BreakGuard-Method/` |
| Exp7 | Class - LLM also given the whole enclosing class | `BreakGuard-Class/` |

| Model folder name | Model |
|---|---|
| `GPT4o` | GPT-4o |
| `GPT_OSS_120b` | GPT-OSS-120B |
| `Qwen_480b_cloud` / `Qwen3_480b_cloud` | Qwen3-Coder-480B |


Each `BUMP` breaking-change instance being tested has an id of the form
`BBC##` (e.g. `BBC103`), and a generated test file/class is named
`<instance><unit-id>Test.java` (e.g. `BBC103U1Test.java`).

## Top-level folders

```
FilteredDataset/         prompts sent to each LLM, and the LLMs' raw responses
TestFilesByStage/        generated test files, sorted by which pipeline stage they reached
GPTResults/              GPT-4o: Docker compile/execute logs + JSON verdicts, per experiment
Qwen480Results/          Qwen3-Coder-480B: same, per experiment
GPTOSSResults/           GPT-OSS-120B: same, per experiment
```

### `FilteredDataset/` - prompts and raw LLM output

```
FilteredDataset/
  Exp{3,6,7}Prompts/
    <instance>/
      <instance><unit-id>Test_prompt.txt     the exact prompt sent to the LLM
  Exp{3,6,7}LLMOutput/
    <model>/
      <instance>/
        <instance><unit-id>Test.txt          the LLM's raw, unprocessed response
```

This is the input to the 5-phase execution pipeline (details available under `BreakGuard-*/README.md`)

### `TestFilesByStage/` - generated tests, sorted by pipeline outcome

```
ResultsDataset/
  Exp{3,6,7}LLMOutput/
    <model>/
      compiled_pre/    <instance>/_java_files/*.java + *_prompt.txt   compiled successfully against the PRE (pre-upgrade) codebase
      execution_pre/   <instance>/_java_files/*.java + *_prompt.txt   compiled AND passed against PRE
      detected_bre/    <instance>/_java_files/*.java + *_prompt.txt   compiled + passed on PRE, then failed/errored on the BREAKING (post-upgrade) codebase - i.e. successfully detected the breaking change
```

Each stage folder is a superset filtered down from the previous one
(`detected_bre` &sube; `execution_pre` &sube; `compiled_pre`). Folder names
(`compiled_pre` / `execution_pre` / `detected_bre`) are kept exactly as they
are on disk - don't rename them, other scripts and the analysis pipeline
reference these names directly.

### `GPTResults/` / `Qwen480Results/` / `GPTOSSResults/` - execution logs and verdicts

One folder per model, each containing one subfolder per experiment:

```
<ModelResults>/
  Exp{3,6,7}BatchResults/
    pre/
      compile_results_pre.json           per-test compile outcome against PRE
      execute_results_pre.json           per-test execution outcome against PRE
      transplant_results_final_pre.json  carry-forward list: compiled AND passed on PRE
      logs/
        <instance>_<TestFile>_compile.log
        <instance>_<TestFile>_execute.log
    bre/
      transplant_results_breaking_single_module.json   final verdict per test, single-module Maven projects
      logs/
        <instance>_<TestFile>_breaking_single.log       raw `javac` + `mvn surefire:test` output (stdout/stderr) for that test run against BREAKING
```
