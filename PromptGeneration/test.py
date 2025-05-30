import json
import logging
from pathlib import Path

# === CONFIG ===
ROOT_DIR = Path("/Volumes/Rachna-HD/Dataset/StaticAnalysis")
OUTPUT_ROOT = Path("/Volumes/Rachna-HD/Dataset/GeneratedPromptLibrary")
USAGE_SUBPATH = "LibraryUsageReport"
USAGE_FILENAME_PATTERN = "*.json"
LOG_FILE = OUTPUT_ROOT / "library_prompt_generation_log.txt"

# === Setup Logging ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger()

def generate_prompt_from_usage_block(block, class_name, package_prefix, library_version):
    library_usages = [
        u for u in block.get("libraryUsages", [])
        if u.get("usageType") == "method_call"
    ]
    if not library_usages:
        return None

    prompt_lines = [
        f"You are a code-generation assistant generating a standalone Java 8 test class named `{class_name}` using JUnit 4.",
        f"This test is based on usage from the library package `{package_prefix}` (version `{library_version}`).",
        "The goal is to simulate realistic usage of the following external methods:\n"
    ]

    for i, usage in enumerate(sorted(library_usages, key=lambda u: u.get("fullyQualifiedName", "")), start=1):
        method_sig = usage.get("fullyQualifiedName", "UnknownMethod")
        arguments = usage.get("argumentTypes", [])
        prompt_lines.append(f"{i}) {method_sig}")
        if arguments:
            prompt_lines.append(f"   → argumentTypes used: {', '.join(arguments)}")

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
    usage_folder = bump_dir / USAGE_SUBPATH
    usage_files = list(usage_folder.glob(USAGE_FILENAME_PATTERN))

    if not usage_files:
        log.warning(f"Skipping {bump_dir.name} — No JSON files found in {USAGE_SUBPATH}")
        return

    for usage_file in usage_files:
        log.info(f"🔍 Reading {usage_file.name} from {bump_dir.name}")
        try:
            with open(usage_file, "r", encoding="utf-8") as f:
                usage_blocks = json.load(f)
        except Exception as e:
            log.error(f"Failed to read {usage_file.name}: {e}")
            continue

        if not isinstance(usage_blocks, list):
            usage_blocks = [usage_blocks]

        output_dir = OUTPUT_ROOT / bump_dir.name
        output_dir.mkdir(parents=True, exist_ok=True)

        for idx, usage_block in enumerate(usage_blocks):
            library_usages = usage_block.get("libraryUsages", [])
            method_calls = [u for u in library_usages if u.get("usageType") == "method_call"]
            if not method_calls:
                log.warning(f"Skipping block {idx} in {usage_file.name} — No method_call usage")
                continue

            first_method = method_calls[0].get("qualifiedMethod", "")
            parts = first_method.split(".")
            package_prefix = ".".join(parts[:-2]) if len(parts) >= 3 else "UnknownPackage"
            library_version = usage_block.get("libraryVersion", "UnknownVersion")

            class_name = f"{bump_dir.name}U{idx}Test"
            prompt = generate_prompt_from_usage_block(usage_block, class_name, package_prefix, library_version)

            if prompt:
                output_path = output_dir / f"{class_name}_prompt.txt"
                with open(output_path, "w", encoding="utf-8") as out_f:
                    out_f.write(prompt)
                log.info(f"✅ Saved: {output_path.name}")
            else:
                log.info(f"⏭️ Skipping {usage_file.name}, block {idx}: No valid prompt generated")

def main():
    for bump_dir in ROOT_DIR.iterdir():
        if bump_dir.is_dir() and bump_dir.name.startswith("BBC"):
            log.info(f"\n🔧 Processing {bump_dir.name}")
            process_bump_instance(bump_dir)

    log.info("\n✅ All prompts generated.")

if __name__ == "__main__":
    main()
