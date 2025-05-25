import re
from pathlib import Path
import xml.etree.ElementTree as ET
# I am resolving the dependency issues by copying all dependencies required from client pom.
# === CONFIGURATION ===
LLM_NAME = "GPT4o"
PROMPT_VERSION = "promptv1"

OUTPUT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/Generated_output_with_client") / LLM_NAME
PROJECT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ExecutableProjects_Client")
CLIENT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ClonedRepo/clients")

# === POM FILE TEMPLATE ===
POM_TEMPLATE = """<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.generated</groupId>
    <artifactId>{project_name}</artifactId>
    <version>1.0-SNAPSHOT</version>
{properties_block}
    <dependencies>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.mockito</groupId>
            <artifactId>mockito-core</artifactId>
            <version>4.5.1</version>
            <scope>test</scope>
        </dependency>
{extra_dependencies}
    </dependencies>
    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>2.22.2</version>
            </plugin>
        </plugins>
    </build>
</project>
"""

# === UTILITIES ===

def extract_properties(bump_id: str) -> str:
    source_pom = CLIENT_ROOT / bump_id / "pom.xml"
    if not source_pom.exists():
        return ""

    try:
        tree = ET.parse(source_pom)
        root = tree.getroot()
        ns = {'m': 'http://maven.apache.org/POM/4.0.0'}

        props_elem = root.find("m:properties", ns)
        if props_elem is None:
            return ""

        props_block = "    <properties>\n"
        for child in props_elem:
            tag = child.tag.split("}")[-1]
            props_block += f"        <{tag}>{child.text}</{tag}>\n"
        props_block += "    </properties>"
        return props_block

    except ET.ParseError:
        print(f"⚠️ Failed to parse properties in pom.xml for {bump_id}")
        return ""

def extract_additional_dependencies(bump_id: str) -> str:
    source_pom = CLIENT_ROOT / bump_id / "pom.xml"
    print(source_pom)
    if not source_pom.exists():
        print(f"⚠️ No source pom.xml found for {bump_id}")
        return ""

    try:
        with open(source_pom, "r", encoding="utf-8") as f:
            text = f.read()

        # Extract all dependencies blocks
        dep_blocks = re.findall(r"<dependency>[\s\S]*?</dependency>", text)

        extra_deps = []
        for block in dep_blocks:
            if "junit" in block or "mockito-core" in block:
                continue
            extra_deps.append("        " + block.strip().replace("\n", "\n        ") + "\n")

        if extra_deps:
            print(f"✅ Found {len(extra_deps)} extra dependencies for {bump_id}")
        else:
            print(f"ℹ️ No extra dependencies found in {bump_id}")

        return "".join(extra_deps)

    except Exception as e:
        print(f"❌ Failed to read dependencies from pom.xml for {bump_id}: {e}")
        return ""

def get_clean_class_name(txt_file: Path) -> str:
    return txt_file.stem.replace("_prompt", "")

def create_java_project_if_needed(bump_id: str, version: str, properties_block: str, extra_dependencies: str) -> Path:
    project_name = f"{bump_id}_{version}"
    project_dir = PROJECT_ROOT / bump_id / project_name
    if not project_dir.exists():
        print(f"Creating Java project: {project_name}")
        project_dir.mkdir(parents=True, exist_ok=True)
        pom_path = project_dir / "pom.xml"

        # ✅ Pass all format variables, not just project_name
        formatted_pom = POM_TEMPLATE.format(
            project_name=project_name,
            properties_block=properties_block,
            extra_dependencies=extra_dependencies
        )

        with open(pom_path, "w", encoding="utf-8") as pom_file:
            pom_file.write(formatted_pom)

    return project_dir


def transplant_file_to_project(project_dir: Path, class_name: str, java_code: str, llm_name: str, prompt_version: str):
    package_path = f"com.generated.{llm_name}.{prompt_version}"
    package_decl = f"package {package_path};"

    java_code = re.sub(r'^package\s+[\w.]+;\s*', '', java_code.strip(), flags=re.MULTILINE)
    java_code = f"{package_decl}\n\n{java_code}"

    package_dir = project_dir / "src" / "test" / "java" / Path(package_path.replace(".", "/"))
    package_dir.mkdir(parents=True, exist_ok=True)
    java_file_path = package_dir / f"{class_name}.java"

    if java_file_path.exists():
        java_file_path.unlink()
        print(f"Replacing existing: {java_file_path.name}")
    else:
        print(f"Creating: {java_file_path.name}")

    with open(java_file_path, "w", encoding="utf-8") as f:
        f.write(java_code)

# === MAIN FUNCTION FOR ONE BUMP ID ===

def process_bump(bump_id: str):
    input_dir = OUTPUT_ROOT / bump_id
    if not input_dir.exists():
        print(f"Output folder missing for {bump_id}")
        return

    txt_files = sorted(input_dir.glob("*.txt"))
    if not txt_files:
        print(f"No test files in {input_dir}")
        return

    print(f"\nProcessing BUMP: {bump_id}")
    properties_block = extract_properties(bump_id)
    extra_dependencies = extract_additional_dependencies(bump_id)

    project_next = create_java_project_if_needed(bump_id, "next", properties_block, extra_dependencies)
    project_prev = create_java_project_if_needed(bump_id, "prev", properties_block, extra_dependencies)

    for txt_file in txt_files:
        class_name = get_clean_class_name(txt_file)
        with open(txt_file, "r", encoding="utf-8") as f:
            full_text = f.read()

        code_blocks = re.findall(r"```(?:java)?\s*([\s\S]*?)\s*```", full_text)
        if not code_blocks:
            print(f"⚠️  Skipping {txt_file.name}: no valid code block found.")
            continue

        java_code = code_blocks[0].strip()

        transplant_file_to_project(project_next, class_name, java_code, LLM_NAME, PROMPT_VERSION)
        transplant_file_to_project(project_prev, class_name, java_code, LLM_NAME, PROMPT_VERSION)

# === MAIN ENTRY POINT ===

if __name__ == "__main__":
    bump_folders = sorted([p.name for p in OUTPUT_ROOT.iterdir() if p.is_dir()])
    print(f"\nFound {len(bump_folders)} BUMP instance(s) under {OUTPUT_ROOT}\n")

    # test_bump_id = "BBC22"
    # process_bump(test_bump_id)
    # print("\nSINGLE BUMP TEST COMPLETED.")

    # Uncomment to process all:
    for bump_id in bump_folders:
        process_bump(bump_id)

    print("\nALL TRANSPLANTS COMPLETED.")
