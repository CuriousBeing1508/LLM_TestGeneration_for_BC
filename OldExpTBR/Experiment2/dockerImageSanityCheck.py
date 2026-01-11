import csv
import subprocess
import json
from pathlib import Path
# This is just to check if the docker images runs as expected. Currently I have included this step in TransplantPipeline.py only.

CSV_PATH = "/Volumes/Rachna-HD/poc/updated_FinalBUMP_Instances_poc.csv"
REPORT_PATH = "/Volumes/Rachna-HD/poc/Experiment2Results/docker_image_minimal_sanity_report.json"
LOG_DIR = Path("/Volumes/Rachna-HD/poc/Experiment2Results/sanity_logs_minimal")
LOG_DIR.mkdir(parents=True, exist_ok=True)

def run_docker_image(image, custom_id, image_type):
    container_name = f"sanity_{custom_id.lower()}_{image_type}"
    log_file = LOG_DIR / f"{custom_id}_{image_type}.log"

    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", "--name", container_name, image],
            capture_output=True,
            text=True,
            timeout=300
        )
        log_file.write_text(proc.stdout + "\n\n" + proc.stderr)

        return {
            "status": "success" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "log_path": str(log_file)
        }

    except Exception as e:
        log_file.write_text(f"Exception: {e}")
        return {
            "status": "docker_error",
            "exit_code": None,
            "log_path": str(log_file)
        }

def main():
    results = {}
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            if not commit:
                continue

            results[custom_id] = {}

            for kind in ["pre", "breaking"]:
                tag = f"{commit}-{kind}"
                image = f"ghcr.io/chains-project/breaking-updates:{tag}"
                print(f"▶ Running {image} ...")
                results[custom_id][kind] = run_docker_image(image, custom_id, kind)

    with open(REPORT_PATH, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n Sanity check complete. Report saved to: {REPORT_PATH}")

if __name__ == "__main__":
    main()
