import pandas as pd
import subprocess
from pathlib import Path
import sys
import time
import json

# === CONFIG ===
SCRIPT_DIR = Path(__file__).resolve().parent
csv_path = SCRIPT_DIR / "Dataset" / "FinalBUMP_Instances.csv"
jar_path = SCRIPT_DIR / "my_spoon_wrapper-1.0-extract.jar"
library_root = Path("/Volumes/Rachna-HD/ClonedRepo/LibraryRepositories")
analysis_root = Path("/Volumes/Rachna-HD/StaticAnalysis")
log_dir = Path("/Volumes/Rachna-HD/MissingReports/logs")
log_dir.mkdir(parents=True, exist_ok=True)
found_versions_log = SCRIPT_DIR / "found_library_versions.csv"

found_entries = []

def log_and_run(cmd, cwd=None, bump_id=None):
    log_path = log_dir / f"{bump_id}_library_usage.log"
    with open(log_path, 'a') as log_file:
        log_file.write(f"\n=== Running: {' '.join(cmd)} ===\n")
        process = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        log_file.write(process.stdout)
        print(process.stdout, end='')
        if process.stderr:
            log_file.write(process.stderr)
            print(process.stderr, end='', file=sys.stderr)
        return process

def commit_or_tag_exists(repo_path, version):
    result = subprocess.run(["git", "rev-parse", "--verify", version],
                            cwd=repo_path, capture_output=True)
    return result.returncode == 0

def find_commit_by_file_version(repo_path, version):
    result = subprocess.run(["git", "rev-list", "--all"], cwd=repo_path, capture_output=True, text=True)
    if result.returncode != 0:
        return None

    for commit in result.stdout.strip().splitlines():
        for filename in ["pom.xml", "build.gradle"]:
            show = subprocess.run(["git", "show", f"{commit}:{filename}"], cwd=repo_path, capture_output=True, text=True)
            if show.returncode != 0:
                continue
            content = show.stdout
            if f">{version}<" in content or f"'{version}'" in content or f'"{version}"' in content:
                return commit
    return None

# === Load CSV and run usage extraction ===
df = pd.read_csv(csv_path)

for _, row in df.iterrows():
    bump_id = row["custom_id"]
    dep_artifact = row["dependencyArtifactID"]
    dep_group = row["dependencyGroupID"]
    lib_url = row["libraryGithubURL"]
    prev_version = row["previousVersion"]
    client_project = row["clientProject"]

    if pd.isna(lib_url) or pd.isna(prev_version):
        print(f"[{bump_id}] Missing library URL or version — skipping.")
        continue

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

    # === Clone library repo if not already ===
    lib_name = lib_url.rstrip("/").split("/")[-1]
    lib_repo_path = library_root / lib_name

    if not lib_repo_path.exists():
        print(f"[{bump_id}] Cloning library: {lib_url}")
        log_and_run(["git", "clone", lib_url + ".git", str(lib_repo_path)], bump_id=bump_id)
        time.sleep(5)
    else:
        print(f"[{bump_id}] Library already cloned: {lib_repo_path}")

    # === Checkout specified version ===
    log_and_run(["git", "fetch", "--all", "--tags"], cwd=lib_repo_path, bump_id=bump_id)

    found_version = None
    if commit_or_tag_exists(lib_repo_path, prev_version):
        found_version = prev_version
    else:
        found_version = find_commit_by_file_version(lib_repo_path, prev_version)
        if found_version:
            print(f"[{bump_id}] Found matching commit by version in build file: {found_version}")
        else:
            print(f"[{bump_id}] Version {prev_version} not found in tags or files — skipping.")
            continue

    checkout_result = log_and_run(["git", "checkout", found_version], cwd=lib_repo_path, bump_id=bump_id)
    if checkout_result.returncode != 0:
        print(f"[{bump_id}] Git checkout failed — skipping analysis.")
        continue

    output_json_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "java", "-jar", str(jar_path),
        "--library-folder", str(lib_repo_path),  # ✅ Now passing the full repo root
        "--input-usage-json", str(usage_json_path),
        "--output-json", str(output_json_path)
    ]

    print(f"[{bump_id}] Running ExtractLibraryUsage...")
    result = log_and_run(cmd, bump_id=bump_id)

    if result.returncode == 0:
        found_entries.append({
            "bump_id": bump_id,
            "prev_version": prev_version,
            "selected_commit": found_version,
            "status": "success"
        })

    time.sleep(2)  # prevent GitHub rate limiting

# Write summary CSV
if found_entries:
    pd.DataFrame(found_entries).to_csv(found_versions_log, index=False)
    print(f"\n📄 Library version usage summary saved to: {found_versions_log}")
