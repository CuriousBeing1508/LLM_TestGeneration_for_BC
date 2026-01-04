import pandas as pd
import subprocess
from pathlib import Path
import sys

# === CONFIG ===
# SCRIPT_DIR = Path(__file__).resolve().parent
# csv_path = SCRIPT_DIR / "Dataset" / "detailed_missing_reportVer2.csv"
# jar_path = SCRIPT_DIR / "my_spoon_wrapper-1.0-shaded.jar"
# clients_base_folder = Path("/Volumes/Rachna-HD/Clients")
# analysis_root = Path("/Volumes/Rachna-HD/StaticAnalysis")
# log_dir = Path("/Volumes/Rachna-HD/MissingReports/logs")
# log_dir.mkdir(parents=True, exist_ok=True)

SCRIPT_DIR = Path(__file__).resolve().parent
csv_path = Path("/Volumes/RachnaPSSD/ConfigFiles/BUMP_with_NoLibraryGitHubURL.csv")
jar_path =  SCRIPT_DIR /"my_spoon_wrapper-1.0-shaded.jar"
clients_base_folder = Path("/Volumes/RachnaPSSD/Dataset/ClonedRepo/Clients")
analysis_root = Path("/Volumes/RachnaPSSD/Dataset/StaticAnalysis")
log_dir = Path("/Volumes/RachnaPSSD/Dataset/logsStaticAna")
log_dir.mkdir(parents=True, exist_ok=True)

def log_and_run(cmd, cwd=None, bump_id=None):
    """Run subprocess and write both stdout and stderr to a log file and console."""
    log_path = log_dir / f"{bump_id}_pipeline.log"
    with open(log_path, 'a') as log_file:
        log_file.write(f"\n=== Running: {' '.join(cmd)} ===\n")
        process = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Write and print stdout
        log_file.write(process.stdout)
        print(process.stdout, end='')
        # Write and print stderr
        if process.stderr:
            log_file.write(process.stderr)
            print(process.stderr, end='', file=sys.stderr)
        return process

def remove_mac_metadata_files(git_repo_path):
    pack_dir = git_repo_path / ".git" / "objects" / "pack"
    if pack_dir.exists():
        for file in pack_dir.glob("._*"):
            try:
                file.unlink()
                print(f"Deleted macOS metadata file: {file}")
            except Exception as e:
                print(f"Failed to delete {file}: {e}")

def commit_exists(repo_path, commit_hash):
    result = subprocess.run(["git", "cat-file", "-e", f"{commit_hash}^{{commit}}"],
                            cwd=repo_path, capture_output=True)
    return result.returncode == 0

# === Load CSV and process all rows ===
df = pd.read_csv(csv_path)

for _, row in df.iterrows():
    bump_id = row['custom_id']
    client_folder_name = row['clientProject']
    dep_group = row['dependencyGroupID']
    dep_artifact = row['dependencyArtifactID']
    import_prefix = row['ActualImportBase']
    breaking_commit = row['breakingCommit']
    github_url = row['url']
    
    client_repo_path = clients_base_folder / client_folder_name
    repo_url = github_url.split("/pull/")[0] + ".git"
    pull_number = github_url.split("/pull/")[-1] if "/pull/" in github_url else None

    # Clone if missing
    if not client_repo_path.exists():
        print(f"[{bump_id}] Cloning {repo_url} into {client_repo_path}")
        client_repo_path.parent.mkdir(parents=True, exist_ok=True)
        # log_and_run(["git", "clone", repo_url, str(client_repo_path)], bump_id=bump_id)
    else:
        print(f"[{bump_id}] Repo already cloned at {client_repo_path}")

    remove_mac_metadata_files(client_repo_path)

    # fetch_result = log_and_run(["git", "fetch", "--all", "--tags", "--prune", "--force"],
    #                            cwd=client_repo_path, bump_id=bump_id)
    # if fetch_result.returncode != 0:
    #     continue

    # log_and_run(["git", "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"],
                # cwd=client_repo_path, bump_id=bump_id)

    # if not commit_exists(client_repo_path, breaking_commit) and pull_number:
    #     print(f"[{bump_id}] Commit not found — fetching PR #{pull_number}")
    #     log_and_run(["git", "fetch", "origin", f"pull/{pull_number}/head:pr-{pull_number}"],
    #                 cwd=client_repo_path, bump_id=bump_id)

    # checkout_result = log_and_run(["git", "checkout", breaking_commit],
    #                               cwd=client_repo_path, bump_id=bump_id)
    # if checkout_result.returncode != 0:
    #     print(f"[{bump_id}] Git checkout failed. Skipping SpoonPipeline.")
    #     continue

    cmd = [
        "java", "-jar", str(jar_path),
        "--bump-id", bump_id,
        "--clients-folder", str(client_repo_path),
        "--analysis-root", str(analysis_root),
        "--dep-group", dep_group,
        "--dep-artifact", dep_artifact,
        "--import-prefix", import_prefix
    ]
    print(f"[{bump_id}] Running SpoonPipeline...")
    result = log_and_run(cmd, bump_id=bump_id)
