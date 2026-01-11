import subprocess
import json
from pathlib import Path
from collections import defaultdict
import re

# Constants
PROJECT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ExecutableProjects_Client")
RESULT_DIR = PROJECT_ROOT.parent / "ResultClientExecution"
RESULT_DIR.mkdir(exist_ok=True)

INSTALL_RESULTS_PATH = RESULT_DIR / "step1_install_results_prev.json"
TEST_COMPILE_RESULTS_PATH = RESULT_DIR / "step2_test_compile_results_prev.json"
TEST_EXECUTION_RESULTS_PATH = RESULT_DIR / "step3_test_execution_results_prev.json"

def run_command(command, cwd):
    try:
        result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired:
        return 1, "Command timed out."

# STEP 1: INSTALL
def run_install_phase():
    summary = {
        "total_projects": 0,
        "install_success_count": 0,
        "install_failure_count": 0,
        "project_install_status": {}
    }

    for bump_dir in sorted(PROJECT_ROOT.iterdir()):
        if not bump_dir.is_dir():
            continue

        bump_id = bump_dir.name
        proj_path = bump_dir / f"{bump_id}_prev"
        if not proj_path.exists():
            continue

        summary["total_projects"] += 1
        print(f"📦 Running install: {bump_id}_prev")

        retcode, output = run_command(["mvn", "clean", "install", "-Dmaven.test.skip=true"], cwd=proj_path)

        status = "success" if retcode == 0 else "failure"
        if status == "success":
            summary["install_success_count"] += 1
        else:
            summary["install_failure_count"] += 1

        # Save install log
        project_log_dir = RESULT_DIR / f"{bump_id}_prev"
        project_log_dir.mkdir(parents=True, exist_ok=True)
        with open(project_log_dir / "install.log", "w", encoding="utf-8") as log_file:
            log_file.write(output)

        summary["project_install_status"][f"{bump_id}_prev"] = {
            "status": status,
            "log_snippet": output[:1000]
        }

    with open(INSTALL_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ Install summary saved to: {INSTALL_RESULTS_PATH}")
    return summary

# STEP 2: TEST COMPILE
def run_test_compile_phase(install_summary):
    summary = {}

    for project, data in install_summary["project_install_status"].items():
        if data["status"] != "success":
            continue

        proj_path = PROJECT_ROOT / project.split("_prev")[0] / project
        src_dir = proj_path / "src" / "test" / "java"
        if not proj_path.exists() or not src_dir.exists():
            continue

        print(f"🛠️ Running test-compile: {project}")
        retcode, output = run_command(["mvn", "clean", "test-compile", "-DskipTests"], cwd=proj_path)

        java_files = list(src_dir.rglob("*.java"))
        all_java_paths = {str(p.relative_to(src_dir)) for p in java_files}
        failed_relative_paths = set()

        for line in output.splitlines():
            if ".java" in line and "error" in line.lower():
                match = re.search(r"(/.*?src/test/java/)(.*?\.java):", line)
                if match:
                    rel_path = match.group(2).strip()
                    failed_relative_paths.add(rel_path)

        compiled_classes = sorted(list(all_java_paths - failed_relative_paths))
        skipped_classes = sorted(list(failed_relative_paths))

        summary[project] = {
            "test_classes_total": len(java_files),
            "compiled_test_classes": compiled_classes,
            "skipped_test_classes": skipped_classes,
            "compiled_count": len(compiled_classes),
            "skipped_count": len(skipped_classes),
        }

        # Save test-compile log
        project_log_dir = RESULT_DIR / project
        project_log_dir.mkdir(parents=True, exist_ok=True)
        with open(project_log_dir / "test_compile.log", "w", encoding="utf-8") as f:
            f.write(output)

    with open(TEST_COMPILE_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ Test-compile summary saved to: {TEST_COMPILE_RESULTS_PATH}")
    return summary

# STEP 3: TEST EXECUTION
def run_test_execution_phase(install_summary, test_compile_summary):
    summary = {}

    for project, install_data in install_summary["project_install_status"].items():
        if install_data["status"] != "success":
            continue

        compile_info = test_compile_summary.get(project, {})
        compiled_classes = compile_info.get("compiled_test_classes", [])
        skipped_count = compile_info.get("skipped_count", 0)

        if not compiled_classes:
            continue

        proj_path = PROJECT_ROOT / project.split("_prev")[0] / project
        project_log_dir = RESULT_DIR / project
        project_log_dir.mkdir(parents=True, exist_ok=True)

        total_tests_run = total_passed = total_failed = total_skipped = 0
        executed_test_classes = 0
        test_log_lines = []

        for rel_path_str in compiled_classes:
            fqcn = Path(rel_path_str).with_suffix("").as_posix().replace("/", ".")

            print(f"🧪 Running test class: {fqcn} in {project}")
            retcode, output = run_command(["mvn", f"-Dtest={fqcn}", "-DfailIfNoTests=false", "surefire:test"], cwd=proj_path)

            test_log_lines.append(f"\n===== {fqcn} =====\n{output}")

            class_tests_run = class_failures = class_errors = class_skipped = 0

            for line in output.splitlines():
                match = re.search(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)", line)
                if match:
                    class_tests_run += int(match.group(1))
                    class_failures += int(match.group(2))
                    class_errors += int(match.group(3))
                    class_skipped += int(match.group(4))

            if class_tests_run > 0:
                executed_test_classes += 1
                total_tests_run += class_tests_run
                total_failed += class_failures + class_errors
                total_skipped += class_skipped

        total_passed = total_tests_run - total_failed - total_skipped

        summary[project] = {
            "compiled_test_classes": len(compiled_classes),
            "executed_test_classes": executed_test_classes,
            "skipped_test_classes": skipped_count,
            "tests_executed": total_tests_run,
            "tests_passed": total_passed,
            "tests_failed": total_failed,
            "tests_skipped": total_skipped,
            "overall_test_execution_result": "pass" if total_failed == 0 else "fail"
        }

        # Save combined log for this project
        with open(project_log_dir / "test_run.log", "w", encoding="utf-8") as f:
            f.write("\n".join(test_log_lines))

    with open(TEST_EXECUTION_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ Test execution summary saved to: {TEST_EXECUTION_RESULTS_PATH}")
    return summary

# === MAIN ===
if __name__ == "__main__":
    install_summary = run_install_phase()
    test_compile_summary = run_test_compile_phase(install_summary)
    test_execution_summary = run_test_execution_phase(install_summary, test_compile_summary)
