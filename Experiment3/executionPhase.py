import os
import csv
import json
import shutil
from pathlib import Path
import subprocess

from common import clean_llm_code, classify_compilation_error, parse_package_summary, LOG_EXEC_DIR

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
LLM_BASE = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp3/GPT4o")
TRANSPLANT_OUTPUT = Path("/Volumes/Rachna-HD/Experiment3Results/transplant_compile_results.json")
EXECUTION_RESULTS = Path("/Volumes/Rachna-HD/Experiment3Results/execution_results_pre.json")

pkg_info = parse_package_summary(SUMMARY_PATH)
transplant_data = json.loads(TRANSPLANT_OUTPUT.read_text())
results = {}

# Build a map from custom_id to commit hash
with open(CSV_PATH) as f:
    reader = csv.DictReader(f)
    commit_map = {row["custom_id"].strip(): row["breakingCommit"].strip() for row in reader if row["breakingCommit"].strip()}

def run_tests_in_docker(image_tag, test_root, package_path, custom_id, class_names):
    mount_test_dir = f"/llm_tests"
    transplant_path = f"{test_root}/{package_path.replace('.', '/')}/{custom_id}"

    # Compose test class fully-qualified names
    fqns = [f"{package_path}.{custom_id}.{cls}" for cls in class_names]
    test_pattern = ",".join(fqns)

    shell_cmds = [
        f"mkdir -p {transplant_path}",
    ]
    for cls in class_names:
        shell_cmds.append(f"cp {mount_test_dir}/{cls}.java {transplant_path}/{cls}.java")

    shell_cmds += [
        f"cd {test_root}/../../..",
        "mvn -o test-compile || exit 1",
        f"mvn -o surefire:test -Dtest=\"{test_pattern}\""
    ]

    log_path = LOG_EXEC_DIR / f"{custom_id}_pre_exec.log"
    cmd = [
        "docker", "run", "--rm",
        "--platform", "linux/amd64",
        "--name", f"{custom_id}_pre_exec",
        "-v", f"/tmp/llm_tests/{custom_id}:{mount_test_dir}",
        "-v", f"{os.path.expanduser('~')}/.m2:/root/.m2",
        image_tag,
        "sh", "-c", " && ".join(shell_cmds)
    ]

    print(f"[INFO] Running test execution for {custom_id} with command: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    log_path.write_text(proc.stdout + "\n\n" + proc.stderr)
    return proc.returncode == 0, str(log_path), proc.stdout + proc.stderr

def extract_test_outcomes(output_log, test_classes):
    passed = []
    failed = []
    for cls in test_classes:
        if f"Tests run: 1, Failures: 0, Errors: 0" in output_log and cls in output_log:
            passed.append(cls)
        elif cls in output_log:
            failed.append(cls)
    return passed, failed

def main():
    for custom_id, data in transplant_data.items():
        if data.get("status") or not data.get("compiles"):
            continue

        test_root, package_path = pkg_info.get((custom_id, "pre"), (None, None))
        if not test_root or not package_path:
            continue

        commit = commit_map.get(custom_id)
        if not commit:
            print(f"[WARN] No commit found for {custom_id}. Skipping.")
            continue

        image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
        test_classes = data["compiles"]

        # Prepare /tmp/llm_tests/<custom_id> with only compiled test files
        temp_test_dir = Path(f"/tmp/llm_tests/{custom_id}")
        shutil.rmtree(temp_test_dir, ignore_errors=True)
        temp_test_dir.mkdir(parents=True)

        llm_dir = LLM_BASE / custom_id
        for cls in test_classes:
            txt_file = llm_dir / f"{cls}_prompt.txt"
            if txt_file.exists():
                lines = txt_file.read_text().splitlines()
                cleaned_code = clean_llm_code(lines)
                full_package = f"{package_path}.{custom_id}".replace("..", ".").strip(".")
                java_code = f"package {full_package};\n\n{''.join(cleaned_code)}"
                java_path = temp_test_dir / f"{cls}.java"
                java_path.write_text(java_code)

        success, log_path, log_content = run_tests_in_docker(
            image_tag, test_root, package_path, custom_id, test_classes
        )

        passed, failed = extract_test_outcomes(log_content, test_classes)

        results[custom_id] = {
            "passed": passed,
            "failed": failed,
            "log": log_path
        }
        print(f"[INFO] {custom_id}: {len(passed)} passed, {len(failed)} failed")

    EXECUTION_RESULTS.write_text(json.dumps(results, indent=2))
    print(f"[INFO] Execution results written to {EXECUTION_RESULTS}")

if __name__ == "__main__":
    main()
