import os
import shutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PRIMARY_DRIVE

# Directory where all repos are cloned
CLONED_REPO_ROOT = PRIMARY_DRIVE

# Folders to remove
HEAVY_DIRS = [".git", "target", "node_modules", "build", ".gradle", ".idea"]
MACOS_GARBAGE = [".DS_Store"]

def remove_path(path: Path):
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
            print(f"Deleted file: {path}")
        except Exception as e:
            print(f"Could not delete file {path}: {e}")
    elif path.is_dir():
        try:
            shutil.rmtree(path)
            print(f"Deleted folder: {path}")
        except Exception as e:
            print(f"Could not delete folder {path}: {e}")

def clean_repo(repo_path: Path):
    for pattern in HEAVY_DIRS:
        target = repo_path / pattern
        if target.exists():
            remove_path(target)

    for root, dirs, files in os.walk(repo_path):
        for name in files:
            if name.startswith("._") or name in MACOS_GARBAGE:
                try:
                    os.remove(os.path.join(root, name))
                    print(f"Deleted macOS metadata: {os.path.join(root, name)}")
                except Exception as e:
                    print(f"Could not delete metadata file: {e}")

def main():
    if not CLONED_REPO_ROOT.exists():
        print(f"Directory not found: {CLONED_REPO_ROOT}")
        return

    for repo_dir in CLONED_REPO_ROOT.glob("*/*"):
        if repo_dir.is_dir():
            print(f"\nCleaning {repo_dir}")
            clean_repo(repo_dir)

if __name__ == "__main__":
    main()
