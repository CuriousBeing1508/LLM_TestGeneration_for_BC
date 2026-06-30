# Baseline prompt: pass the whole focal class for better context to the LLM. # The goal is to generate tests that expose breaking changes in OSS libraries. 
# 
# """ ---- 1. Metadata --- 
# - Client Project: $project_name$ 
# - OSS Library: $oss_library_name$ -
#  OSS Version: $oss_version_baseline$ 
# 
# ---- 2. Program context --- 
# - Focal class FQN: $focal_class_fqn$ 
# - Focal method signature: $focal_method_fqn$($param_types$): $return_type$ 
# /* Dependency methods invoked by the focal method */ 
# $dependency_method_signatures$ // fully-qualified with param + return type 
# 
# ---- 3. Test code format for LLM --- 
# /* Test framework: $test_framework$ */ 
# 
# public class $test_class_name$ 
# { // Deterministic necessary object declaration 
# 
# @Test public void test_$focal_method_name$() 
# { // Act: call focal method 
# 
# // Assert: strong deterministic checks (values, ordering, exception types, invariants) } } 
# 
# ---- 4. Test goal --- 
# 
# /** $test_intention$ * Goal: detect breaking changes in $oss_library_name$ 
# * by running the client’s focal method and observing its behavior. 
# * Cover nominal, boundary, and error scenarios. 
# 
# */ /* Full focal class code 
# */ $focal_class_code$ 
# 
# """

#!/usr/bin/env python3
"""
Generate LLM prompts for regression-oriented Java tests.

Config:
- ROOT_DIR: base dataset folder containing BBC* directories
- CSV_PATH_IN: CSV with at least: custom_id, test_framework
- OUTPUT_ROOT: where prompts are written (per-bump subfolder, only created if a prompt is written)
- USAGE_SUBPATH: subfolder under each bump dir that contains *.json usage reports

"""

import csv
import json
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PRIMARY_DRIVE

# === CONFIG ===
ROOT_DIR = PRIMARY_DRIVE / "Dataset/StaticAnalysis"
CSV_PATH_IN = PRIMARY_DRIVE / "updated_FinalBUMP_Instances_with_TestRunner.csv"
OUTPUT_ROOT = ROOT_DIR.parent.parent / "GeneratedPromptsClientsExp7"
USAGE_SUBPATH = "UsageReport"
CLONED_REPO_ROOT = PRIMARY_DRIVE / "Dataset/ClonedRepo/Clients"


# -------------------------
# CSV mapping
# -------------------------
def load_test_framework_map(csv_path: Path):
    """Return a map: custom_id -> test_framework (verbatim from CSV)."""
    by_custom_id = {}
    if not csv_path.exists():
        print(f"[ERROR] CSV not found at {csv_path}")
        return by_custom_id

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("custom_id") or "").strip()
            tf = (row.get("test_framework") or "").strip()
            if cid and tf:
                by_custom_id[cid] = tf
    return by_custom_id


# -------------------------
# Resolve focal class path
# -------------------------
def resolve_focal_class_path(bump_id: str, file_path: str) -> Optional[Path]:
    """
    Find the full path to the focal class inside Clients/{bump_id}/...
    Works even if there are extra module folders before src/.
    """
    root = CLONED_REPO_ROOT / bump_id
    if not root.exists():
        return None

    # 1) Direct join attempt
    candidate = root / file_path
    if candidate.exists():
        return candidate

    # 2) Recursive search: look for files whose path ends with file_path
    try:
        matches = [p for p in root.rglob(Path(file_path).name) if str(p).endswith(file_path)]
    except RecursionError:
        matches = []
    if matches:
        return min(matches, key=lambda p: len(str(p)))

    return None


# -------------------------
# Infer OSS library
# -------------------------
def infer_oss_library_name(usages: list) -> str:
    """Infer OSS library/package name from usages."""
    for u in usages or []:
        cls = (u.get("className") or "").strip()
        if cls and "." in cls:
            return cls.rsplit(".", 1)[0]
        elif cls:
            return cls
    for u in usages or []:
        fq = (u.get("fullyQualifiedName") or "").strip()
        if fq and "." in fq:
            return fq.rsplit(".", 1)[0]
        elif fq:
            return fq
    return "UnknownLibrary"


