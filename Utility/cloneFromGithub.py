import os
import subprocess
import pandas as pd
import time
from tqdm import tqdm
from pathlib import Path
import shutil

# Load GitHub token from environment
GITHUB_PAT = os.environ.get('GITHUB_PAT')

def handle_remove_error(func, path, exc_info):
    """Handle deletion errors caused by macOS metadata or stubborn directories."""
    import errno
    exc_type, exc_value, _ = exc_info

    if isinstance(exc_value, FileNotFoundError):
        print(f"Warning: File not found while deleting: {path}")
        return

    if isinstance(exc_value, OSError) and exc_value.errno == errno.ENOTEMPTY:
        print(f"Warning: Directory not empty (macOS metadata?): {path}")
        try:
            for root, dirs, files in os.walk(path, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                    except FileNotFoundError:
                        pass
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except OSError:
                        pass
            os.rmdir(path)
        except Exception as e:
            print(f"Failed to remove {path}: {e}")
        return

    raise

def clone_github_repo(github_repo_url, destination_dir, force_reclone=False, retries=3):
    os.makedirs(destination_dir, exist_ok=True)

    repo_name = github_repo_url.rstrip('/').split('/')[-1].replace('.git', '')
    local_repo_path = os.path.join(destination_dir, repo_name)

    if os.path.exists(local_repo_path):
        if force_reclone:
            print(f"Deleting existing repo: {local_repo_path}")
            shutil.rmtree(local_repo_path, onerror=handle_remove_error)
        else:
            print(f"Repo already exists, skipping: {local_repo_path}")
            return True

    attempt = 0
    while attempt < retries:
        try:
            auth_repo_url = github_repo_url.replace('https://', f'https://{GITHUB_PAT}@')
            result = subprocess.run(
                ["git", "clone", "--progress", auth_repo_url, local_repo_path],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"Cloned: {github_repo_url}")
                return True
            else:
                raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        except subprocess.CalledProcessError as e:
            print(f"Attempt {attempt + 1} failed for {github_repo_url}:\n{e.stderr}")
            attempt += 1
            time.sleep(5)

    print(f"Failed to clone after {retries} attempts: {github_repo_url}")
    return False

def extract_repo_url_from_pr(url):
    parts = url.split('/')
    return f"https://github.com/{parts[3]}/{parts[4]}.git" if len(parts) >= 5 else url

def main():
    # Paths
    external_drive = "/Volumes/Rachna-HD/ClonedRepo"
    clients_dir = os.path.join(external_drive, "ClonedRepo", "Clients")
    libraries_dir = os.path.join(external_drive, "LibraryRepositories")
    script_dir = Path(__file__).resolve().parent
    clients_csv = script_dir / "Dataset" / "unique_client_projects.csv"
    libraries_csv = script_dir / "Dataset" / "unique_dependencies.csv"

    # Load CSVs
    client_urls = pd.read_csv(clients_csv)['url'].dropna().map(extract_repo_url_from_pr).unique()
    library_urls = pd.read_csv(libraries_csv)['githubURL'].dropna().unique()

    # Clone
    # for url in tqdm(client_urls, desc="Cloning Clients"):
    #     clone_github_repo(url, clients_dir, force_reclone=False)

    for url in tqdm(library_urls, desc="Cloning Libraries"):
        clone_github_repo(url, libraries_dir, force_reclone=False)

if __name__ == "__main__":
    main()
