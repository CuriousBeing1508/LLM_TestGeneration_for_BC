import xml.etree.ElementTree as ET
import pandas as pd
from pathlib import Path
from datetime import datetime
import re

# === CONFIGURATION ===
PROJECT_ROOT = Path("/Volumes/Rachna-HD/Dataset/Exp1/ExecutableProjects_baseline")
CSV_PATH = Path("/Volumes/Rachna-HD/FinalBUMP_Instances.csv")
LOG_FILE = PROJECT_ROOT.parent / "dependency_update_baseline_nextlog.txt"

# === Logging helper ===
def log(message: str):
    print(message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")

# === Property resolver ===
def resolve_property(prop_expr, root, ns):
    if not prop_expr.startswith("${") or not prop_expr.endswith("}"):
        return prop_expr  # literal version
    prop_name = prop_expr[2:-1]
    props_elem = root.find("m:properties", ns)
    if props_elem is not None:
        for prop in props_elem:
            tag = prop.tag.split("}")[-1]
            if tag == prop_name:
                return prop.text.strip() if prop.text else ""
    return f"<UNRESOLVED:{prop_expr}>"

# === Dependency updater ===
def update_dependencies_from_csv(csv_path: Path):
    df = pd.read_csv(csv_path)
    log(f"\n==== Dependency Update Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ====\n")

    for _, row in df.iterrows():
        custom_id = row['custom_id']
        group_id = str(row['dependencyGroupID']).strip()
        artifact_id = str(row['dependencyArtifactID']).strip()
        new_version = str(row['newVersion']).strip()

        pom_path = PROJECT_ROOT / custom_id / f"{custom_id}_next" / "pom.xml"
        if not pom_path.exists():
            log(f" pom.xml not found for {custom_id}_next")
            continue

        try:
            tree = ET.parse(pom_path)
            root = tree.getroot()
            ns_match = re.match(r'\{(.*)\}', root.tag)
            ns = {'m': ns_match.group(1)} if ns_match else {}

            updated = False
            found_candidates = []

            for dep in root.findall(".//m:dependency", ns):
                gid = dep.find("m:groupId", ns)
                aid = dep.find("m:artifactId", ns)
                ver = dep.find("m:version", ns)

                gid_text = gid.text.strip() if gid is not None and gid.text else ""
                aid_text = aid.text.strip() if aid is not None and aid.text else ""
                ver_text = ver.text.strip() if ver is not None and ver.text else ""

                found_candidates.append(f"{gid_text}:{aid_text}:{ver_text}")

                if gid_text == group_id and aid_text == artifact_id:
                    resolved_old_version = resolve_property(ver_text, root, ns)

                    if resolved_old_version == new_version:
                        log(f"ℹ️ Already up-to-date: {group_id}:{artifact_id} = {resolved_old_version} in {custom_id}_next")
                        break

                    # Replace the version tag value
                    if ver is not None:
                        ver.text = new_version
                        updated = True
                        log(f" Updated {group_id}:{artifact_id} from {resolved_old_version} to {new_version} in {custom_id}_next")
                    else:
                        log(f" No <version> tag for {group_id}:{artifact_id} in {custom_id}_next")

                    break

            if updated:
                tree.write(pom_path, encoding="utf-8", xml_declaration=True)
            elif not updated:
                log(f" No matching dependency found for {group_id}:{artifact_id} in {custom_id}_next")
                log(f" Dependencies found: {', '.join(found_candidates)}")

        except ET.ParseError:
            log(f" Failed to parse pom.xml for {custom_id}_next")

# === MAIN ENTRY POINT ===
if __name__ == "__main__":
    update_dependencies_from_csv(CSV_PATH)
