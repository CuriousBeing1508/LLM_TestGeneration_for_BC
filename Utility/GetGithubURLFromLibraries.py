import csv
import requests
from pathlib import Path
import os
import time
from dotenv import load_dotenv


# Load .env file
load_dotenv()

# === Your Libraries.io API Key ===
LIBRARY_IO_API_KEY = os.getenv("LIBRARY_IO_API_KEY")
if not LIBRARY_IO_API_KEY:
    raise EnvironmentError("Please set your LIBRARY_IO_API_KEY environment variable.")

BASE_URL = "https://libraries.io/api/maven"

# Function to get GitHub URL from Libraries.io using GroupID and ArtifactID
def fetch_github_url(group_id, artifact_id):
    api_url = f"{BASE_URL}/{group_id}:{artifact_id}?api_key={LIBRARY_IO_API_KEY}"
    print(f"Hitting the API for group_id '{group_id}' and artifact_id: '{artifact_id}'")
    
    response = requests.get(api_url)
    time.sleep(1)  # Respect API limits
    
    if response.status_code == 200:
        data = response.json()
        repo_url = data.get("repository_url")
        if repo_url and "github.com" in repo_url.lower():
            return repo_url
        else:
            print(f" No GitHub Repo Found for {group_id}:{artifact_id}")
            return "No GitHub Repo Found"
    
    elif response.status_code == 404:
        print(f"Artifact Not Found on Libraries.io for {group_id}:{artifact_id}")
        return "Artifact Not Found"
    
    else:
        print(f"🔥 API Error {response.status_code} for {group_id}:{artifact_id}")
        return f"API Error: {response.status_code}"


def main():
    # Get the directory where this script is located
    SCRIPT_DIR = Path(__file__).resolve().parent.parent

    # Define input and output CSV paths relative to script location
    input_csv = SCRIPT_DIR / "bump_instances_dataset_TEST_FAILURE.csv"
    output_csv = SCRIPT_DIR / "Library_with_GitHub.csv"
    
    # Open the input CSV to read data
    with open(input_csv, 'r') as infile:
        reader = csv.DictReader(infile)
        rows = list(reader)
    
    # Prepare to write to the new CSV with an extra column for GitHub URL
    with open(output_csv, 'w', newline='') as outfile:
        fieldnames = reader.fieldnames + ['githubURL']
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        
        # Process each row in the CSV
        for idx, row in enumerate(rows, start=1):
            group_id = row['dependencyGroupID'].strip()
            artifact_id = row['dependencyArtifactID'].strip()
            
            if not group_id or not artifact_id:
                github_url = "Missing GroupID or ArtifactID"
            else:
                github_url = fetch_github_url(group_id, artifact_id)
            
            row['githubURL'] = github_url
            writer.writerow(row)
            
            print(f"[{idx}/{len(rows)}] Processed: {group_id}:{artifact_id} -> {github_url}")
    
    print(f"\nGitHub URLs added! Output saved to: {output_csv}")

# Run the main function
if __name__ == "__main__":
    main()
