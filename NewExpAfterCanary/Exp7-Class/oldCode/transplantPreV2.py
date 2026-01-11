import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from common import parse_package_summary, classify_compilation_error, LOG_CANARY_DIR_BATCH, clean_llm_code
# This is experiment when I pass the whole class as context. It took more than 18 hrs to get all prompts.I am also generating tests for type references as well. 
# Update, now it lists which test pass, vs failed per bump instance.
# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
TRANSPLANT_OUTPUT = Path("/Volumes/Rachna-HD/Exp7BatchResults/transplant_results_final_pre.json")
CSV_SUMMARY_OUTPUT = Path("/Volumes/Rachna-HD/Exp7BatchResults/transplant_results_final_pre_summaryv2.csv")
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp7Batch/GPT4o")

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

success_count = 0
failure_count = 0
failure_categories = Counter()
csv_rows = []

# NEW: Per-bump stats
per_bump_success = Counter()
per_bump_failure = Counter()
per_bump_instances = defaultdict(dict)  # bump_id -> {custom_id: {"result": "pass"/"fail"}}

# NEW: Track instances that should be carried forward (i.e. had at least one success)
carry_forward_instances = set()

# NEW: Detailed carry forward tests per custom_id
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})


def _extract_error_fields(err_info):
    if err_info is None:
        return ("unknown", "")
    if isinstance(err_info, dict):
        category = str(err_info.get("category", "unknown"))
        reason = str(err_info.get("reason", err_info.get("message", "")))
        if not reason:
            try:
                reason = json.dumps(err_info, ensure_ascii=False)
            except Exception:
                reason = str(err_info)
        return (category, reason)
    return ("unknown", str(err_info))


def _abc_has_any_file(custom_id: str) -> bool:
    d = ABC_ROOT / custom_id
    if not d.is_dir():
        return False
    for p in d.rglob("*"):
        if p.is_file():
            return True
    return False


def _sanitize_class_name(name: str) -> str:
    cleaned = []
    for ch in name:
        cleaned.append(ch if (ch.isalnum() or ch == "_") else "_")
    if not cleaned:
        return "XEmpty"
    base = "".join(cleaned)
    if base[0].isdigit():
        base = "X" + base
    return base


def _to_java_filename(txt_name: str) -> tuple[str, str]:
    """
    BBC10U1Test_prompt.txt  -> (BBC10U1Test.java, BBC10U1Test)
    anyname.txt             -> (anyname.java, anyname)
    """
    base = txt_name
    if base.endswith("_prompt.txt"):
        base = base[:-len("_prompt.txt")]
    elif base.endswith(".txt"):
        base = base[:-len(".txt")]
    base = _sanitize_class_name(base)
    return f"{base}.java", base


def validate_image_runs(image_tag: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", image_tag],
            capture_output=True, text=True, timeout=400
        )
        return result.returncode == 0 or "BUILD SUCCESS" in result.stdout
    except Exception as e:
        print(f"[WARN] Image sanity check failed: {e}")
        return False


# -------- Extract content between ```java and ``` (no regex for extraction) --------
def _extract_llm_java_block(text: str) -> str:
    lines = text.splitlines()
    in_block = False
    buf = []
    for line in lines:
        s = line.strip()
        if not in_block:
            if s.lower() == "```java":
                in_block = True
        else:
            if s == "```":
                break
            buf.append(line)
    return "\n".join(buf).strip()


# -------- Package + class rename logic  --------
def _rewrite_package_and_class(code_text: str, package_decl: str, class_name: str) -> str:
    """
    - Replace existing 'package ...;' with 'package <package_decl>;' OR inject it at the top if missing.
    - Rename the first 'public class <X>' to 'public class <class_name>'.
    """
    code = code_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    # Replace or inject package line
    if re.search(r"^\s*package\s+[\w\.]+;\s*$", code, flags=re.MULTILINE):
        code = re.sub(
            r"^\s*package\s+[\w\.]+;\s*$",
            f"package {package_decl};",
            code,
            count=1,
            flags=re.MULTILINE
        )
    else:
        code = f"package {package_decl};\n\n{code}"

    # Ensure first public class name matches the file's base name
    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)", r"\1" + class_name, code, count=1)

    return code


