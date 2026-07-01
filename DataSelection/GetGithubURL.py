import csv
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
import time

# Function to get GitHub URL from Maven Central using GroupID and ArtifactID
def fetch_github_url(group_id, artifact_id):
    search_url = "https://search.maven.org/solrsearch/select"
    params = {"q": f'g:"{group_id}" AND a:"{artifact_id}"', "rows": 1, "wt": "json"}
    
    response = requests.get(search_url, params=params)
    
    # Add delay after each API call to avoid rate limiting
    time.sleep(1)   # 1 second delay
    
    if response.status_code != 200:
        return f"Search API Error: {response.status_code}"
    
    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError:
        return "Invalid JSON Response from Maven Central"
    
    results = data.get('response', {}).get('docs', [])
    if not results:
        return "Artifact Not Found"
    
    latest_version = results[0]['latestVersion']
    group_path = group_id.replace('.', '/')
    pom_url = f"https://repo1.maven.org/maven2/{group_path}/{artifact_id}/{latest_version}/{artifact_id}-{latest_version}.pom"
    
    pom_response = requests.get(pom_url)
    
    # Another delay after fetching POM
    time.sleep(1)
    
    if pom_response.status_code != 200:
        return "POM Not Found"
    
    pom_xml = ET.fromstring(pom_response.content)
    namespace = {'m': 'http://maven.apache.org/POM/4.0.0'}
    
    scm_url_tag = pom_xml.find('.//m:scm/m:url', namespace)
    
    if scm_url_tag is not None and "github.com" in scm_url_tag.text.lower():
        return scm_url_tag.text
    
    return "No GitHub Repo Found"

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
        for row in rows:
            group_id = row['dependencyGroupID']
            artifact_id = row['dependencyArtifactID']
            
            # If group or artifact is missing, skip lookup
            if not group_id or not artifact_id:
                github_url = "Missing GroupID or ArtifactID"
            else:
                github_url = fetch_github_url(group_id, artifact_id)
            
            # Add the GitHub URL to the row
            row['githubURL'] = github_url
            
            # Write the updated row to the new CSV
            writer.writerow(row)
    
    print(f"GitHub URLs added! Output saved to: {output_csv}")

# Run the main function
if __name__ == "__main__":
    main()
