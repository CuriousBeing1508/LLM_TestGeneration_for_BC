import os
import requests
import time
import random
import pandas as pd
from bs4 import BeautifulSoup
from pathlib import Path

# === Configuration ===
CSV_PATH = "/Users/rachnaraj/Documents/Research/Poc/Dataset/FinalBUMP_Instances.csv"
BASE_REPO_URL = "https://repo1.maven.org/maven2"
DOWNLOAD_DIR = "/Users/rachnaraj/Documents/Research/Poc/Dataset/downloaded_jars"

def polite_delay():
    """Random delay between requests to avoid rate-limiting"""
    time.sleep(random.uniform(2, 4))

def download_jar(group_id, artifact_id, version, custom_id):
    """Download the main JAR for the given coordinates into the custom_id folder"""
    group_path = group_id.replace('.', '/')
    jar_name = f"{artifact_id}-{version}.jar"
    jar_url = f"{BASE_REPO_URL}/{group_path}/{artifact_id}/{version}/{jar_name}"

    output_dir = Path(DOWNLOAD_DIR) / custom_id
    output_dir.mkdir(parents=True, exist_ok=True)
    jar_path = output_dir / jar_name

    if jar_path.exists():
        print(f"[SKIP] Already downloaded: {jar_path}")
        return

    print(f"[DOWNLOAD] {jar_url}")
    response = requests.get(jar_url, stream=True)
    polite_delay()

    if response.status_code == 200:
        with open(jar_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"[SAVED] {jar_path}")
    else:
        print(f"[FAILED] Could not download: {jar_url} [Status: {response.status_code}]")

def main():
    df = pd.read_csv(CSV_PATH)

    # Drop rows with missing critical data
    df = df.dropna(subset=["custom_id", "dependencyGroupID", "dependencyArtifactID", "previousVersion"])

    # Use each row individually to map to its custom_id
    print(f"[INFO] Found {len(df)} rows to process.")
    
    for _, row in df.iterrows():
        custom_id = str(row["custom_id"]).strip()
        group_id = str(row["dependencyGroupID"]).strip()
        artifact_id = str(row["dependencyArtifactID"]).strip()
        version = str(row["previousVersion"]).strip()

        download_jar(group_id, artifact_id, version, custom_id)

    print("[DONE] All downloads attempted.")

if __name__ == "__main__":
    main()
