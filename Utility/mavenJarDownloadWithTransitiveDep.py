import subprocess
import pandas as pd
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PRIMARY_DRIVE
# Path for PoC
CSV_PATH = "/Users/rachnaraj/Documents/Research/Poc/Dataset/FinalBUMP_Instances.csv"
ROOT_DIR = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/downloaded_jars_1")

# Path Experiment 1
# CSV_PATH = PRIMARY_DRIVE / "FinalBUMP_Instances.csv"
# ROOT_DIR = PRIMARY_DRIVE / "Dataset/downloaded_jars"

def create_temp_pom(group_id, artifact_id, version, pom_path):
    pom_template = f"""<project xmlns="http://maven.apache.org/POM/4.0.0"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                         http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>temp</groupId>
  <artifactId>temp-artifact</artifactId>
  <version>1.0</version>
  <dependencies>
    <dependency>
      <groupId>{group_id}</groupId>
      <artifactId>{artifact_id}</artifactId>
      <version>{version}</version>
    </dependency>
  </dependencies>
</project>
"""
    pom_path.write_text(pom_template)

def run_maven_copy_deps(pom_dir, output_dir):
    cmd = [
        "mvn",
        "org.apache.maven.plugins:maven-dependency-plugin:3.6.0:copy-dependencies",
        f"-DoutputDirectory={output_dir}",
        "-DincludeScope=runtime",
        "-DexcludeOptional=false",  # include optional dependencies
        "-B",                       # batch mode (no interactive prompts)
    ]
    subprocess.run(cmd, cwd=pom_dir, check=True)


def process_bump_instance(group_id, artifact_id, version, custom_id):
    print(f"[INFO] Processing: {custom_id} ➜ {group_id}:{artifact_id}:{version}")
    output_dir = ROOT_DIR / custom_id
    if any(output_dir.glob("*.jar")):
        print(f"[SKIP] Already has JARs: {output_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    # Create a temp folder for the POM
    temp_pom_dir = output_dir / "__temp"
    temp_pom_dir.mkdir(parents=True, exist_ok=True)
    pom_path = temp_pom_dir / "pom.xml"

    create_temp_pom(group_id, artifact_id, version, pom_path)

    try:
        run_maven_copy_deps(temp_pom_dir, output_dir)
        print(f"[OK] Downloaded dependencies into {output_dir}")
    except subprocess.CalledProcessError:
        print(f"[FAIL] Maven failed for {custom_id}")
    finally:
        # Clean up temp pom directory
        for f in temp_pom_dir.glob("*"):
            f.unlink()
        temp_pom_dir.rmdir()

def main():
    df = pd.read_csv(CSV_PATH)
    df = df.dropna(subset=["custom_id", "dependencyGroupID", "dependencyArtifactID", "previousVersion"])

    for _, row in df.iterrows():
        custom_id = str(row["custom_id"]).strip()
        group_id = str(row["dependencyGroupID"]).strip()
        artifact_id = str(row["dependencyArtifactID"]).strip()
        version = str(row["previousVersion"]).strip()

        process_bump_instance(group_id, artifact_id, version, custom_id)

    print("\n All downloads completed.")

if __name__ == "__main__":
    main()
