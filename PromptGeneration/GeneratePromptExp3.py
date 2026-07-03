# This prompt script is after canary replicating the experiment 7 prompt style.
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
# */ /* No focal context is provided, just the fully qualified method names of the third party library.
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
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PRIMARY_DRIVE

# === CONFIG ===
ROOT_DIR = PRIMARY_DRIVE / "Dataset/StaticAnalysis"
CSV_PATH_IN = PRIMARY_DRIVE / "ConfigFiles" / "FinalCandidateBUMPUsed.csv"
OUTPUT_ROOT = ROOT_DIR.parent.parent / "GeneratedPromptsClientsExp3"
USAGE_SUBPATH = "UsageReport"
CLONED_REPO_ROOT = PRIMARY_DRIVE / "Dataset/ClonedRepo/Clients"


# -------------------------
# CSV mapping
# -------------------------
def load_bump_metadata_map(csv_path: Path):
    """Return a map: custom_id -> {client_name, old_version, new_version, test_framework}."""
    by_custom_id = {}
    if not csv_path.exists():
        print(f"[ERROR] CSV not found at {csv_path}")
        return by_custom_id

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("custom_id") or "").strip()
            tf = (row.get("test_framework") or "").strip()
            if not cid or not tf:
                continue
            by_custom_id[cid] = {
                "client_name": (row.get("clientProject") or "").strip(),
                "old_version": (row.get("previousVersion") or "").strip(),
                "new_version": (row.get("newVersion") or "").strip(),
                "test_framework": tf,
            }
    return by_custom_id


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
# Method signature parsing
# -------------------------
_MODIFIERS = {"public", "private", "protected", "static", "final", "abstract",
              "synchronized", "native", "default", "strictfp", "transient"}


def _split_top_level(s: str, sep: str) -> list:
    """Split on sep, ignoring separators nested inside <...> or (...)."""
    parts = []
    depth = 0
    current = []
    for ch in s:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def parse_method_signature(method_source: str, method_name: str):
    """Best-effort extraction of (return_type, param_types) from a method source
    snippet. Returns (None, None) if the declaration can't be confidently located."""
    if not method_source:
        return None, None

    # Drop annotation-only lines (e.g. "@Test") to reach the declaration line.
    header = "\n".join(
        ln for ln in method_source.split("\n") if not ln.strip().startswith("@")
    )

    pattern = re.compile(
        r"(?P<prefix>[\w.\[\]<>,\s]+?)\b" + re.escape(method_name) + r"\s*\((?P<params>[^)]*)\)"
    )
    m = pattern.search(header)
    if not m:
        return None, None

    return_type = None
    for tok in reversed(m.group("prefix").split()):
        if tok not in _MODIFIERS and not re.fullmatch(r"<.*>", tok):
            return_type = tok
            break

    param_types = []
    params_raw = m.group("params").strip()
    if params_raw:
        for part in _split_top_level(params_raw, ","):
            part = part.replace("final ", "").strip()
            if not part:
                continue
            tokens = part.split()
            param_types.append(" ".join(tokens[:-1]) if len(tokens) > 1 else tokens[0])

    return return_type, param_types


