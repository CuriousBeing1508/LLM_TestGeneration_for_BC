"""Path resolution for the BreakGuard-Method execution pipeline.
"""
from pathlib import Path

LLM_OUTPUT_SUBDIR = "Exp6LLMOutput"
CONTEXT_BATCH_DIRNAME = "Exp6BatchResults"

PACKAGE_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PACKAGE_ROOT / "data" / "ConfigFiles"

# --model key -> (folder name under <data_root>/FilteredDataset/Exp6LLMOutput/,
#                 folder name under <results_root>/<namespace>/)
MODELS = {
    "gpt4o": "GPT4o",
    "qwen3-480b": "Qwen3_480b_cloud",
    "gpt-oss-120b": "GPT_OSS_120b",
}
MODEL_RESULTS_DIRNAME = {
    "gpt4o": "GPTResults",
    "qwen3-480b": "Qwen480Results",
    "gpt-oss-120b": "GPTOSSResults",
}


def get_paths(model: str, data_root: Path, results_root: Path, results_namespace: str = "Replication2") -> dict:
    if model not in MODELS:
        raise ValueError(f"Unknown model '{model}'. Choices: {sorted(MODELS)}")
    data_root = Path(data_root)
    results_dir = Path(results_root) / results_namespace / MODEL_RESULTS_DIRNAME[model] / CONTEXT_BATCH_DIRNAME
    pre_dir = results_dir / "pre"
    bre_dir = results_dir / "bre"
    return {
        "csv_path": CONFIG_DIR / "updated_FinalBUMP_Instances_with_TestRunner.csv",
        "summary_path": CONFIG_DIR / "package_structure_summary.txt",
        "multi_module_list": CONFIG_DIR / "multi_module_instances.json",
        "abc_root": data_root / "FilteredDataset" / LLM_OUTPUT_SUBDIR / MODELS[model],
        "model_name": MODELS[model],
        "results_dir": results_dir,
        "compile_output": pre_dir / "compile_results_pre.json",
        "execute_output": pre_dir / "execute_results_pre.json",
        "final_pre_output": pre_dir / "transplant_results_final_pre.json",
        "breaking_single_output": bre_dir / "transplant_results_breaking_single_module.json",
        "breaking_multi_output": bre_dir / "transplant_results_breaking_multi_module.json",
        "breaking_output": bre_dir / "transplant_results_breaking.json",
        "log_dir_pre": pre_dir / "logs",
        "log_dir_bre": bre_dir / "logs",
    }