def prepare_llm_tests(custom_id: str, package_decl: str) -> tuple[str, list[str]]:
    """
    - Read all *.txt under ABC_ROOT/custom_id (recursively)
    - Extract ONLY code between ```java and ```
    - Clean with clean_llm_code
    - Enforce package to the REAL project test package (package_decl)
    - Ensure first public class matches .java filename base
    - Write under /tmp/llm_exec/<custom_id>/LLMTest/<package as dirs>/File.java
    Returns (tmp_root_path, list_of_java_files)
    """
    src_dir = ABC_ROOT / custom_id
    tmp_root = Path(f"/tmp/llm_exec/{custom_id}")
    shutil.rmtree(tmp_root, ignore_errors=True)

    # map package (e.g., "org.example.tests") to directory path
    pkg_path = Path(*package_decl.split(".")) if package_decl else Path(".")
    dest_dir = tmp_root / "LLMTest" / pkg_path
    dest_dir.mkdir(parents=True, exist_ok=True)

    java_files = []
    for p in src_dir.rglob("*.txt"):
        try:
            java_filename, class_base = _to_java_filename(p.name)
            raw = p.read_text(encoding="utf-8", errors="ignore")

            java_only = _extract_llm_java_block(raw)
            if not java_only:
                print(f"[WARN] Skipping {p} (no ```java … ``` block found)")
                continue

            cleaned_lines = clean_llm_code(java_only.splitlines())
            cleaned = "\n".join(cleaned_lines).strip()
            if not cleaned:
                print(f"[WARN] Skipping {p} (empty after cleaning)")
                continue

            final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)
            (dest_dir / java_filename).write_text(final_code, encoding="utf-8")
            java_files.append(java_filename)
        except Exception as e:
            print(f"[WARN] Failed to convert {p}: {e}")
            continue

    return str(tmp_root), java_files


