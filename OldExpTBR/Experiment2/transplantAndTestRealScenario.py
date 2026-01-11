import csv
import subprocess
import re
from pathlib import Path
import os

# === Configuration ===
CSV_PATH = "/Volumes/Rachna-HD/poc/FinalBUMP_Instances_poc.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/poc/package_structure_summary.txt"
LLM_BASE = Path("/Volumes/Rachna-HD/poc/Generated_output_with_client/GPT4o")
LOG_PATH = "/Volumes/Rachna-HD/poc/python_transplant_real_exec_log.txt"
OUTPUT_DIR = Path("/Volumes/Rachna-HD/poc/TransplantImagesExp2")

# === Step 1: Parse package_structure_summary.txt ===
print("Parsing package_structure_summary.txt...")
package_info = {}  # (custom_id, type) -> (test_root, package)

if not Path(SUMMARY_PATH).exists():
    print(f"ERROR: {SUMMARY_PATH} not found.")
    exit(1)

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

# === Step 2: Process each row in the CSV ===
print("Transplanting and executing tests...")

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
            container_name = f"{custom_id.lower()}_{image_type}_llm_temp"
            final_image_name = f"{custom_id.lower()}_{image_type}_with_llm:latest"
            tar_file_path = OUTPUT_DIR / custom_id / f"{image_type}-with-llm-test.tar"
            os.makedirs(tar_file_path.parent, exist_ok=True)

            log.write(f"\n==== {custom_id} | {image_type} | IMAGE: {base_image} ====\n")

            mount_flags = []
            copy_cmds = []
            test_names = []

            for txt_file in sorted(llm_dir.glob("*_prompt.txt")):
                class_name = txt_file.stem.replace("_prompt", "")
                temp_mount_path = f"/tmp/{custom_id}_{class_name}.java"
                container_dest_path = f"{transplant_dir}/{class_name}.java"
                test_names.append(f"{new_package}.{class_name}")

                with open(txt_file) as tf:
                    code_lines = [line for line in tf if not line.strip().startswith("```")]
                    full_code = f"package {new_package};\n\n{''.join(code_lines).strip()}"

                host_temp_file = Path(f"/tmp/{custom_id}_{image_type}_{class_name}.java")
                host_temp_file.write_text(full_code)

                mount_flags.extend(["-v", f"{host_temp_file}:{temp_mount_path}"])
                copy_cmds.append((temp_mount_path, container_dest_path))

            cp_statements = " && ".join([f"cp {src} {dst}" for src, dst in copy_cmds])
            mvn_cmd = f"""
                mkdir -p {transplant_dir} && \
                {cp_statements} && \
                echo '[DEBUG] Transplanted test(s): {' '.join(test_names)}' && \
                cd {test_root}/../../.. && \
                mvn test -Dtest={','.join(test_names)}
            """

            try:
                # Step 1: Create container
                subprocess.run([
                    "docker", "create", "--platform", "linux/amd64", "--name", container_name,
                    *mount_flags, base_image,
                    "sh", "-c", mvn_cmd
                ], check=True)

                # Step 2: Start container and capture logs
                exec_result = subprocess.run(
                    ["docker", "start", "-a", container_name],
                    capture_output=True, text=True
                )
                log.write(exec_result.stdout)
                log.write(exec_result.stderr)

                if exec_result.returncode != 0:
                    log.write(f"[ERROR] Container execution failed for {container_name}. Skipping commit and save.\n")
                    continue

                log.write(f"[INFO] Test(s) executed successfully in container: {container_name}\n")

                # Step 3: Commit to new image
                subprocess.run(["docker", "commit", container_name, final_image_name], check=True)
                log.write(f"[INFO] Committed new image: {final_image_name}\n")

                # Step 4: Ensure image exists before saving
                image_check = subprocess.run(["docker", "images", "-q", final_image_name], capture_output=True, text=True)
                image_id = image_check.stdout.strip()
                if not image_id:
                    log.write(f"[ERROR] Image {final_image_name} not found after commit. Skipping save.\n")
                    continue

                # Step 5: Save image
                subprocess.run(["docker", "save", "-o", str(tar_file_path), final_image_name], check=True)
                log.write(f"[INFO] Saved image to: {tar_file_path}\n")

            except subprocess.CalledProcessError as e:
                log.write(f"[ERROR] {custom_id} | {image_type}: {e}\n")

            finally:
                subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL)
                subprocess.run(["docker", "rmi", final_image_name], stdout=subprocess.DEVNULL)

print(f"\n All done. Log saved at: {LOG_PATH}")
