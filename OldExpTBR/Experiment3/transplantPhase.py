import os
import csv
import json
import shutil
from pathlib import Path
import subprocess

from common import clean_llm_code, classify_compilation_error, parse_package_summary, LOG_DIR

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
LLM_BASE = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp3/GPT4o")
TRANSPLANT_OUTPUT = Path("/Volumes/Rachna-HD/Experiment3Results/transplant_compile_results.json")

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

def clean_llm_code(lines):
    """Extract code inside ```java ... ``` blocks."""
    in_code = False
    code_lines = []

    for line in lines:
        if line.strip().startswith("```java"):
            in_code = True
            continue
        elif line.strip().startswith("```") and in_code:
            break
        if in_code:
            code_lines.append(line)
    return code_lines

def compile_test_in_docker(image_tag, custom_id, class_name, test_root, package_path, test_file):
    log_path = LOG_DIR / f"{custom_id}_{class_name}_compile.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_lines = []
    log_lines.append(f"[INFO] Preparing to run Docker for: {custom_id}.{class_name}")
    print(log_lines[-1])

    mount_test_dir = "/llm_tests"
    transplant_target_dir = f"{test_root}/{package_path.replace('.', '/')}/{custom_id}"

    docker_cmd = [
        "docker", "run", "--rm",
        "--platform", "linux/amd64",
        "--name", f"{custom_id}_{class_name}_compile",
        "-v", f"{test_file.parent.resolve()}:{mount_test_dir}",
        "-v", f"{os.path.expanduser('~')}/.m2:/root/.m2",
        image_tag,
        "sh", "-c",
        f"mkdir -p {transplant_target_dir} && cp {mount_test_dir}/{test_file.name} {transplant_target_dir}/{test_file.name} && cd {test_root}/../../.. && mvn -o clean test-compile"
    ]

    log_lines.append(f"[INFO] Running Docker command: {' '.join(docker_cmd)}")

    try:
        proc = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=180)
        log_lines.append(proc.stdout)
        log_lines.append(proc.stderr)
        success = proc.returncode == 0
    except Exception as e:
        log_lines.append(f"[EXCEPTION] Docker command failed: {e}")
        success = False

    log_path.write_text("\n".join(log_lines))
    error_info = classify_compilation_error("\n".join(log_lines))
    return success, error_info, str(log_path)

def main():
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            if not commit:
                continue

            result = {"compiles": [], "fails_to_compile": {}}
            llm_dir = LLM_BASE / custom_id
            if not llm_dir.exists():
                result["status"] = "llm_dir_missing"
                results[custom_id] = result
                continue

            test_root, package_path = pkg_info.get((custom_id, "pre"), (None, None))
            if not test_root or not package_path:
                result["status"] = "missing_package_info"
                results[custom_id] = result
                continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"

            temp_test_dir = Path(f"/tmp/llm_tests/{custom_id}")
            shutil.rmtree(temp_test_dir, ignore_errors=True)
            temp_test_dir.mkdir(parents=True)

            class_names = [f.stem.replace("_prompt", "") for f in llm_dir.glob("*_prompt.txt")]

            for cls in class_names:
                txt_file = llm_dir / f"{cls}_prompt.txt"
                if txt_file.exists():
                    lines = txt_file.read_text().splitlines()
                    cleaned_code = clean_llm_code(lines)
                    full_package = f"{package_path}.{custom_id}".replace("..", ".").strip(".")
                    java_code = f"package {full_package};\n\n{''.join(cleaned_code)}"
                    java_path = temp_test_dir / f"{cls}.java"
                    java_path.write_text(java_code)

                    success, err_info, log_path = compile_test_in_docker(
                        image_tag, custom_id, cls, test_root, package_path, java_path
                    )

                    if success:
                        result["compiles"].append(cls)
                    else:
                        result["fails_to_compile"][cls] = {
                            "error": err_info,
                            "log": log_path
                        }

            results[custom_id] = result

    TRANSPLANT_OUTPUT.write_text(json.dumps(results, indent=2))
    print(f"[INFO] Transplant phase complete. Results saved to {TRANSPLANT_OUTPUT}")

if __name__ == "__main__":
    main()
