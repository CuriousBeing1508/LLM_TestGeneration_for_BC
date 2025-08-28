#!/usr/bin/env python3
"""
Generate LLM prompts for regression-oriented Java tests.

Config:
- ROOT_DIR: base dataset folder containing BBC* directories
- CSV_PATH_IN: CSV with at least: custom_id, test_framework
- OUTPUT_ROOT: where prompts are written (per-bump subfolder, only created if a prompt is written)
- USAGE_SUBPATH: subfolder under each bump dir that contains *.json usage reports

Behavior:
- Pulls `test_framework` per bump directly from CSV (verbatim).
- If a bump_id (custom_id) has no entry in CSV or the field is empty, skip it (no default).
- Forbids environment setup (Docker already configured).
- Skips creating output folders unless at least one prompt is saved for that bump.
"""

import csv
import json
from pathlib import Path

# === CONFIG ===
ROOT_DIR = Path("/Volumes/Rachna-HD/Dataset/StaticAnalysis")
CSV_PATH_IN = Path("/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv")
OUTPUT_ROOT = ROOT_DIR.parent / "GeneratedPromptsClientsExp6"
USAGE_SUBPATH = "UsageReport"


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
# Prompt generator
# -------------------------
def generate_prompt_from_usage_block(block, class_name, test_runner: str):
    library_usages = [
        u for u in block.get("libraryUsages", [])
        if u.get("usageType") == "method_call"
    ]
    if not library_usages:
        return None

    client_project = block.get("clientProject", "UnknownProject")
    library_version = block.get("libraryVersion", "UnknownVersion")
    client_class = block.get("clientClass", "UnknownClass")
    method_name = block.get("methodName", "unknownMethod")
    method_source = (block.get("methodSource", "") or "").strip()

    prompt_lines = [
        "You are generating a Java unit test to validate usage of a library method in a real project context.",
        "",
        "### Context:",
        f"- Project: `{client_project}`",
        f"- Client class: `{client_class}`",
        f"- Library version: `{library_version}`",
        f"- Client method: `{method_name}`",
        f"- Full method body:\n{method_source}",
        "",
        "### Target Library Usages:",
    ]

    for usage in sorted(library_usages, key=lambda u: (u.get("fullyQualifiedName") or "")):
        method = usage.get("fullyQualifiedName", "UnknownMethod")
        receiver = usage.get("className", "UnknownClass")
        arg_types = usage.get("argumentTypes", []) or []
        ret_type = usage.get("returnType", "UnknownReturnType")
        arguments = usage.get("arguments", []) or []

        prompt_lines.append(f"- Method: `{method}`")
        prompt_lines.append(f"  - Receiver type: `{receiver}`")
        prompt_lines.append(f"  - Argument types: `{', '.join(arg_types) if arg_types else 'None'}`")
        prompt_lines.append(f"  - Return type: `{ret_type}`")
        if arguments:
            prompt_lines.append(f"  - Example arguments: `{', '.join(arguments)}`")

    # --- Test Framework section (verbatim from CSV)
    prompt_lines.append(f"""
### Test Framework
- Runner: **{test_runner}**
""".strip())

    # --- Objective section (merged, concise)
    prompt_lines.append(f"""
### Objective
Write a **self-contained** Java test class named `{class_name}` that:

- Generates **compilable and runnable tests** for the observed library usages.
- Includes **multiple @Test methods** (add as many as needed to cover distinct behaviors: nominal, boundary, error, nullability, ordering, idempotence, etc.).
- Uses **strong assertions**:
  - Exact values, sizes, and ordering for collections/strings.
  - Exact exception TYPE (and stable message substring if meaningful).
  - State invariants: inputs unchanged (immutability), idempotence, no unintended side effects.
- Use the **{test_runner}** framework only. Do **not** use mocking frameworks.
- **Environment-free**:
  - Assume Docker image has all runtime config.
  - Do NOT use files, network, env vars, system properties, time, locale, or randomness.
  - Build all inputs deterministically in-memory.
- (Optional) If methods are overloaded, add a reflection check for the intended signature.

### Output Format
Only output the complete Java file — no explanation, no Markdown.
""")

    return "\n".join(prompt_lines).strip()


# -------------------------
# Processing a single bump
# -------------------------
def process_bump_instance(bump_dir: Path, by_custom_id: dict):
    usage_dir = bump_dir / USAGE_SUBPATH
    json_files = sorted(usage_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON file found in {usage_dir}")
        return

    if len(json_files) > 1:
        print(f"Multiple JSON files found in {usage_dir}, using only the first one.")
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

        # Lookup custom_id (folder name) in CSV map
        test_runner = by_custom_id.get(bump_dir.name)
        if not test_runner:
            print(f"[SKIP] No test_framework entry in CSV for {bump_dir.name}")
            continue

        prompt = generate_prompt_from_usage_block(usage_block, class_name, test_runner)
        if prompt:
            if not wrote_any:
                output_dir = OUTPUT_ROOT / bump_dir.name
                output_dir.mkdir(parents=True, exist_ok=True)
            else:
                output_dir = OUTPUT_ROOT / bump_dir.name

            output_path = output_dir / f"{class_name}_prompt.txt"
            try:
                with open(output_path, "w", encoding="utf-8") as out_f:
                    out_f.write(prompt)
                wrote_any = True
                print(f"Saved: {output_path.name} [runner='{test_runner}']")
            except Exception as e:
                print(f"[ERROR] Could not write {output_path}: {e}")
        else:
            print(f"Skipping {json_file.name}, block {idx}: No method_call usage")

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
