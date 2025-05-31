import xml.etree.ElementTree as ET
import pandas as pd
from pathlib import Path
from datetime import datetime

# === CONFIGURATION ===
PROJECT_ROOT = Path("/Volumes/Rachna-HD/Dataset/Exp1/ExecutableProjects_Client")  # Update if needed
CSV_PATH = Path("/Volumes/Rachna-HD/FinalBUMP_Instances.csv")  #  Update this to the actual CSV path
LOG_FILE = Path(PROJECT_ROOT.parent/ "dependency_update_client_nextlog.txt")
# === NAMESPACE USED BY MAVEN POM FILES ===
NAMESPACE = {'m': 'http://maven.apache.org/POM/4.0.0'}

# === Logging helper ===
def log(message: str):
    print(message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")



def update_dependencies_from_csv(csv_path: Path):
    df = pd.read_csv(csv_path)
    log(f"\n==== Dependency Update Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ====\n")

    for _, row in df.iterrows():
        custom_id = row['custom_id']
        group_id = str(row['dependencyGroupID']).strip()
        artifact_id = str(row['dependencyArtifactID']).strip()
        prev_version = str(row['previousVersion']).strip()
        new_version = str(row['newVersion']).strip()

        pom_path = PROJECT_ROOT / custom_id / f"{custom_id}_next" / "pom.xml"
        if not pom_path.exists():
            log(f"❌ pom.xml not found for {custom_id}_next")
            continue

        try:
            tree = ET.parse(pom_path)
            root = tree.getroot()

            updated = False
            found_candidates = []

            for dep in root.findall(".//m:dependency", NAMESPACE):
                gid = dep.find("m:groupId", NAMESPACE)
                aid = dep.find("m:artifactId", NAMESPACE)
                ver = dep.find("m:version", NAMESPACE)

                gid_text = gid.text.strip() if gid is not None and gid.text else ""
                aid_text = aid.text.strip() if aid is not None and aid.text else ""
                ver_text = ver.text.strip() if ver is not None and ver.text else ""

                found_candidates.append(f"{gid_text}:{aid_text}:{ver_text}")

                if gid_text == group_id and aid_text == artifact_id:
                    old_version = ver_text
                    ver.text = new_version
                    updated = True
                    log(f"✅ Updated {group_id}:{artifact_id} from {old_version} to {new_version} in {custom_id}_next")
                    break

            if updated:
                tree.write(pom_path, encoding="utf-8", xml_declaration=True)
            else:
                log(f"❌ No matching dependency found for {group_id}:{artifact_id} in {custom_id}_next")
                log(f"🔍 Dependencies found: {', '.join(found_candidates)}")

        except ET.ParseError:
            log(f"❌ Failed to parse pom.xml for {custom_id}_next")



# === MAIN ENTRY POINT ===
if __name__ == "__main__":
    update_dependencies_from_csv(CSV_PATH)
