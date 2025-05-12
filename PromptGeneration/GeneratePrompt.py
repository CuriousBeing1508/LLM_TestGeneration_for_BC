import json
from pathlib import Path

# === CONFIG ===
ROOT_DIR = Path("/Volumes/Rachna-HD/StaticAnalysis")
OUTPUT_ROOT = ROOT_DIR.parent / "GeneratedPrompts"
USAGE_SUBPATH = "UsageReport"

def generate_prompt_from_usage_block(block, class_name):
    library_usages = [
        u for u in block.get("libraryUsages", [])
        if u.get("usageType") == "method_call"
    ]
    if not library_usages:
        return None

    prompt_lines = [
        f"You are a code-generation assistant generating a standalone Java 8 test class named `{class_name}` using JUnit 4.",
        "The goal is to simulate realistic usage of the following external methods:\n"
    ]

    method_counter = 1
    for usage in sorted(library_usages, key=lambda u: u.get("fullyQualifiedName", "")):
        method_sig = usage.get("fullyQualifiedName", "UnknownMethod")
        arguments = usage.get("argumentTypes", [])
        prompt_lines.append(f"{method_counter}) {method_sig}")
        if arguments:
            prompt_lines.append(f"   → argument Types used: {', '.join(arguments)}")
        method_counter += 1

    prompt_lines.append(f"""
Write a **self-contained** test class named `{class_name}` that:
- Invoke all of the listed methods in a way that compiles.
- Realistically demonstrates how a client might invoke these methods.
- Includes dummy values, helper classes, or mocks as needed so the code compiles.
- Does not assert correctness — show real usage calls (tests may fail if behavior changed).
- Includes all necessary imports.
- Returns only one code block with the complete Java file, nothing else.

This test will be used to detect **breaking changes** between versions of the library. Accuracy and compilability are essential.
""")

    return "\n".join(prompt_lines).strip()

def process_bump_instance(bump_dir):
    usage_dir = bump_dir / USAGE_SUBPATH
    json_files = list(usage_dir.glob("*.json"))

    if not json_files:
        print(f"No JSON file found in {usage_dir}")
        return
    if len(json_files) > 1:
        print(f"Multiple JSON files found in {usage_dir}, using only the first one.")

    json_file = json_files[0]

    with open(json_file, "r", encoding="utf-8") as f:
        usage_blocks = json.load(f)

    if not isinstance(usage_blocks, list):
        usage_blocks = [usage_blocks]

    output_dir = OUTPUT_ROOT / bump_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)

    for idx, usage_block in enumerate(usage_blocks):
        class_name = f"{bump_dir.name}U{idx}Test"
        output_path = output_dir / f"{class_name}_prompt.txt"

        prompt = generate_prompt_from_usage_block(usage_block, class_name)
        if prompt:
            with open(output_path, "w", encoding="utf-8") as out_f:
                out_f.write(prompt)
            print(f"Saved: {output_path.name}")
        else:
            print(f"Skipping {json_file.name}, block {idx}: No method_call usage")

def main():
    for bump_dir in ROOT_DIR.iterdir():
        if bump_dir.is_dir() and bump_dir.name.startswith("BBC"):
            print(f"\n🔍 Processing {bump_dir.name}")
            process_bump_instance(bump_dir)

    print("\nAll prompts generated.")

if __name__ == "__main__":
    main()