# -------------------------
# Prompt generator
# -------------------------
def generate_prompt_from_usage_block(block, class_name, test_runner: str, bump_id: str):
    # Metadata
    client_project = block.get("clientProject", "UnknownProject")
    oss_version = block.get("libraryVersion", "UnknownVersion")
    focal_class_fqn = block.get("clientClass", "UnknownClass")
    method_name = block.get("methodName", "unknownMethod")
    param_types = block.get("paramTypes", []) or []
    return_type = block.get("returnType", "void")
    file_path = block.get("filePath")

    # OSS library
    oss_library = infer_oss_library_name(block.get("libraryUsages", []))

    # Dependencies (deduped)
    dep_set = set()
    for u in block.get("libraryUsages", []):
        usage_type = u.get("usageType")
        fq = u.get("fullyQualifiedName", "Unknown")

        if usage_type == "method_call":
            args = ", ".join(u.get("argumentTypes") or [])
            ret = u.get("returnType", "Unknown")
            dep_set.add(f"{fq}({args}): {ret}")
        elif usage_type == "type_reference":
            dep_set.add(f"{fq}   // type reference")
        elif usage_type == "constructor_call":
            args = ", ".join(u.get("argumentTypes") or [])
            dep_set.add(f"{fq} constructor({args})")
        elif usage_type == "field_access":
            dep_set.add(f"{fq}   // field access")

    deps = sorted(dep_set)

    # Focal class code
    focal_class_code = ""
    if file_path:
        src_path = resolve_focal_class_path(bump_id, file_path)
        if src_path and src_path.exists():
            try:
                focal_class_code = src_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                focal_class_code = f"// [ERROR reading {src_path}: {e}]"
        else:
            focal_class_code = f"// [ERROR: could not locate focal class source for filePath='{file_path}']"

    # Build prompt
    prompt = f"""---- 1. Metadata ---
- Client Project: {client_project}
- OSS Library: {oss_library}
- OSS Version: {oss_version}

---- 2. Program context ---
- Focal class FQN: {focal_class_fqn}
- Focal method signature: {method_name}({', '.join(param_types)}): {return_type}

---- 3. Test code format for LLM ---
/* Test framework: {test_runner} */
public class {class_name} {{

    // Deterministic necessary object declaration
    @Test
    public void test_{method_name}() {{

        // Act: call focal method

        /* Dependency methods invoked by this focal method */
{chr(10).join("        " + d for d in deps) if deps else "        // None"}

        // Assert: strong deterministic checks (values, ordering, exception types, invariants)
    }}
}}

 ---- 4. Test goal ---
/**
 * Test intention:
 * - Detect breaking changes in {oss_library} by executing the focal method {method_name}.
 * - Cover nominal, boundary, and error scenarios with deterministic assertions
 *   (values, ordering, exceptions, invariants).
 *
 * Constraints:
 * - Output ONLY a complete, compilable Java test class named {class_name}.
 * - Include all required imports (e.g., correct @Test annotation for {test_runner}).
 * - Do NOT use mocking or stubbing frameworks (Mockito, EasyMock, etc.).
 * - Do NOT leave empty catch blocks; if exceptions are expected, assert them explicitly.
 * - Do NOT include unused or redundant imports.
 * - Ensure all braces are properly closed; the code must compile as-is.
 * - Do NOT output explanations, comments, or text outside the Java code.
 */



/* Full focal class code for additional context */
{focal_class_code}
"""
    return prompt


# -------------------------
# Processing
# -------------------------
def process_bump_instance(bump_dir: Path, by_custom_id: dict):
    usage_dir = bump_dir / USAGE_SUBPATH
    json_files = sorted(usage_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON file found in {usage_dir}")
        return

    json_file = json_files[0]
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            usage_blocks = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load {json_file}: {e}")
        return

    if not isinstance(usage_blocks, list):
        usage_blocks = [usage_blocks]

    wrote_any = False

    for idx, usage_block in enumerate(usage_blocks):
        class_name = f"{bump_dir.name}U{idx}Test"
        test_runner = by_custom_id.get(bump_dir.name)
        if not test_runner:
            print(f"[SKIP] No test_framework entry in CSV for {bump_dir.name}")
            continue

        prompt = generate_prompt_from_usage_block(usage_block, class_name, test_runner, bump_dir.name)
        if prompt:
            output_dir = OUTPUT_ROOT / bump_dir.name
            if not wrote_any:
                output_dir.mkdir(parents=True, exist_ok=True)

            output_path = output_dir / f"{class_name}_prompt.txt"
            try:
                with open(output_path, "w", encoding="utf-8") as out_f:
                    out_f.write(prompt)
                wrote_any = True
                print(f"Saved: {output_path.name} [runner='{test_runner}']")
            except Exception as e:
                print(f"[ERROR] Could not write {output_path}: {e}")

    if not wrote_any:
        print(f"No prompts generated for {bump_dir.name}")


# -------------------------
# Main
# -------------------------
def main():
    by_custom_id = load_test_framework_map(CSV_PATH_IN)
    for bump_dir in sorted(ROOT_DIR.iterdir()):
        if bump_dir.is_dir() and bump_dir.name.startswith("BBC"):
            print(f"\nProcessing {bump_dir.name}")
            process_bump_instance(bump_dir, by_custom_id)
    print("\nAll prompts processed.")


if __name__ == "__main__":
    main()