def run_canary_in_container(image_tag: str, custom_id: str, test_root: str, prepared_tmp_root: str, java_file: str):
    log_path = LOG_CANARY_DIR_BATCH / f"{custom_id}_{java_file}_canary_exec.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Mount our prepared LLM tests under <test_root>/LLMTest
    container_mount = f"{test_root}/LLMTest"
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{prepared_tmp_root}/LLMTest:{container_mount}:ro",
        image_tag,
        "mvn", "test", "-Dtest=" + java_file.replace(".java", "")
    ]

    log_lines = [f"[INFO] Running container for {custom_id} test {java_file} with image {image_tag}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        log_lines.append(proc.stdout)
        log_lines.append(proc.stderr)
        success = proc.returncode == 0 and "BUILD SUCCESS" in proc.stdout
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        success = False

    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    error_info = classify_compilation_error(log_text)
    return success, error_info, str(log_path), image_tag


def main():
    global success_count, failure_count, failure_categories

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            bump_id = row.get("bump_id", commit)  # NEW: assume bump_id column or fallback to commit
            if not commit:
                continue

            if not _abc_has_any_file(custom_id):
                print("[SKIP] No files under {}/{}; skipping.".format(ABC_ROOT, custom_id))
                continue

            # Get real test_root and real package from summary
            test_root, real_package = pkg_info.get((custom_id, "pre"), (None, None))
            if not test_root or not real_package:
                err_category = "missing_test_root_or_package"
                err_reason = "No test_root/package found for (custom_id, 'pre') in package summary"
                failure_count += 1
                failure_categories[err_category] += 1
                per_bump_failure[bump_id] += 1
                per_bump_instances[bump_id][custom_id] = {"result": "fail"}
                csv_rows.append({
                    "custom_id": custom_id,
                    "result": "fail",
                    "failure_reason": err_reason,
                    "error_category": err_category,
                    "log_path": "",
                })
                results[custom_id] = {"status": err_category}
                continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"

            if not validate_image_runs(image_tag):
                print(f"[SKIP] Docker image {image_tag} fails sanity check. Skipping {custom_id}.")
                failure_count += 1
                failure_categories["invalid_docker_image"] += 1
                per_bump_failure[bump_id] += 1
                per_bump_instances[bump_id][custom_id] = {"result": "fail"}
                csv_rows.append({
                    "custom_id": custom_id,
                    "result": "fail",
                    "failure_reason": "Docker image fails to start or test",
                    "error_category": "invalid_docker_image",
                    "log_path": "",
                })
                results[custom_id] = {"status": "invalid_docker_image"}
                continue

            # Use the REAL project test package (not LLMTest.<id>)
            package_decl = real_package
            prepared_tmp_root, java_files = prepare_llm_tests(custom_id, package_decl)
            print(f"[INFO] Prepared {len(java_files)} test file(s) for {custom_id}")

            if not java_files:
                failure_count += 1
                failure_categories["no_llm_tests_found"] += 1
                per_bump_failure[bump_id] += 1
                per_bump_instances[bump_id][custom_id] = {"result": "fail"}
                csv_rows.append({
                    "custom_id": custom_id,
                    "result": "fail",
                    "failure_reason": "No LLM Java tests found in abc folder",
                    "error_category": "no_llm_tests_found",
                    "log_path": "",
                })
                results[custom_id] = {"status": "no_llm_tests_found"}
                continue

            # NEW: track per test results
            per_test_status = {"passed": [], "failed": []}

            for java_file in java_files:
                success, err_info, log_path, used_image_tag = run_canary_in_container(
                    image_tag, custom_id, test_root, prepared_tmp_root, java_file
                )

                if success:
                    print(f"[INFO] Canary test passed for {custom_id}/{java_file}")
                    results.setdefault(custom_id, {"tests": {}})["tests"][java_file] = {"canary_status": "success"}
                    success_count += 1
                    per_bump_success[bump_id] += 1
                    per_test_status["passed"].append(java_file)
                    csv_rows.append({
                        "custom_id": custom_id,
                        "result": "pass",
                        "failure_reason": "",
                        "error_category": "",
                        "log_path": log_path,
                    })
                else:
                    print(f"[ERROR] Canary test failed for {custom_id}/{java_file}")
                    category, reason = _extract_error_fields(err_info)
                    results.setdefault(custom_id, {"tests": {}})["tests"][java_file] = {
                        "canary_status": {
                            "error": err_info,
                            "log": log_path
                        }
                    }
                    failure_count += 1
                    failure_categories[category] += 1
                    per_bump_failure[bump_id] += 1
                    per_test_status["failed"].append(java_file)
                    csv_rows.append({
                        "custom_id": custom_id,
                        "result": "fail",
                        "failure_reason": reason,
                        "error_category": category,
                        "log_path": log_path,
                    })

            # Save carry-forward only if at least one test passed
            if per_test_status["passed"]:
                carry_forward_instances.add(custom_id)
                carry_forward_tests[custom_id]["passed"].extend(per_test_status["passed"])
                carry_forward_tests[custom_id]["failed"].extend(per_test_status["failed"])

    TRANSPLANT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    TRANSPLANT_OUTPUT.write_text(json.dumps({
        "results": results,
        "per_bump_instances": per_bump_instances,
        "carry_forward_instances": list(carry_forward_instances),  # NEW: store passing ones
        "carry_forward_tests": carry_forward_tests                # NEW: store per-test detail
    }, indent=2), encoding="utf-8")
    print(f"[INFO] Canary execution complete. Results saved to {TRANSPLANT_OUTPUT}")

    CSV_SUMMARY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["custom_id", "result", "failure_reason", "error_category", "log_path"]
    with open(CSV_SUMMARY_OUTPUT, "w", newline="", encoding="utf-8") as fcsv:
        writer = csv.DictWriter(fcsv, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"[SUMMARY] Successes: {success_count}")
    print(f"[SUMMARY]  Failures: {failure_count}")
    print(f"[SUMMARY]  Distinct failure categories: {len([c for c in failure_categories if c])}")
    if failure_categories:
        print("[SUMMARY] Failure category breakdown:")
        for cat, cnt in failure_categories.most_common():
            print(f"  - {cat}: {cnt}")

    # NEW: Print per-bump stats
    print(f"[SUMMARY] Per-bump results:")
    for bump, inst in per_bump_instances.items():
        print(f"  - Bump {bump}: {per_bump_success[bump]} passed, {per_bump_failure[bump]} failed (total {len(inst)})")

    print(f"[INFO]  CSV summary saved to {CSV_SUMMARY_OUTPUT}")


if __name__ == "__main__":
    main()
