import csv
import subprocess
import re
from pathlib import Path
import os

# === Configuration ===
CSV_PATH = "/Volumes/Rachna-HD/poc/FinalBUMP_Instances_poc.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/poc/package_structure_summary.txt"
LLM_BASE = Path("/Volumes/Rachna-HD/poc/Generated_output_with_client/GPT4o")
LOG_PATH = "/Volumes/Rachna-HD/poc/python_transplant_debug_log.txt"
OUTPUT_DIR = Path("/Volumes/Rachna-HD/poc/TransplantImagesExp2_img_creation")

print("Step 1: Parsing package structure summary...")
package_info = {}
with open(SUMMARY_PATH) as f:
    current_id = current_type = test_root = package = None
    for line in f:
        if m := re.match(r"==== (\w+) \| (pre|breaking) \|", line):
            current_id, current_type = m.group(1), m.group(2)
            test_root = package = None
        elif "Test root:" in line:
            test_root = line.strip().split(": ")[1]
        elif "package " in line:
            package = line.strip().split("package ")[1].replace(";", "")
        if current_id and current_type and test_root:
            # Always store even if package is missing (None)
            package_info[(current_id, current_type)] = (test_root, package or "")

print("Step 2: Executing transplant and image creation...")

with open(LOG_PATH, "w") as log, open(CSV_PATH) as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        custom_id = row["custom_id"]
        breaking_commit = row["breakingCommit"]
        llm_dir = LLM_BASE / custom_id

        if not llm_dir.exists():
            log.write(f"[SKIP] No LLM dir for {custom_id}\n")
            continue

        for image_type in ["pre", "breaking"]:
            key = (custom_id, image_type)
            if key not in package_info:
                log.write(f"[SKIP] No package info for {custom_id} | {image_type}\n")
                continue

            test_root, package_path = package_info[key]

            if package_path:
                transplant_dir = f"{test_root}/{package_path.replace('.', '/')}/{custom_id}"
                new_package = f"{package_path}.{custom_id}"
            else:
                transplant_dir = f"{test_root}/{custom_id}"
                new_package = custom_id

            base_image = f"ghcr.io/chains-project/breaking-updates:{breaking_commit}-{image_type}"
            container_name = f"{custom_id.lower()}_{image_type}_llm_temp"
            final_image_name = f"{custom_id.lower()}_{image_type}_with_llm"
            tar_path = OUTPUT_DIR / custom_id / f"{image_type}-with-llm-test.tar"
            os.makedirs(tar_path.parent, exist_ok=True)

            mount_flags = []
            copy_cmds = []
            test_names = []

            for txt_file in sorted(llm_dir.glob("*_prompt.txt")):
                class_name = txt_file.stem.replace("_prompt", "")
                temp_host = Path(f"/tmp/{custom_id}_{image_type}_{class_name}.java")
                temp_container = f"/tmp/{custom_id}_{class_name}.java"
                dest_path = f"{transplant_dir}/{class_name}.java"
                test_names.append(f"{new_package}.{class_name}")

                with open(txt_file) as tf:
                    code_lines = [line for line in tf if not line.strip().startswith("```")]

                if new_package.isidentifier() or '.' in new_package:
                    java_code = f"package {new_package};\n\n{''.join(code_lines).strip()}"
                else:
                    java_code = ''.join(code_lines).strip()

                temp_host.write_text(java_code)
                mount_flags.extend(["-v", f"{temp_host}:{temp_container}"])
                copy_cmds.append((temp_container, dest_path))

            cp_cmd = " && ".join([f"cp {src} {dst}" for src, dst in copy_cmds])
            transplant_cmd = f"set -e && mkdir -p {transplant_dir} && {cp_cmd}"
            transplant_cmd = transplant_cmd.strip().replace("\n", " ")

            log.write(f"[DEBUG] {custom_id} | {image_type} transplant_cmd:\n{transplant_cmd}\n")

            try:
                subprocess.run([
                    "docker", "create", "--platform", "linux/amd64", "--name", container_name,
                    *mount_flags, base_image, "sh", "-c", transplant_cmd
                ], check=True)

                subprocess.run(["docker", "start", "-a", container_name], check=True)
                log.write(f"[INFO] Transplanted files into: {container_name}\n")

                commit_proc = subprocess.run(
                    ["docker", "commit", container_name, final_image_name],
                    capture_output=True, text=True
                )
                if commit_proc.returncode != 0:
                    log.write(f"[ERROR] docker commit failed: {commit_proc.stderr}\n")
                    continue

                log.write(f"[INFO] Committed image: {final_image_name}\n")

                test_cmd = f"cd {test_root}/../../.. && mvn test -Dtest={','.join(test_names)}"
                test_proc = subprocess.run([
                    "docker", "run", "--rm", "--platform", "linux/amd64",
                    final_image_name, "sh", "-c", test_cmd
                ], capture_output=True, text=True)
                log.write(test_proc.stdout)
                log.write(test_proc.stderr)

                save_proc = subprocess.run([
                    "docker", "save", "-o", str(tar_path), final_image_name
                ], capture_output=True, text=True)

                if save_proc.returncode == 0:
                    log.write(f"[INFO] Saved image to: {tar_path}\n")
                else:
                    log.write(f"[ERROR] Failed to save image: {save_proc.stderr}\n")

            except subprocess.CalledProcessError as e:
                log.write(f"[ERROR] {custom_id} | {image_type}: {e}\n")

            finally:
                subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL)
                subprocess.run(["docker", "rmi", final_image_name], stdout=subprocess.DEVNULL)

print(f"\n All done. Log saved at: {LOG_PATH}")
