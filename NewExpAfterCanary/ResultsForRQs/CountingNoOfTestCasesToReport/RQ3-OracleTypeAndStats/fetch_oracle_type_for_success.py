"""
For every DETECTED (i.e. successfully bug-detecting) test file, reports the raw
assertion data for each test method/case: which assert method was called, its
exact call text, and whether it's inside a try/catch.

Source of truth: AssertAnalysisResults/<context>/<model>/detected_bre_details.json. That file already has, per test file, per test method,
the list of assert calls (method name, message text, try/catch context) extracted
via tree-sitter.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicate_extractor import extract_predicate_and_message, resolve_call_text

BASE = PRIMARY_DRIVE / "AssertAnalysisResults"

CONTEXTS = {
    "Exp3LLMOutput": "Minimal",
    "Exp6LLMOutput": "Method",
    "Exp7LLMOutput": "Class",
}
MODELS = {
    "GPT4o": "GPT4o",
    "GPT_OSS_120b": "GPTOSS",
    "Qwen_480b_cloud": "Qwen3-coder",
}

OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "success_cases" / "oracle_types_detected_success.json"


def instance_from_path(path_str):
    # .../detected_bre/<INSTANCE>/_java_files/<file>.java
    parts = Path(path_str).parts
    idx = parts.index("detected_bre")
    return parts[idx + 1]


def build_test_method_entry(test_method, java_file_path):
    assert_calls = test_method["assert_calls"]
    if not assert_calls:
        return {
            "assert_calls": [],
            "note": "no explicit assertion in this test method",
        }

    entries = []
    for call in assert_calls:
        call_text, truncated = resolve_call_text(call, java_file_path)
        predicate, message = extract_predicate_and_message(call["method"], call.get("framework"), call_text)
        entry = {
            "method": call["method"],
            "call_text": call_text,
            "predicate": predicate,
            "message": message,
            "line": call["line"],
            "in_trycatch": call["in_trycatch"],
            "framework": call.get("framework"),
            "source": call.get("source"),
        }
        if truncated:
            entry["call_text_truncated"] = True
        entries.append(entry)

    return {"assert_calls": entries}


def build_test_file_entry(file_record):
    return {
        test_method["name"]: build_test_method_entry(test_method, file_record["path"])
        for test_method in file_record["test_methods"]
    }


def main():
    result = {}
    missing = []
    for context_dir, context_label in CONTEXTS.items():
        result[context_label] = {}
        for model_dir, model_label in MODELS.items():
            details_path = BASE / context_dir / model_dir / "detected_bre_details.json"
            if not details_path.exists():
                missing.append(str(details_path))
                continue

            file_records = json.load(open(details_path, encoding="utf-8"))
            per_instance = {}
            for record in file_records:
                instance = instance_from_path(record["path"])
                per_instance.setdefault(instance, {})[record["file"]] = build_test_file_entry(record)

            result[context_label][model_label] = per_instance

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"Wrote {OUTPUT_PATH}")
    if missing:
        print("Missing source files:")
        for m in missing:
            print(f"  {m}")


if __name__ == "__main__":
    main()
