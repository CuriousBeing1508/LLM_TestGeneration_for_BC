import json
from pathlib import Path

# === CONFIG ===
# Poc Path
# ROOT_DIR = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/StaticAnalysis")
# OUTPUT_ROOT = ROOT_DIR.parent / "GeneratedPromptsWithVersionLibrary"
# # This is the folder inside the root directory where baseline (library usage) is stored.
# USAGE_SUBPATH = "LibraryUsageReport" 
# # this is the name of the json file where baseline library usage data is stored..
# USAGE_FILENAME = "library_usage.json"

# Experiment 1 path
ROOT_DIR = Path("/Volumes/Rachna-HD/Dataset/StaticAnalysis")
OUTPUT_ROOT = Path( "/Volumes/Rachna-HD/Dataset/GeneratedPromptsWithVersionLibrary")
# This is the folder inside the root directory where baseline (library usage) is stored.
USAGE_SUBPATH = "LibraryUsageReport" 
# this is the name of the json file where baseline library usage data is stored..
USAGE_FILENAME = "*.json"


def generate_prompt_from_usage_block(block, class_name):
    # Filter only method calls
    library_usages = [
        u for u in block.get("libraryUsages", [])
    ]
    if not library_usages:
        return None

    # Extract metadata
    client_project = block.get("clientProject", "UnknownProject")
    library_version = block.get("libraryVersion", "UnknownVersion")

    # Extract package prefix from first method
    first_method = library_usages[0].get("qualifiedMethod", "")
    parts = first_method.split(".")
    package_prefix = ".".join(parts[:-2]) if len(parts) >= 3 else "UnknownPackage"

    # Prompt header
    prompt_lines = [
        f"You are a code-generation assistant generating a standalone Java 8 test class named `{class_name}` using JUnit 4.",
        f"This test is based on usage from the library package `{package_prefix}` (version `{library_version}`).",
        "The goal is to simulate realistic usage of the following external methods:\n"
    ]

    # Method usages
    for i, usage in enumerate(sorted(library_usages, key=lambda u: u.get("fullyQualifiedName", "")), start=1):
        method_sig = usage.get("fullyQualifiedName", "UnknownMethod")
        arguments = usage.get("argumentTypes", [])
        prompt_lines.append(f"{i}) {method_sig}")
        if arguments:
            prompt_lines.append(f"   → argumentTypes used: {', '.join(arguments)}")

    # Final instruction block
    prompt_lines.append(f"""
Write a **self-contained** test class named `{class_name}` that:
- Invokes all of the listed methods in a way that compiles.
- Realistically demonstrates how a client might invoke these methods.
- Includes dummy values, helper classes, or mocks as needed so the code compiles.
- Does not assert correctness — show real usage calls (tests may fail if behavior changed).
- Includes all necessary imports.
- Returns only one code block with the complete Java file, nothing else.

This test will be used to detect **breaking changes** between versions of the library. Accuracy and compilability are essential.
""")

    return "\n".join(prompt_lines).strip()

def process_bump_instance(bump_dir):
    usage_file = bump_dir / USAGE_SUBPATH / USAGE_FILENAME

    if not usage_file.exists():
        print(f" Skipping {bump_dir.name} — '{USAGE_FILENAME}' not found in LibraryUsageReport")
        return
    else:
        output_dir = OUTPUT_ROOT / bump_dir.name
        output_dir.mkdir(parents=True, exist_ok=True)

    print(f"🔍 Reading usage file: {usage_file.name} from {bump_dir.name}")

    try:
        with open(usage_file, "r", encoding="utf-8") as f:
            usage_blocks = json.load(f)
            
    except Exception as e:
        print(f" Failed to read {usage_file.name}: {e}")
        return

    if not isinstance(usage_blocks, list):
        usage_blocks = [usage_blocks]
        
    
    for idx, usage_block in enumerate(usage_blocks):
        class_name = f"{bump_dir.name}U{idx}Test"
        output_path = output_dir / f"{class_name}_prompt.txt"

        prompt = generate_prompt_from_usage_block(usage_block, class_name)
        if prompt:
            with open(output_path, "w", encoding="utf-8") as out_f:
                out_f.write(prompt)
            print(f" Saved: {output_path.name}")
        else:
            print(f" Skipping block {idx} — No method_call usage in {usage_file.name}")

def main():
    for bump_dir in ROOT_DIR.iterdir():
        if bump_dir.is_dir() and bump_dir.name.startswith("BBC"):
            print(f"\n Processing {bump_dir.name}")
            process_bump_instance(bump_dir)

    print("\n All prompts generated.")

if __name__ == "__main__":
    main()
