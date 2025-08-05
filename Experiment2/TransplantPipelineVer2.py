import os
import csv
import json
import subprocess
from pathlib import Path
import shutil
from xml.etree import ElementTree as ET
# This is currently the best fixed version of the script.
# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"     
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
LLM_BASE = Path("/Volumes/Rachna-HD/Dataset/LLMOutputClient/GPT4o")
RESULT_JSON_PATH = Path("/Volumes/Rachna-HD/Experiment2Results/transplant_results.json")
SUREFIRE_OUT = Path("/Volumes/Rachna-HD/Experiment2Results/surefire_reports")
LOG_DIR = Path("/Volumes/Rachna-HD/Experiment2Results/logs")

SUREFIRE_OUT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# === Exceptions ===
class DockerRunError(Exception):
    def __init__(self, message, log_path):
        super().__init__(message)
        self.log_path = log_path

class MavenTestError(Exception):
    def __init__(self, message, log_path):
        super().__init__(message)
        self.log_path = log_path

# === Utility ===
def get_last_lines(log_path, num_lines=50):
    try:
        with open(log_path) as f:
            lines = f.readlines()
        return ''.join(lines[-num_lines:]).strip()
    except Exception:
        return ""

# === Parse Package Info ===
# def parse_package_summary(path):
#     info = {}
#     current_id = current_type = test_root = package = None
#     with open(path) as f:
#         for line in f:
#             if line.startswith("===="):
#                 parts = line.strip().split(" | ")
#                 if len(parts) >= 2:
#                     current_id = parts[0].replace("==== ", "").strip()
#                     current_type = parts[1].strip()
#             elif "Test root:" in line:
#                 test_root = line.strip().split(": ")[1]
#             elif "package " in line:
#                 package = line.strip().split("package ")[1].replace(";", "")
#                 if all([current_id, current_type, test_root, package]):
#                     info[(current_id, current_type)] = (test_root, package)
#                     current_id = current_type = test_root = package = None
#     return info

def parse_package_summary(path):
    info = {}
    current_id = current_type = None
    test_root = package = None

    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("===="):
                # Reset at start of new section
                parts = line.split(" | ")
                if len(parts) >= 2:
                    current_id = parts[0].replace("====", "").strip()
                    current_type = parts[1].strip()
                    test_root = package = None  # reset for new block

            elif line.startswith("Test root:"):
                test_root = line.split("Test root:")[1].strip()

            elif line.startswith("package:"):
                package = line.split("package:")[1].strip()

            if current_id and current_type and test_root and package:
                info[(current_id, current_type)] = (test_root, package)
                # Reset after storing to avoid duplicate entries
                current_id = current_type = test_root = package = None

    return info


