import re
import csv
from pathlib import Path
import xml.etree.ElementTree as ET
# This is the baseline transplantation, to resolve library dependency, 
# I decided to take it from client's pom. So matching the dependency group id and injecting all necessary library dependency.
# === CONFIGURATION ===
LLM_NAME = "GPT4o"
PROMPT_VERSION = "promptv1"

OUTPUT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/Generated_Output_Library") / LLM_NAME
PROJECT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ExecutableProjects_Baseline")
CLIENT_PROJECTS_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ClonedRepo/clients")
METADATA_CSV = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/FinalBUMP_Instances.csv")

# === POM FILE TEMPLATE ===
POM_TEMPLATE = """<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.generated</groupId>
    <artifactId>{project_name}</artifactId>
    <version>1.0-SNAPSHOT</version>
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

# === STEP 1: TRANSPLANT UTILITIES ===
def get_clean_class_name(txt_file: Path) -> str:
    return txt_file.stem.replace("_prompt", "")

def create_java_project_if_needed(bump_id: str, version: str) -> Path:
    project_name = f"{bump_id}_{version}"
    project_dir = PROJECT_ROOT / bump_id / project_name
    if not project_dir.exists():
        print(f"Creating Java project: {project_name}")
        project_dir.mkdir(parents=True, exist_ok=True)
        pom_path = project_dir / "pom.xml"
        with open(pom_path, "w", encoding="utf-8") as pom_file:
            pom_file.write(POM_TEMPLATE.format(project_name=project_name))
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

def process_bump(bump_id: str):
    input_dir = OUTPUT_ROOT / bump_id
    if not input_dir.exists():
        print(f"⚠️ Output folder missing for {bump_id}")
        return

    txt_files = sorted(input_dir.glob("*.txt"))
    if not txt_files:
        print(f"⚠️ No test files in {input_dir}")
        return

    print(f"\nProcessing BUMP: {bump_id}")
    project_next = create_java_project_if_needed(bump_id, "next")
    project_prev = create_java_project_if_needed(bump_id, "prev")

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

# === STEP 2: DEPENDENCY INJECTION UTILITIES ===
def parse_metadata_csv(csv_path: Path):
    metadata = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row['custom_id'].strip()
            group_id = row['dependencyGroupID'].strip()
            artifact_id = row['dependencyArtifactID'].strip()
            metadata.append((custom_id, group_id, artifact_id))
    return metadata

# def extract_library_dependencies(client_pom: Path, group_id: str):
#     tree = ET.parse(client_pom)
#     ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
#     dependencies = []

#     for dep in tree.findall(".//m:dependency", ns):
#         gid = dep.find("m:groupId", ns)
#         if gid is not None and gid.text.strip() == group_id:
#             dependencies.append(ET.tostring(dep, encoding='unicode'))
#     return dependencies

def extract_library_dependencies(client_pom: Path, group_id: str):
    tree = ET.parse(client_pom)
    ns = {'m': 'http://maven.apache.org/POM/4.0.0'}

    # Phase 1: relaxed but still directly related match
    primary_matches = []
    for dep in tree.findall(".//m:dependency", ns):
        gid = dep.find("m:groupId", ns)
        if gid is not None:
            actual_gid = gid.text.strip()
            if group_id in actual_gid or actual_gid.startswith(group_id) or group_id.startswith(actual_gid):
                primary_matches.append(ET.tostring(dep, encoding='unicode'))

    if primary_matches:
        return primary_matches

    # Phase 2: generalized prefix fallback (same ecosystem)
    fallback_matches = []
    base_input = ".".join(group_id.strip().split(".")[:2])
    for dep in tree.findall(".//m:dependency", ns):
        gid = dep.find("m:groupId", ns)
        if gid is not None:
            actual_gid = gid.text.strip()
            base_actual = ".".join(actual_gid.split(".")[:2])
            if base_input == base_actual:
                fallback_matches.append(ET.tostring(dep, encoding='unicode'))

    return fallback_matches



def update_transplant_pom(pom_path: Path, extra_deps):
    tree = ET.parse(pom_path)
    ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
    ET.register_namespace('', ns['m'])

    root = tree.getroot()
    deps_elem = root.find(".//m:dependencies", ns)
    if deps_elem is None:
        deps_elem = ET.SubElement(root, "dependencies")

    for dep_xml in extra_deps:
        dep_elem = ET.fromstring(dep_xml)
        deps_elem.append(dep_elem)

    tree.write(pom_path, encoding="utf-8", xml_declaration=True)

# === STEP 3: COMBINED WORKFLOW ===
def process_bump_with_library_injection(custom_id: str, group_id: str, artifact_id: str):
    process_bump(custom_id)

    client_pom = CLIENT_PROJECTS_ROOT / custom_id / "pom.xml"
    if not client_pom.exists():
        print(f"❌ Client pom.xml not found for {custom_id}")
        return

    extra_deps = extract_library_dependencies(client_pom, group_id)
    if not extra_deps:
        print(f"⚠️ No matching dependencies found for groupId '{group_id}' in client {custom_id}")
        return

    for version in ["next", "prev"]:
        project_dir = PROJECT_ROOT / custom_id / f"{custom_id}_{version}"
        pom_path = project_dir / "pom.xml"
        if pom_path.exists():
            print(f"Injecting {len(extra_deps)} dependency(ies) into {pom_path.name}")
            update_transplant_pom(pom_path, extra_deps)

# === MAIN EXECUTION ===
if __name__ == "__main__":
    metadata_entries = parse_metadata_csv(METADATA_CSV)
    print(f"\nFound {len(metadata_entries)} BUMP instance(s) in metadata.\n")

    for custom_id, group_id, artifact_id in metadata_entries:
        process_bump_with_library_injection(custom_id, group_id, artifact_id)

    print("\n✅ ALL TRANSPLANTS AND DEPENDENCY INJECTIONS COMPLETED.")