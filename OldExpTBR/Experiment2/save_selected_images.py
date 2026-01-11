import csv
import os
import subprocess

# Set output directory on your external drive
EXPERIMENT_DIR = "/Volumes/Rachna-HD/poc/Experiment2"
IMAGE_REPO = "ghcr.io/chains-project/breaking-updates"
CSV_FILE = "/Volumes/Rachna-HD/poc/FinalBUMP_Instances_poc.csv"

# Make sure base output directory exists
os.makedirs(EXPERIMENT_DIR, exist_ok=True)

# Read the CSV file
with open(CSV_FILE, newline='') as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        custom_id = row['custom_id'].strip()
        commit = row['breakingCommit'].strip()
        output_dir = os.path.join(EXPERIMENT_DIR, custom_id)
        os.makedirs(output_dir, exist_ok=True)

        for tag_type in ["pre", "breaking"]:
            image_tag = f"{IMAGE_REPO}:{commit}-{tag_type}"
            tar_filename = f"{commit}-{tag_type}.tar"
            tar_path = os.path.join(output_dir, tar_filename)

            print(f" Attempting to save {image_tag} to {tar_path}...")

            try:
                subprocess.run(["docker", "save", "-o", tar_path, image_tag], check=True)
                print(f"Saved {tag_type} image for {custom_id} ({commit})")
            except subprocess.CalledProcessError:
                print(f"Failed to save {tag_type} image for {custom_id} ({commit})")
