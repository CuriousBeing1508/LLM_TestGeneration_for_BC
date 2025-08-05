import os
import csv
import json
import subprocess
from pathlib import Path
import shutil
from xml.etree import ElementTree as ET
# This is working code but does not transplant the passed test correctly (For BBC16)
# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/poc/updated_FinalBUMP_Instances_poc.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/poc/package_structure_summary.txt"
LLM_BASE = Path("/Volumes/Rachna-HD/poc/Generated_output_with_client/GPT4o")
RESULT_JSON_PATH = Path("/Volumes/Rachna-HD/poc/Experiment2Results/transplant_resultsv1.json")
SUREFIRE_OUT = Path("/Volumes/Rachna-HD/poc/Experiment2Results/surefire_reports")
LOG_DIR = Path("/Volumes/Rachna-HD/poc/Experiment2Results/logs1")

SUREFIRE_OUT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# === Custom Exceptions ===
class DockerRunError(Exception):
    def __init__(self, message, log_path):
        super().__init__(message)
        self.log_path = log_path

class MavenTestError(Exception):
    def __init__(self, message, log_path):
        super().__init__(message)
        self.log_path = log_path

# === Docker Sanity Check ===
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
        return proc.returncode == 0, str(log_path)
    except Exception as e:
        log_path.write_text(f"Sanity check failed: {e}")
        return False, str(log_path)

# === Package Summary Parser ===
def parse_package_summary(path):
    info = {}
    current_id = current_type = test_root = package = None
    with open(path) as f:
        for line in f:
            if line.startswith("===="):
                parts = line.strip().split(" | ")
                if len(parts) >= 2:
                    current_id = parts[0].replace("==== ", "").strip()
                    current_type = parts[1].strip()
            elif "Test root:" in line:
                test_root = line.strip().split(": ")[1]
            elif "package " in line:
                package = line.strip().split("package ")[1].replace(";", "")
                if all([current_id, current_type, test_root, package]):
                    info[(current_id, current_type)] = (test_root, package)
                    current_id = current_type = test_root = package = None
    return info

# === Record Error with Detail ===
def record_error(results, stage, custom_id, status, log_path, message):
    results[custom_id][stage] = {
        "status": status,
        "log_path": str(log_path),
        "error_message": message,
        "stderr_tail": Path(log_path).read_text()[-500:] if Path(log_path).exists() else ""
    }