# -------------------------
# Prompt generator
# -------------------------
def generate_prompt_from_usage_block(block, class_name, row_entry: dict):
    client_class_fqn = block.get("clientClass", "UnknownClass")
    method_name = block.get("methodName", "unknownMethod")

    return_type, param_types = parse_method_signature(block.get("methodSource", ""), method_name)
    if param_types is not None:
        signature = f"({', '.join(param_types)})" + (f": {return_type}" if return_type else "")
        focal_method_fqn = f"{client_class_fqn}.{method_name}{signature}"
    else:
        focal_method_fqn = f"{client_class_fqn}.{method_name}"
    test_package_name = client_class_fqn.rsplit(".", 1)[0] if "." in client_class_fqn else "generated"
    test_class_name = class_name

    library_name = infer_oss_library_name(block.get("libraryUsages", []))
    old_version = row_entry["old_version"]
    new_version = row_entry["new_version"]
    testing_framework = row_entry["test_framework"]

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
    deps_str = "\n".join(f"  - {d}" for d in deps) if deps else "  - (none detected)"

    prompt = f"""---- 1. Metadata ----
- Client Project  : {row_entry['client_name']}
- OSS Library     : {library_name}
- Old Version     : {old_version}
- New Version     : {new_version}

---- 2. Program context ----
- Focal class FQN    : {client_class_fqn}
- Focal method FQN   : {focal_method_fqn}
- Test package       : {test_package_name}
- Test class name    : {test_class_name}

- Library API calls made by this method:
{deps_str}

---- 3. Test code format ----
/* Test framework: {testing_framework} */

package {test_package_name};

public class {test_class_name} {{

    @Test
    public void test_{method_name}_<scenario>() {{
        // Arrange: set up necessary objects

        // Act: call the focal method

        // Assert: strong deterministic checks
        //         (values, ordering, exception types, invariants)
    }}
}}

---- 4. Test goal ----
/**
 * PURPOSE
 * -------
 * Detect whether upgrading {library_name} from {old_version} to {new_version}
 * breaks the client's existing usage of the library.
 *
 * A breaking change means: code that worked correctly with {old_version} no
 * longer works, or produces different observable output, with {new_version}.
 * A test detects this by PASSING on {old_version} and FAILING on {new_version}.
 * A test that passes on both versions detects nothing — write zero such tests.
 *
 * WHAT TO TEST: THE CLIENT'S USAGE CHAIN
 * ---------------------------------------------------------------------
 * The focal method (section 3) calls the library APIs listed in section 2.
 * Your tests must replicate that same usage chain with concrete inputs and
 * then assert on the OBSERVABLE OUTPUT or SIDE EFFECT it produces.
 * Do NOT write tests that call each library API in isolation as a smoke test.


 * STEP-BY-STEP REASONING (apply this to every test you write)
 * ------------------------------------------------------------
 * Step 1 — Identify the usage chain.
 *   Read section 3. Find the sequence of library calls the focal method makes
 *   and the final result it computes (return value, state change, side effect).
 *
 * Step 2 — Choose a simple, old-version-safe input.
 *   Tests run on {old_version} FIRST. If a test cannot run on {old_version},
 *   it is discarded entirely and detects nothing.
 *
 * Step 3 — Replicate the usage chain in the test.
 *   Call the same library APIs in the same order with the same configuration
 *   as the focal method does.
 *
 * Step 4 — Assert on the concrete output.
 *   Look at what the usage chain PRODUCES and assert on its content.
    Cover nominal, boundary, and error scenarios with deterministic assertions (values, ordering, exceptions, invariants)
 *
 * Step 5 — Ask the version-sensitivity question.
 *   Before finalising each assertion, ask: "Could this assertion ever fail if
 *   {library_name} changed how it handles this input between versions?"
 *   If the answer is NO — the assertion would pass on every version — discard the test and write a more specific one.
 *
 * FORMAT REQUIREMENTS
 * -------------------
 * - Output ONLY a complete compilable Java test class.
 * - Use ONLY {testing_framework} annotations and assertions.
 * - Do NOT use mocking or stubbing (Mockito, EasyMock, etc.).
 * - Do NOT include unused imports.
 * - All braces must be properly closed.
 * - Do NOT output explanations or text outside the Java class.
 */

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
        row_entry = by_custom_id.get(bump_dir.name)
        if not row_entry:
            print(f"[SKIP] No CSV entry for {bump_dir.name}")
            continue

        prompt = generate_prompt_from_usage_block(usage_block, class_name, row_entry)
        if prompt:
            output_dir = OUTPUT_ROOT / bump_dir.name
            if not wrote_any:
                output_dir.mkdir(parents=True, exist_ok=True)

            output_path = output_dir / f"{class_name}_prompt.txt"
            try:
                with open(output_path, "w", encoding="utf-8") as out_f:
                    out_f.write(prompt)
                wrote_any = True
                print(f"Saved: {output_path.name} [runner='{row_entry['test_framework']}']")
            except Exception as e:
                print(f"[ERROR] Could not write {output_path}: {e}")

    if not wrote_any:
        print(f"No prompts generated for {bump_dir.name}")


# -------------------------
# Main
# -------------------------
def main():
    by_custom_id = load_bump_metadata_map(CSV_PATH_IN)
    for bump_dir in sorted(ROOT_DIR.iterdir()):
        if bump_dir.is_dir() and bump_dir.name.startswith("BBC"):
            print(f"\nProcessing {bump_dir.name}")
            process_bump_instance(bump_dir, by_custom_id)
    print("\nAll prompts processed.")


if __name__ == "__main__":
    main()
