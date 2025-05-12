import pandas as pd
import subprocess
from pathlib import Path
import sys
import time

# === CONFIG ===


SCRIPT_DIR = Path(__file__).resolve().parent
csv_path = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/FinalBUMP_Instances.csv")
jar_path = SCRIPT_DIR / "Extract-library-usage-from-bytecode.jar"
library_root = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/downloaded_jars")
analysis_root = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/StaticAnalysisClient")
log_dir = Path("/Users/rachnaraj/Documents/Research/Poc/logs")
log_dir.mkdir(parents=True, exist_ok=True)


def log_and_run(cmd, bump_id=None):
    log_path = log_dir / f"{bump_id}_library_usage.log"
    with open(log_path, 'a') as log_file:
        log_file.write(f"\n=== Running: {' '.join(map(str, cmd))} ===\n")
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        log_file.write(process.stdout)
        print(process.stdout, end='')
        if process.stderr:
            log_file.write(process.stderr)
            print(process.stderr, end='', file=sys.stderr)
        return process

# === Load CSV and run usage extraction ===
df = pd.read_csv(csv_path)

for _, row in df.iterrows():
    bump_id = row["custom_id"]
    dep_artifact = row["dependencyArtifactID"]

    usage_folder = analysis_root / bump_id / "UsageReport"
    if not usage_folder.exists():
        print(f"[{bump_id}] UsageReport folder missing: {usage_folder}")
        continue

    usage_json_files = list(usage_folder.glob(f"{bump_id}_*.json"))
    if not usage_json_files:
        print(f"[{bump_id}] No UsageReport JSON found in folder: {usage_folder}")
        continue

    usage_json_path = usage_json_files[0]
    output_json_path = analysis_root / bump_id / "LibraryUsageReport" / f"{dep_artifact}_library_usage.json"
    library_folder = library_root / bump_id

    if not library_folder.exists():
        print(f"[{bump_id}] Library JAR folder not found: {library_folder}")
        continue

    output_json_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "java", "-jar", str(jar_path),
        "--input-usage-json", str(usage_json_path),
        "--output-json", str(output_json_path),
        "--library-folder", str(library_folder)
    ]

    print(f"[{bump_id}] Running ExtractLibraryUsage...")
    result = log_and_run(cmd, bump_id=bump_id)

  
