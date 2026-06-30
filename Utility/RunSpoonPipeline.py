import pandas as pd
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PRIMARY_DRIVE

# === CONFIG ===
SCRIPT_DIR = Path(__file__).resolve().parent
csv_path = SCRIPT_DIR / "Dataset" / "FinalBUMP_Instances.csv"
jar_path = SCRIPT_DIR / "my_spoon_wrapper-1.0-shaded.jar"
clients_base_folder = PRIMARY_DRIVE / "Clients"
analysis_root = PRIMARY_DRIVE / "StaticAnalysis"

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
    github_url = row['clientGithubURL']
    
    client_repo_path = clients_base_folder / client_folder_name
    repo_url = github_url.split("/pull/")[0] + ".git"
    pull_number = github_url.split("/pull/")[-1] if "/pull/" in github_url else None

    # Clone if missing
    if not client_repo_path.exists():
        print(f"[{bump_id}] Cloning {repo_url} into {client_repo_path}")
        client_repo_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", repo_url, str(client_repo_path)], check=True)
    else:
        print(f"[{bump_id}] Repo already cloned at {client_repo_path}")

    remove_mac_metadata_files(client_repo_path)

    try:
        subprocess.run(["git", "fetch", "--all", "--tags", "--prune", "--force"],
                       cwd=client_repo_path, check=True, capture_output=True)
        subprocess.run(["git", "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"],
                       cwd=client_repo_path, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"[{bump_id}] Git fetch failed:\n{e.stderr.decode()}")
        continue

    # Try fallback PR fetch if commit not found
    if not commit_exists(client_repo_path, breaking_commit) and pull_number:
        print(f"[{bump_id}] Commit not found — fetching PR #{pull_number}")
        subprocess.run(["git", "fetch", "origin", f"pull/{pull_number}/head:pr-{pull_number}"],
                       cwd=client_repo_path, check=True)

    try:
        subprocess.run(["git", "checkout", breaking_commit],
                       cwd=client_repo_path, check=True, capture_output=True)
        print(f"[{bump_id}] Checkout successful.")
    except subprocess.CalledProcessError as e:
        print(f"[{bump_id}] Git checkout failed. Skipping SpoonPipeline.\n{e.stderr.decode()}")
        continue

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
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"[{bump_id}] Success:\n{result.stdout}")
    except subprocess.CalledProcessError as e:
        print(f"[{bump_id}] SpoonPipeline failed:\n{e.stderr}")
