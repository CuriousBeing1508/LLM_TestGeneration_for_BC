import re
import csv
from pathlib import Path
import xml.etree.ElementTree as ET

# === CONFIGURATION ===
LLM_NAME = "GPT4o"
PROMPT_VERSION = "promptv1"

# OUTPUT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/LLMOutputLibrary") / LLM_NAME
# PROJECT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ExecutableProjects_baseline")
# CLIENT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ClonedRepo/clients")
# METADATA_CSV = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/FinalBUMP_Instances.csv")

OUTPUT_ROOT = Path("/Volumes/Rachna-HD/Dataset/LLMOutputLibrary") / LLM_NAME
PROJECT_ROOT = Path("/Volumes/Rachna-HD/Dataset/Exp1/ExecutableProjects_baseline")
CLIENT_ROOT = Path("/Volumes/Rachna-HD/Dataset/ClonedRepo/clients")
METADATA_CSV = Path("/Volumes/Rachna-HD/FinalBUMP_Instances.csv")

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

# === STEP 1: Extract <properties> from client pom ===
def extract_properties(bump_id: str) -> str:
    pom_path = CLIENT_ROOT / bump_id / "pom.xml"
    if not pom_path.exists():
        return ""
    try:
        tree = ET.parse(pom_path)
        ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
        props_elem = tree.find(".//m:properties", ns)
        if props_elem is None:
            return ""
        block = "    <properties>\n"
        for child in props_elem:
            tag = child.tag.split("}")[-1]
            block += f"        <{tag}>{child.text}</{tag}>\n"
        block += "    </properties>"
        return block
    except ET.ParseError:
        print(f" Could not parse properties for {bump_id}")
        return ""

# === STEP 2: Extract library-specific dependencies only (based on groupId match logic) ===
def extract_library_dependencies(client_pom: Path, group_id: str) -> str:
    if not client_pom.exists():
        return ""
    try:
        tree = ET.parse(client_pom)
        ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
        deps = []

        # Phase 1: exact or relaxed matches
        for dep in tree.findall(".//m:dependency", ns):
            gid = dep.find("m:groupId", ns)
            if gid is not None:
                actual_gid = gid.text.strip()
                if group_id in actual_gid or actual_gid.startswith(group_id) or group_id.startswith(actual_gid):
                    block = ET.tostring(dep, encoding="unicode")
                    deps.append("        " + block.strip().replace("\n", "\n        ") + "\n")

        # Fallback to common prefix
        if not deps:
            base_input = ".".join(group_id.split(".")[:2])
            for dep in tree.findall(".//m:dependency", ns):
                gid = dep.find("m:groupId", ns)
                if gid is not None:
                    actual_gid = gid.text.strip()
                    base_actual = ".".join(actual_gid.split(".")[:2])
                    if base_input == base_actual:
                        block = ET.tostring(dep, encoding="unicode")
                        deps.append("        " + block.strip().replace("\n", "\n        ") + "\n")

        return "".join(deps)

    except Exception as e:
        print(f" Failed to extract dependencies from {client_pom.name}: {e}")
        return ""

# === CLASS TRANSPLANT HELPERS ===
def get_clean_class_name(txt_file: Path) -> str:
    return txt_file.stem.replace("_prompt", "")

def create_java_project_if_needed(bump_id: str, version: str, properties_block: str, extra_dependencies: str) -> Path:
    project_name = f"{bump_id}_{version}"
    project_dir = PROJECT_ROOT / bump_id / project_name
    if not project_dir.exists():
        print(f"Creating Java project: {project_name}")
        project_dir.mkdir(parents=True, exist_ok=True)
        pom_path = project_dir / "pom.xml"
        pom_content = POM_TEMPLATE.format(
            project_name=project_name,
            properties_block=properties_block,
            extra_dependencies=extra_dependencies
        )
        with open(pom_path, "w", encoding="utf-8") as f:
            f.write(pom_content)
    return project_dir

def transplant_file_to_project(project_dir: Path, class_name: str, java_code: str, llm_name: str, prompt_version: str):
    package_path = f"com.generated.{llm_name}.{prompt_version}"
    java_code = re.sub(r'^package\s+[\w.]+;\s*', '', java_code.strip(), flags=re.MULTILINE)
    java_code = f"package {package_path};\n\n{java_code}"

    dest_dir = project_dir / "src" / "test" / "java" / Path(package_path.replace(".", "/"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    java_path = dest_dir / f"{class_name}.java"

    if java_path.exists():
        print(f"Replacing existing: {java_path.name}")
    else:
        print(f"Creating: {java_path.name}")

    with open(java_path, "w", encoding="utf-8") as f:
        f.write(java_code)

# === WORKFLOW ===
def process_bump(bump_id: str, group_id: str):
    input_dir = OUTPUT_ROOT / bump_id
    if not input_dir.exists():
        print(f" Output folder missing for {bump_id}")
        return

    txt_files = sorted(input_dir.glob("*.txt"))
    if not txt_files:
        print(f" No .txt test files found for {bump_id}")
        return

    print(f"\n🔧 Processing BUMP: {bump_id}")
    client_pom = CLIENT_ROOT / bump_id / "pom.xml"
    properties_block = extract_properties(bump_id)
    extra_dependencies = extract_library_dependencies(client_pom, group_id)

    project_next = create_java_project_if_needed(bump_id, "next", properties_block, extra_dependencies)
    project_prev = create_java_project_if_needed(bump_id, "prev", properties_block, extra_dependencies)

    for txt_file in txt_files:
        class_name = get_clean_class_name(txt_file)
        with open(txt_file, "r", encoding="utf-8") as f:
            content = f.read()

        code_blocks = re.findall(r"```(?:java)?\s*([\s\S]*?)\s*```", content)
        if not code_blocks:
            print(f" Skipping {txt_file.name}: no valid code block")
            continue

        java_code = code_blocks[0].strip()
        transplant_file_to_project(project_next, class_name, java_code, LLM_NAME, PROMPT_VERSION)
        transplant_file_to_project(project_prev, class_name, java_code, LLM_NAME, PROMPT_VERSION)

# === ENTRY POINT ===
if __name__ == "__main__":
    with open(METADATA_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"\n📦 Found {len(rows)} BUMP instance(s) from metadata.\n")
    for row in rows:
        bump_id = row["custom_id"].strip()
        group_id = row["dependencyGroupID"].strip()
        process_bump(bump_id, group_id)

    print("\n ALL TRANSPLANTS AND DEPENDENCY INJECTIONS COMPLETED.")
