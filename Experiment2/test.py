import csv
import subprocess
import re
from pathlib import Path
import os

# === Configuration ===
CSV_PATH = "/Volumes/Rachna-HD/poc/FinalBUMP_Instances_poc.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/poc/package_structure_summary.txt"
LLM_BASE = Path("/Volumes/Rachna-HD/poc/Generated_output_with_client/GPT4o")  # Path to LLM output folder
LOG_PATH = "/Volumes/Rachna-HD/poc/python_transplant_real_exec_log.txt"
OUTPUT_DIR = Path("/Volumes/Rachna-HD/poc/TransplantImagesExp2")  # <== For future image saving (not used now)

# === Step 1: Parse package_structure_summary.txt ===
print(" Parsing package_structure_summary.txt...")
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

# === Step 2: Process each row in the CSV and transplant ===
print(" Transplanting and executing tests...")

with open(LOG_PATH, "w") as log, open(CSV_PATH) as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        custom_id = row["custom_id"]
        breaking_commit = row["breakingCommit"]
        llm_dir = LLM_BASE / custom_id

        if not llm_dir.exists():
            log.write(f"\n[SKIP] No test folder found for {custom_id}\n")
            continue

        for image_type in ["pre", "breaking"]:
            key = (custom_id, image_type)
            if key not in package_info:
                log.write(f"\n==== {custom_id} | {image_type} | SKIPPED: No package info ====\n")
                continue

            test_root, package_path = package_info[key]
            transplant_dir = f"{test_root}/{package_path.replace('.', '/')}/{custom_id}"
            new_package = f"{package_path}.{custom_id}"

            base_image = f"ghcr.io/chains-project/breaking-updates:{breaking_commit}-{image_type}"

            log.write(f"\n==== {custom_id} | {image_type} | IMAGE: {base_image} ====\n")

            # === Prepare files for mount and transplant ===
            mount_flags = []
            copy_cmds = []  # (source, dest) inside container
            test_names = []

            for txt_file in sorted(llm_dir.glob("*_prompt.txt")):
                class_name = txt_file.stem.replace("_prompt", "")
                temp_mount_path = f"/tmp/{custom_id}_{class_name}.java"
                container_dest_path = f"{transplant_dir}/{class_name}.java"
                test_names.append(f"{new_package}.{class_name}")

                with open(txt_file) as tf:
                    code_lines = [line for line in tf if not line.strip().startswith("```")]
                    full_code = f"package {new_package};\n\n{''.join(code_lines).strip()}"

                # Write temp file
                host_temp_file = Path(f"/tmp/{custom_id}_{image_type}_{class_name}.java")
                host_temp_file.write_text(full_code)

                # Mount to /tmp and copy later to destination
                mount_flags.extend(["-v", f"{host_temp_file}:{temp_mount_path}"])
                copy_cmds.append((temp_mount_path, container_dest_path))

            # === Build mvn command to transplant and run ===
            cp_statements = " && ".join([f"cp {src} {dst}" for src, dst in copy_cmds])
            mvn_cmd = f"""
                mkdir -p {transplant_dir} && \
                {cp_statements} && \
                echo '[DEBUG] Transplanted test(s): {' '.join(test_names)}' && \
                cd {test_root}/../../.. && \
                mvn test -Dtest={','.join(test_names)}
            """

            docker_cmd = [
                "docker", "run", "--rm", "--platform", "linux/amd64",
                *mount_flags,
                base_image,
                "sh", "-c", mvn_cmd
            ]

            try:
                result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=300)
                log.write(result.stdout)
                log.write(result.stderr)
            except Exception as e:
                log.write(f"[ERROR] {custom_id} | {image_type}: {str(e)}\n")

print(f"\n Execution complete. Log saved to: {LOG_PATH}")