# === Run Tests in Docker ===
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

    shell_lines.append(f'cd "{test_root}/../../.."')
    shell_lines.append(f'mvn test-compile || exit 1')
    shell_lines.append(f'mvn surefire:test -Dtest="{test_names}"')
    shell_lines.append(f'cp -r target/surefire-reports "/out_surefire/{custom_id}_{image_type}"')

    full_command = " && ".join(shell_lines)

    cmd = [
        "docker", "run", "--rm",
        "--platform", "linux/amd64",
        "--name", container_name,
        "-v", f"{test_files_dir}:{mount_target}",
        "-v", f"{SUREFIRE_OUT.absolute()}:/out_surefire",
        "-v", f"{os.path.expanduser('~')}/.m2:/root/.m2",
        image_tag,
        "sh", "-c", full_command
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        log_path.write_text(proc.stdout + "\n\n" + proc.stderr)
    except Exception as e:
        raise DockerRunError(f"Failed to start Docker container: {e}", log_path)

    if proc.returncode != 0:
        out = proc.stdout
        if "No tests matching pattern" in out:
            raise MavenTestError("No matching tests found", log_path)
        elif "COMPILATION ERROR" in out:
            raise MavenTestError("Compilation failed", log_path)
        elif "BUILD FAILURE" in out:
            raise MavenTestError("Test run failed", log_path)
        else:
            raise DockerRunError("Unknown Docker/Maven error", log_path)

    return SUREFIRE_OUT / f"{custom_id}_{image_type}", log_path

# === Surefire XML Parser ===
def parse_surefire_results(surefire_dir):
    passed, failed = [], []
    if not surefire_dir.exists():
        return passed, failed
    for xml_file in surefire_dir.glob("TEST-*.xml"):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            class_name = root.attrib.get("name")
            errors = int(root.attrib.get("errors", 0))
            failures = int(root.attrib.get("failures", 0))
            if errors == 0 and failures == 0:
                passed.append(class_name)
            else:
                failed.append(class_name)
        except ET.ParseError:
            continue
    return passed, failed

# === Main Pipeline ===
def main():
    results = {}
    pkg_info = parse_package_summary(SUMMARY_PATH)

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            if not commit:
                continue

            llm_dir = LLM_BASE / custom_id
            if not llm_dir.exists():
                continue

            all_classes = [f.stem.replace("_prompt", "") for f in llm_dir.glob("*_prompt.txt")]
            if not all_classes:
                continue

            temp_test_dir = Path(f"/tmp/llm_tests/{custom_id}")
            shutil.rmtree(temp_test_dir, ignore_errors=True)
            temp_test_dir.mkdir(parents=True)
            results[custom_id] = {}

            # === PRE STAGE ===
            pre_key = (custom_id, "pre")
            if pre_key not in pkg_info:
                results[custom_id]["pre"] = {"status": "missing_package_info"}
                continue

            test_root, package_path = pkg_info[pre_key]
            for cls in all_classes:
                txt_file = llm_dir / f"{cls}_prompt.txt"
                if not txt_file.exists():
                    continue
                with open(txt_file) as f:
                    lines = [line for line in f if not line.strip().startswith("```")]
                java_code = f"package {package_path}.{custom_id};\n\n{''.join(lines)}"
                (temp_test_dir / f"{cls}.java").write_text(java_code)

            pre_image = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"

            # sanity check
            ok, sanity_log = sanity_check_docker_image(pre_image, custom_id, "pre")
            results[custom_id]["pre_sanity"] = {
                "status": "passed" if ok else "failed",
                "log_path": sanity_log,
                "message": "sanity check passed" if ok else "sanity check failed"
            }
            if not ok:
                results[custom_id]["pre"] = {"status": "skipped_due_to_failed_sanity"}
                continue

            try:
                sure_dir, log_path = run_test_in_docker(pre_image, test_root, package_path, temp_test_dir, all_classes, custom_id, "pre")
                passed, failed = parse_surefire_results(sure_dir)
                results[custom_id]["pre"] = {
                    "status": "success",
                    "passed": passed,
                    "failed": failed,
                    "log_path": str(log_path)
                }
            except (MavenTestError, DockerRunError) as e:
                record_error(results, "pre", custom_id, "maven_failed" if isinstance(e, MavenTestError) else "docker_failed", e.log_path, str(e))
                continue

            if not results[custom_id]["pre"].get("passed"):
                results[custom_id]["breaking"] = {"status": "skipped_due_to_pre_failure"}
                continue

            # === BREAKING STAGE ===
            br_key = (custom_id, "breaking")
            if br_key not in pkg_info:
                results[custom_id]["breaking"] = {"status": "missing_package_info"}
                continue

            test_root, package_path = pkg_info[br_key]
            short_passed = [cls.split(".")[-1] for cls in results[custom_id]["pre"]["passed"]]
            breaking_image = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"

            ok, sanity_log = sanity_check_docker_image(breaking_image, custom_id, "breaking")
            results[custom_id]["breaking_sanity"] = {
                "status": "passed" if ok else "failed",
                "log_path": sanity_log,
                "message": "sanity check passed" if ok else "sanity check failed"
            }
            if not ok:
                results[custom_id]["breaking"] = {"status": "skipped_due_to_failed_sanity"}
                continue

            try:
                sure_dir, log_path = run_test_in_docker(breaking_image, test_root, package_path, temp_test_dir, short_passed, custom_id, "breaking")
                passed, failed = parse_surefire_results(sure_dir)
                results[custom_id]["breaking"] = {
                    "status": "success",
                    "passed": passed,
                    "failed": failed,
                    "log_path": str(log_path)
                }
                results[custom_id]["breaking_changes"] = list(set(results[custom_id]["pre"]["passed"]) & set(failed))
            except (MavenTestError, DockerRunError) as e:
                record_error(results, "breaking", custom_id, "maven_failed" if isinstance(e, MavenTestError) else "docker_failed", e.log_path, str(e))

    with open(RESULT_JSON_PATH, "w") as out:
        json.dump(results, out, indent=2)
    print(f"\nAll done. Results saved to: {RESULT_JSON_PATH}")

if __name__ == "__main__":
    main()
