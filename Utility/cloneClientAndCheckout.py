import os
import subprocess
import pandas as pd
import time
from tqdm import tqdm
from pathlib import Path
import shutil
import re

GITHUB_PAT = os.environ.get('GITHUB_PAT')
assert GITHUB_PAT, "GitHub token not found in environment variable GITHUB_PAT"

FAILED_LOG_PATH = "failed_clones.txt"
CLIENTS_DIR = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ClonedRepo/Clients")

def extract_repo_url_and_pr_number(pr_url):
    """From a PR URL, extract repo URL and PR number"""
    match = re.match(r'https://github.com/([^/]+/[^/]+)/pull/(\d+)', pr_url)
    if match:
        repo_slug, pr_number = match.groups()
        repo_url = f"https://github.com/{repo_slug}.git"
        return repo_url, pr_number
    return None, None

def clone_repo(repo_url, clone_dir, custom_id, force_reclone=False):
    auth_url = repo_url.replace("https://", f"https://{GITHUB_PAT}@")
    if clone_dir.exists():
        if force_reclone:
            shutil.rmtree(clone_dir)
        else:
            print(f"{custom_id}: Repo already exists. Skipping clone.")
            return True

    clone_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--no-single-branch", "--depth", "1000", auth_url, str(clone_dir)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"Cloned: {custom_id}")
        return True
    else:
        print(f"{custom_id}: Clone failed.\n{result.stderr}")
        return False

def fetch_pr_branch(clone_dir, pr_number, custom_id):
    """Fetch PR ref into a local branch so commit becomes reachable"""
    result = subprocess.run(
        ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"],
        cwd=clone_dir,
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print(f"{custom_id}: Fetched PR-{pr_number}")
        return True
    else:
        print(f"{custom_id}: Failed to fetch PR-{pr_number}.\n{result.stderr}")
        return False

def checkout_commit(clone_dir, commit_hash, custom_id, fail_log_file):
    print(f"{custom_id}: Attempting checkout to {commit_hash}")
    result = subprocess.run(["git", "checkout", commit_hash], cwd=clone_dir, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"{custom_id}: Checked out to {commit_hash}")
        return True
    else:
        print(f"{custom_id}: Checkout failed.\n{result.stderr.strip()}")
        fail_log_file.write(f"{custom_id} - CHECKOUT_FAILED\n")
        return False

def extract_pom_file(clone_dir, custom_id_dir, custom_id):
    for name in ['pom.xml', 'POM.XML']:
        path = clone_dir / name
        if path.exists():
            shutil.copy(path, custom_id_dir / "pom.xml")
            print(f"Extracted pom.xml for {custom_id}")
            return
    print(f"{custom_id}: No pom.xml found")

def main():
    csv_path = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/FinalBUMP_Instances.csv")
    df = pd.read_csv(csv_path)

    with open(FAILED_LOG_PATH, "w") as fail_log_file:
        for _, row in tqdm(df.iterrows(), total=len(df), desc="Processing Clients"):
            custom_id = str(row["custom_id"]).strip()
            pr_url = str(row["clientGithubURL"]).strip()
            commit_hash = str(row["breakingCommit"]).strip()

            if not pr_url or not commit_hash:
                print(f"Skipping {custom_id}: invalid URL or commit.")
                fail_log_file.write(f"{custom_id} - INVALID_URL_OR_COMMIT\n")
                continue

            repo_url, pr_number = extract_repo_url_and_pr_number(pr_url)
            if not repo_url:
                print(f"{custom_id}: Could not extract repo from URL: {pr_url}")
                fail_log_file.write(f"{custom_id} - INVALID_REPO_URL\n")
                continue

            base_dir = CLIENTS_DIR / custom_id
            clone_dir = base_dir / repo_url.rstrip('/').split('/')[-1].replace('.git', '')

            if clone_repo(repo_url, clone_dir, custom_id):
                fetch_pr_branch(clone_dir, pr_number, custom_id)
                extract_pom_file(clone_dir, base_dir, custom_id)
                checkout_commit(clone_dir, commit_hash, custom_id, fail_log_file)

if __name__ == "__main__":
    main()
