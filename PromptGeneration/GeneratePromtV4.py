import json
import csv
from pathlib import Path
# this script is used after successful canary test experiment.
# === CONFIG ===
ROOT_DIR = Path("/Volumes/Rachna-HD/Dataset/StaticAnalysis")
CSV_PATH_IN = Path("/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv")
OUTPUT_ROOT = ROOT_DIR.parent / "GeneratedPromptsClientsExp4"
USAGE_SUBPATH = "UsageReport"

# === Load CSV with test_framework per bump_dir ===
TEST_RUNNER_MAP = {}
with open(CSV_PATH_IN, newline='', encoding="utf-8") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        bump_dir_name = row.get("bump_dir") or row.get("bump_name") or row.get("BUMP_NAME")
        test_framework = row.get("test_framework", "JUnit4").strip()
        if bump_dir_name:
            TEST_RUNNER_MAP[bump_dir_name.strip()] = test_framework

# === Prompt generator ===
def generate_prompt_from_usage_block(block, class_name, test_runner="JUnit4"):
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

    prompt_lines = []

    # === Section 1: Context ===
    prompt_lines.append("You are generating a Java unit test to validate the usage of one or more library methods in the context of a real-world project. This test should help detect regressions such as breaking changes across versions.\n")
    prompt_lines.append("---")
    prompt_lines.append("### 1. Program Context")
    prompt_lines.append(f"- Project Name: {client_project}")
    prompt_lines.append(f"- Client Class: {client_class}")
    prompt_lines.append(f"- Library Version: {library_version}")
    prompt_lines.append(f"- Focal Method: {method_name}")
    prompt_lines.append(f"\n```java\n// Full source of the client method:\n{method_source}\n```")

    # === Section 2: Usage Info ===
    prompt_lines.append("\n---")
    prompt_lines.append("### 2. Target Library Usages")
    for usage in sorted(library_usages, key=lambda u: u.get("fullyQualifiedName", "")):
        method = usage.get("fullyQualifiedName", "UnknownMethod")
        receiver = usage.get("className", "UnknownClass")
        arg_types = usage.get("argumentTypes", [])
        ret_type = usage.get("returnType", "UnknownReturnType")
        arguments = usage.get("arguments", [])

        prompt_lines.append(f"- Fully Qualified Method: {method}")
        prompt_lines.append(f"  - Receiver Type: {receiver}")
        prompt_lines.append(f"  - Argument Types: {', '.join(arg_types) if arg_types else 'None'}")
        prompt_lines.append(f"  - Return Type: {ret_type}")
        if arguments:
            prompt_lines.append(f"  - Example Arguments: {', '.join(arguments)}")

    # === Section 3: Objective ===
    prompt_lines.append("\n---")
    prompt_lines.append("### 3. Objective")
    prompt_lines.append(f"""Write a **self-contained Java class** named `{class_name}` that:

1. Instantiates and configures necessary objects to call the above method(s).
2. Reconstructs any logic required from the focal method (e.g., visitor patterns, setup code).
3. Uses **assertions** to verify correctness (e.g., output value, non-null, size, etc.).
4. Avoids using mocking frameworks (like Mockito).
5. Uses **Java 8** and **{test_runner}** (no external libraries).
6. Includes **all necessary import statements** and must **compile without modifications**.""")

    # === Section 4: Skeleton ===
    prompt_lines.append("\n---")
    prompt_lines.append("### 4. Test Class Skeleton (for reference)")
    prompt_lines.append(f"""```java
public class {class_name} {{

    // Test setup (instantiate focal object, setup environment, etc.)

    @Test
    public void test_{method_name}() {{
        // Invoke the target method(s)
        // Assert the correctness of result
    }}
}}
```""")

    # === Section 5: Output Format ===
    prompt_lines.append("\n---")
    prompt_lines.append("### 5. Output Format")
    prompt_lines.append("Only output the complete Java test class as a single code file. Do **not** include explanations, comments, or Markdown formatting.")

    return "\n".join(prompt_lines).strip()


# === Process Each BUMP Instance ===
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

    test_runner = TEST_RUNNER_MAP.get(bump_dir.name, "JUnit4")

    for idx, usage_block in enumerate(usage_blocks):
        class_name = f"{bump_dir.name}U{idx}Test"
        output_path = output_dir / f"{class_name}_prompt.txt"
        prompt = generate_prompt_from_usage_block(usage_block, class_name, test_runner)
        if prompt:
            with open(output_path, "w", encoding="utf-8") as out_f:
                out_f.write(prompt)
            print(f"Saved: {output_path.name}")
        else:
            print(f"Skipping {json_file.name}, block {idx}: No method_call usage")


# === Main Driver ===
def main():
    for bump_dir in ROOT_DIR.iterdir():
        if bump_dir.is_dir() and bump_dir.name.startswith("BBC"):
            print(f"\nProcessing {bump_dir.name}")
            process_bump_instance(bump_dir)
    print("\nAll prompts generated.")


if __name__ == "__main__":
    main()
