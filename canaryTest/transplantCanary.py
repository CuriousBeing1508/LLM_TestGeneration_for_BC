import os
import csv
import json
import shutil
import subprocess
from pathlib import Path
from common import parse_package_summary, classify_compilation_error, LOG_CANARY_DIR

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
TRANSPLANT_OUTPUT = Path("/Volumes/Rachna-HD/CanaryResults/transplant_results_final_exec.json")

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

def run_canary_in_container(image_tag, custom_id, test_root, java_file):
    log_path = LOG_CANARY_DIR / f"{custom_id}_canary_exec.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    container_name = f"canary_{custom_id.lower()}"

    # Paths inside container
    transplant_target_dir = f"{test_root}/LLMTest/{custom_id}"

    log_lines = [f"[INFO] Spinning up container {container_name} for {custom_id}"]

    try:
        # Start container detached
        subprocess.run([
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "--name", container_name, "-dit", image_tag, "sh"
        ], check=True)

        # Copy file into container
        subprocess.run([
            "docker", "exec", container_name, "mkdir", "-p", transplant_target_dir
        ], check=True)

        subprocess.run([
            "docker", "cp", str(java_file), f"{container_name}:{transplant_target_dir}/"
        ], check=True)

        # Run mvn test
        mvn_cmd = f"cd {test_root}/../../.. && mvn -o test"
        exec_result = subprocess.run(
            ["docker", "exec", container_name, "sh", "-c", mvn_cmd],
            capture_output=True, text=True, timeout=300
        )

        log_lines.append(exec_result.stdout)
        log_lines.append(exec_result.stderr)

        success = exec_result.returncode == 0 and "BUILD SUCCESS" in exec_result.stdout

    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        success = False

    finally:
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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

            result = {}
            test_root, _ = pkg_info.get((custom_id, "pre"), (None, None))
            if not test_root:
                result["status"] = "missing_test_root"
                results[custom_id] = result
                continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"

            temp_dir = Path(f"/tmp/llm_exec/{custom_id}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True)

            package_decl = f"LLMTest.{custom_id}"
            class_name = "HelloWorldTest"

            java_code = f"""\
package {package_decl};

import org.junit.Test;
import static org.junit.Assert.*;

public class {class_name} {{
    @Test
    public void testHello() {{
        String msg = "Hello World";
        assertEquals("Hello World", msg);
    }}
}}"""

            java_file = temp_dir / f"{class_name}.java"
            java_file.write_text(java_code)

            success, err_info, log_path = run_canary_in_container(
                image_tag, custom_id, test_root, java_file
            )

            if success:
                print(f"[INFO] ✅ Canary test passed for {custom_id}")
                result["canary_status"] = "success"
            else:
                print(f"[ERROR] ❌ Canary test failed for {custom_id}")
                result["canary_status"] = {
                    "error": err_info,
                    "log": log_path
                }

            results[custom_id] = result

    TRANSPLANT_OUTPUT.write_text(json.dumps(results, indent=2))
    print(f"[INFO] ✅ Canary execution complete. Results saved to {TRANSPLANT_OUTPUT}")

if __name__ == "__main__":
    main()
