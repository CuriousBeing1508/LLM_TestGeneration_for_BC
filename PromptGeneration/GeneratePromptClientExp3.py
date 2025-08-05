import json
from pathlib import Path

ROOT_DIR = Path("/Volumes/Rachna-HD/Dataset/StaticAnalysis")
OUTPUT_ROOT = ROOT_DIR.parent / "GeneratedPromptsClientsExp3"
USAGE_SUBPATH = "UsageReport"

def generate_prompt_from_usage_block(block, class_name):
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
    method_source = block.get("methodSource", "").strip()

    prompt_lines = [
        f"You are generating a Java unit test to validate usage of a library method in a real project context.",
        "",
        f"### Context:",
        f"- Project: `{client_project}`",
        f"- Client class: `{client_class}`",
        f"- Library version: `{library_version}`",
        f"- Client method: `{method_name}`",
        f"- Full method body:\n{method_source}",
        "",
        f"### Target Library Usages:"
    ]

    for usage in sorted(library_usages, key=lambda u: u.get("fullyQualifiedName", "")):
        method = usage.get("fullyQualifiedName", "UnknownMethod")
        receiver = usage.get("className", "UnknownClass")
        arg_types = usage.get("argumentTypes", [])
        ret_type = usage.get("returnType", "UnknownReturnType")
        arguments = usage.get("arguments", [])

        prompt_lines.append(f"- Method: `{method}`")
        prompt_lines.append(f"  - Receiver type: `{receiver}`")
        prompt_lines.append(f"  - Argument types: `{', '.join(arg_types) if arg_types else 'None'}`")
        prompt_lines.append(f"  - Return type: `{ret_type}`")
        if arguments:
            prompt_lines.append(f"  - Example arguments: `{', '.join(arguments)}`")

    prompt_lines.append(f"""
### Objective:
Write a **self-contained** Java class named `{class_name}` that:
- Instantiates and configures necessary objects for calling the above method(s).
- Reconstructs setup logic seen in the method body if needed (e.g. visitors, etc.).
- Uses assertions to verify return values or method effects (e.g., non-null, correct structure).
- DO NOT use mocking frameworks like Mockito.
- Uses only Java 8 and JUnit 4 (no external dependencies).
- Includes all import statements and compiles without modification.

### Output Format:
Only output the complete Java file — no explanation, no Markdown formatting.
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
            print(f"\n Processing {bump_dir.name}")
            process_bump_instance(bump_dir)
    print("\n All prompts generated.")

if __name__ == "__main__":
    main()
