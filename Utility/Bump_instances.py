import json
import csv
from pathlib import Path

# Get the directory where the script is located
SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent

# Directory containing BUMP JSON instances (inside benchmark folder)
INPUT_DIR = SCRIPT_DIR.parent / "bump" / "data" / "benchmark"

print(INPUT_DIR)
OUTPUT_CSV = "bump_instances_dataset_TEST_FAILURE_lib.csv"

# Define CSV columns
CSV_FIELDS = [
    "custom_id",
    "url",
    "clientProject",
    "clientProjectOrganisation",
    "breakingCommit",
    "dependencyGroupID",
    "dependencyArtifactID",
    "previousVersion",
    "newVersion",
    "failureCategory"
]

def generate_custom_id(project, dependency_artifact, index):
    # Example ID: quickfixj_mina-core_BC01
    return f"BBC{str(index).zfill(2)}"

def main():
    data_rows = []
    json_files = list(INPUT_DIR.glob("*.json"))
    
    filtered_idx = 1  # To ensure indexing only counts filtered entries
    
    for json_file in json_files:
        with open(json_file, 'r') as f:
            content = json.load(f)
        
        # Filter only TEST_FAILURE instances
        if content.get("failureCategory") != "TEST_FAILURE":
            continue
        
        dep = content.get("updatedDependency", {})
        
        row = {
            "custom_id": generate_custom_id(content["project"], dep.get("dependencyArtifactID", "unknown"), filtered_idx),
            "url": content.get("url"),
            "clientProject": content.get("project"),
            "clientProjectOrganisation": content.get("projectOrganisation"),
            "breakingCommit": content.get("breakingCommit"),
            "dependencyGroupID": dep.get("dependencyGroupID"),
            "dependencyArtifactID": dep.get("dependencyArtifactID"),
            "previousVersion": dep.get("previousVersion"),
            "newVersion": dep.get("newVersion"),
            "failureCategory": content.get("failureCategory")
        }
        data_rows.append(row)
        filtered_idx += 1  # Increment only for valid entries
    
    # Write to CSV
    with open(OUTPUT_CSV, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(data_rows)
    
    print(f"Dataset created: {OUTPUT_CSV} with {len(data_rows)} TEST_FAILURE entries.")

if __name__ == "__main__":
    main()