# === Sanity Check ===
def sanity_check_docker_image(image_tag, custom_id, image_type):
    log_path = LOG_DIR / f"{custom_id}_{image_type}_sanity.log"
    cmd = [
        "docker", "run", "--rm",
        "--platform", "linux/amd64",
        "--name", f"sanity_{custom_id.lower()}_{image_type}",
        image_tag,
        "sh", "-c", "mvn --version || mvn validate || echo 'sanity failed'"
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        log_path.write_text(proc.stdout + "\n\n" + proc.stderr)
        return {
            "status": "passed" if proc.returncode == 0 else "failed",
            "log_path": str(log_path),
            "message": "sanity check passed" if proc.returncode == 0 else "sanity check failed"
        }
    except Exception as e:
        log_path.write_text(f"Sanity check failed: {e}")
        return {
            "status": "error",
            "log_path": str(log_path),
            "message": f"Exception during sanity: {e}"
        }

# === Docker Test Runner ===
def run_test_in_docker(image_tag, test_root, package_path, test_files_dir, test_classes, custom_id, image_type):
    container_name = f"{custom_id.lower()}_{image_type}_container"
    mount_target = "/llm_tests"
    transplant_path = f"{test_root}/{package_path.replace('.', '/')}/{custom_id}"
    log_path = LOG_DIR / f"{custom_id}_{image_type}.log"

    valid_classes = []
    shell_lines = []

    for cls in test_classes:
        source_path = test_files_dir / f"{cls}.java"
        if source_path.exists():
            shell_lines.append(f'mkdir -p "{transplant_path}"')
            shell_lines.append(f'cp "{mount_target}/{cls}.java" "{transplant_path}/{cls}.java"')
            valid_classes.append(cls)

    if not valid_classes:
        raise MavenTestError("No valid test classes found to run", log_path)

    test_names = ",".join([f"{package_path}.{custom_id}.{cls.strip()}" for cls in valid_classes])
    print(f"[DEBUG] -Dtest value: {test_names}")

    shell_lines.append(f'cd "{test_root}/../../.."')
    shell_lines.append(f'mvn test-compile || exit 1')
    shell_lines.append(f'mvn surefire:test -Dtest="{test_names}"')
    shell_lines.append(f'cp -r target/surefire-reports "/out_surefire/{custom_id}_{image_type}"')

    cmd = [
        "docker", "run", "--rm",
        "--platform", "linux/amd64",
        "--name", container_name,
        "-v", f"{test_files_dir}:{mount_target}",
        "-v", f"{SUREFIRE_OUT.absolute()}:/out_surefire",
        "-v", f"{os.path.expanduser('~')}/.m2:/root/.m2",
        image_tag,
        "sh", "-c", " && ".join(shell_lines)
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        log_path.write_text(proc.stdout + "\n\n" + proc.stderr)
    except Exception as e:
        raise DockerRunError(f"Failed to start Docker container: {e}", log_path)

    if proc.returncode != 0:
        if "No tests matching pattern" in proc.stdout:
            raise MavenTestError("No matching tests found", log_path)
        elif "BUILD FAILURE" in proc.stdout or "COMPILATION ERROR" in proc.stdout:
            raise MavenTestError("Compilation failed", log_path)
        else:
            raise MavenTestError("Test run failed", log_path)

    return SUREFIRE_OUT / f"{custom_id}_{image_type}", log_path

# === CORRECTED TEST FILE COPY FOR BREAKING PHASE ===
def prepare_breaking_tests(custom_id, passed_class_names):
    temp_dir = Path(f"/tmp/llm_tests/{custom_id}_breaking")
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True)
    for cls in passed_class_names:
        original_file = Path(f"/tmp/llm_tests/{custom_id}/{cls}.java")
        if original_file.exists():
            shutil.copy(original_file, temp_dir / f"{cls}.java")
    print(f"[DEBUG] Copied {len(passed_class_names)} test files to: {temp_dir}")
    return temp_dir

# === MAIN ===
def main():
    print("[INFO] Starting full transplant pipeline...")
    results = {}
    pkg_info = parse_package_summary(SUMMARY_PATH)

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            if not commit:
                continue

            results[custom_id] = {}
            llm_dir = LLM_BASE / custom_id
            if not llm_dir.exists():
                results[custom_id]["pre"] = {"status": "llm_dir_missing"}
                continue

            all_classes = [f.stem.replace("_prompt", "") for f in llm_dir.glob("*_prompt.txt")]
            if not all_classes:
                results[custom_id]["pre"] = {"status": "no_llm_tests"}
                continue

            temp_test_dir = Path(f"/tmp/llm_tests/{custom_id}")
            shutil.rmtree(temp_test_dir, ignore_errors=True)
            temp_test_dir.mkdir(parents=True)

            # Write Java test files for pre phase
            for cls in all_classes:
                txt_file = llm_dir / f"{cls}_prompt.txt"
                if txt_file.exists():
                    with open(txt_file) as tf:
                        lines = [line for line in tf if not line.strip().startswith("```")]
                    java_code = f"package dummy.{custom_id};\n\n{''.join(lines)}"
                    (temp_test_dir / f"{cls}.java").write_text(java_code)

            pre_image = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
            breaking_image = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"

            # Run sanity checks
            results[custom_id]["pre_sanity"] = sanity_check_docker_image(pre_image, custom_id, "pre")
            results[custom_id]["breaking_sanity"] = sanity_check_docker_image(breaking_image, custom_id, "breaking")

            # Get package path
            test_root, package_path = pkg_info.get((custom_id, "pre"), (None, None))
            if not test_root:
                results[custom_id]["pre"] = {"status": "missing_package_info"}
                continue

            try:
                sure_dir, log_path = run_test_in_docker(pre_image, test_root, package_path, temp_test_dir, all_classes, custom_id, "pre")
                passed = [f for f in all_classes if (temp_test_dir / f"{f}.java").exists()]
                results[custom_id]["pre"] = {
                    "status": "success",
                    "passed": passed,
                    "log_path": str(log_path)
                }
            except Exception as e:
                results[custom_id]["pre"] = {
                    "status": "error",
                    "error_message": str(e)
                }
                continue

            # Prepare for breaking phase
            short_passed = results[custom_id]["pre"].get("passed", [])
            temp_breaking_dir = prepare_breaking_tests(custom_id, short_passed)

            test_root, package_path = pkg_info.get((custom_id, "breaking"), (None, None))
            if not test_root:
                results[custom_id]["breaking"] = {"status": "missing_package_info"}
                continue

            try:
                sure_dir, log_path = run_test_in_docker(breaking_image, test_root, package_path, temp_breaking_dir, short_passed, custom_id, "breaking")
                results[custom_id]["breaking"] = {
                    "status": "success",
                    "log_path": str(log_path)
                }
            except Exception as e:
                results[custom_id]["breaking"] = {
                    "status": "error",
                    "error_message": str(e)
                }

    with open(RESULT_JSON_PATH, "w") as out:
        json.dump(results, out, indent=2)
    print("[INFO] Transplant pipeline completed.")

if __name__ == "__main__":
    main()
