import csv
import subprocess
import re
from pathlib import Path

# === Configuration ===
CSV_PATH = "/Volumes/Rachna-HD/poc/FinalBUMP_Instances_poc.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/poc/package_structure_summary.txt"
LOG_PATH = "/Volumes/Rachna-HD/poc/python_transplant_log.txt"

# === Parse summary file ===
print("Parsing package_structure_summary.txt...")

package_info = {}  # (custom_id, type) -> (test_root, package)

with open(SUMMARY_PATH) as f:
    lines = f.readlines()

current_id = current_type = test_root = package = None

for line in lines:
    if m := re.match(r"==== (\w+) \| (pre|breaking) \|", line):
        current_id, current_type = m.group(1), m.group(2)
        test_root = package = None
    elif "Test root:" in line:
        test_root = line.strip().split(": ")[1]
    elif "package " in line:
        package = line.strip().split("package ")[1].replace(";", "")
        if current_id and current_type and test_root and package:
            package_info[(current_id, current_type)] = (test_root, package)
            current_id = current_type = test_root = package = None

# === Process CSV ===
print("Processing CSV...")

with open(LOG_PATH, "w") as log, open(CSV_PATH) as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        custom_id = row["custom_id"]
        breaking_commit = row["breakingCommit"]

        for image_type in ["pre", "breaking"]:
            key = (custom_id, image_type)
            if key not in package_info:
                log.write(f"\n==== {custom_id} | {image_type} | SKIPPED: No package info found ====\n")
                continue

            test_root, package_path = package_info[key]
            image = f"ghcr.io/chains-project/breaking-updates:{breaking_commit}-{image_type}"
            transplant_dir = f"{test_root}/{package_path.replace('.', '/')}/{custom_id}"
            new_package = f"{package_path}.{custom_id}"

            log.write(f"\n==== {custom_id} | {image_type} | {image} ====\n")

            # Docker command with inline file creation and test execution
            docker_cmd = [
                "docker", "run", "--rm", "--platform", "linux/amd64",
                image,
                "sh", "-c",
                f"""
                mkdir -p {transplant_dir} && \
                echo 'package {new_package}; import org.junit.Test; public class HelloWorldTest {{ @Test public void test() {{ System.out.println("Hello from {custom_id}!"); }} }}' > {transplant_dir}/HelloWorldTest.java && \
                echo "[DEBUG] Created test file in {transplant_dir}" && \
                cat {transplant_dir}/HelloWorldTest.java && \
                echo "[DEBUG] Compiling and executing test..." && \
                cd {test_root}/../../.. && \
                mvn test -Dtest={new_package}.HelloWorldTest
                """
            ]

            try:
                result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=300)
                log.write(result.stdout)
                log.write(result.stderr)
            except Exception as e:
                log.write(f"ERROR: {str(e)}\n")

print(" Done. Log saved to:", LOG_PATH)
