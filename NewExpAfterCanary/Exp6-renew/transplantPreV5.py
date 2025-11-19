import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from common import parse_package_summary, classify_compilation_error, LOG_DIR_BATCH, clean_llm_code

# prepare_staging_tests → puts all candidates into /staging.
# run_test_in_isolation → mounts only one file at a time into docker, runs container default CMD.
# good_tests/ dir → accumulates only the tests that passed (preserving package path).
# carry_forward_tests JSON → clearly lists passed vs failed per custom_id.
# Next stage (breaking) just mounts good_tests/ for each custom_id.
# This script also counts the number of tests in each file for each custom id. that is the only extension from v3.

# === CONFIG ===
CSV_PATH = "/Volumes/RachnaPSSD/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = "/Volumes/RachnaPSSD/package_structure_summary.txt"
TRANSPLANT_OUTPUT = Path("/Volumes/RachnaPSSD/Exp6BatchResults/pre/transplant_results_final_pre.json")
CSV_SUMMARY_OUTPUT = Path("/Volumes/RachnaPSSD/Exp6BatchResults/pre/transplant_results_final_pre_summary.csv")
ABC_ROOT = Path("/Volumes/RachnaPSSD/GeneratedOutputClientsExp6/GPT4o")
MODEL_NAME = ABC_ROOT.name  # e.g., "GPT4o"

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

success_count = 0
failure_count = 0
failure_categories = Counter()
csv_rows = []

# Per-bump stats
per_bump_success = Counter()
per_bump_failure = Counter()
per_bump_instances = defaultdict(dict)  # bump_id -> {custom_id: {"result": "pass"/"fail"}}

# Carry forward info
carry_forward_instances = set()
carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []})

# Test count tracker
test_counts = defaultdict(lambda: {"generated": 0, "executed": 0})


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


def _rewrite_package_and_class(code_text: str, package_decl: str, class_name: str) -> str:
    """
    - Replace existing 'package ...;' with 'package <package_decl>;' OR inject it at the top if missing.
    - Rename the first 'public class <X>' to 'public class <class_name>'.
    """
    code = code_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

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

    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)", r"\1" + class_name, code, count=1)

    return code


def _count_test_methods(java_path: Path) -> int:
    """Count @Test annotations in a given Java file."""
    if not java_path.exists():
        return 0
    text = java_path.read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r'@Test\b', text))


def prepare_staging_tests(custom_id: str, package_decl: str) -> tuple[str, list[str]]:
    """
    Prepare all candidate tests into a staging dir.
    Returns (staging_root, [java_files]).
    """
    src_dir = ABC_ROOT / custom_id
    staging_root = Path(f"/tmp/llm_exec/{custom_id}/staging")
    shutil.rmtree(staging_root, ignore_errors=True)

    pkg_path = Path(*package_decl.split(".")) if package_decl else Path(".")
    dest_dir = staging_root / "LLMTest" / pkg_path
    dest_dir.mkdir(parents=True, exist_ok=True)

    java_files = []
    for p in src_dir.rglob("*.txt"):
        try:
            java_filename, class_base = _to_java_filename(p.name)
            raw = p.read_text(encoding="utf-8", errors="ignore")
            java_only = _extract_llm_java_block(raw)
            if not java_only:
                continue
            cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
            if not cleaned:
                continue
            final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)
            out_file = dest_dir / java_filename
            out_file.write_text(final_code, encoding="utf-8")
            java_files.append(java_filename)

            # Count generated tests
            test_counts[custom_id]["generated"] += _count_test_methods(out_file)

        except Exception as e:
            print(f"[WARN] Failed to convert {p}: {e}")
            continue

    return str(staging_root), java_files


def run_test_in_isolation(image_tag: str, custom_id: str, test_root: str, staging_root: str, java_file: str):
    """
    Run a single test in isolation by mounting only that test file.
    """
    scratch_dir = Path(f"/tmp/llm_exec/{custom_id}/scratch/{java_file}")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_pkg_dir = scratch_dir / "LLMTest"
    scratch_pkg_dir.mkdir(parents=True, exist_ok=True)

    # copy only this java file from staging
    found = None
    for root, _, files in os.walk(staging_root):
        if java_file in files:
            src_path = Path(root) / java_file
            rel_path = Path(root).relative_to(Path(staging_root) / "LLMTest")
            dest_path = scratch_pkg_dir / rel_path
            dest_path.mkdir(parents=True, exist_ok=True)
            shutil.copy(src_path, dest_path / java_file)
            found = src_path
            break
    if not found:
        print(f"[WARN] Could not find staged file {java_file} for {custom_id}")
        return False, None, ""

    log_path = LOG_DIR_BATCH / f"{custom_id}_{java_file}_Exp6.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    container_mount = f"{test_root}/LLMTest"
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{scratch_pkg_dir}:{container_mount}:ro",
        image_tag
    ]

    log_lines = [f"[INFO] Running isolated test {java_file} for {custom_id} using {image_tag}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        log_lines.append(proc.stdout)
        log_lines.append(proc.stderr)
        success = proc.returncode == 0 and "BUILD SUCCESS" in proc.stdout
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        success = False

    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    err_info = classify_compilation_error(log_text)
    return success, err_info, str(log_path)


def main():
    global success_count, failure_count, results, carry_forward_instances, carry_forward_tests, test_counts

    # === BATCH CONFIG ===
    START_ID = 65
    END_ID = 190

    # Load existing JSON if it exists
    if TRANSPLANT_OUTPUT.exists():
        try:
            existing = json.loads(TRANSPLANT_OUTPUT.read_text(encoding="utf-8"))
            results = existing.get("results", {})
            carry_forward_instances = set(existing.get("carry_forward_instances", []))
            carry_forward_tests = defaultdict(lambda: {"passed": [], "failed": []},
                                              existing.get("carry_forward_tests", {}))
            test_counts = defaultdict(lambda: {"generated": 0, "executed": 0},
                                      existing.get("test_counts", {}))
            print(f"[INFO] Loaded existing results with {len(results)} custom_ids")
        except Exception as e:
            print(f"[WARN] Failed to load existing JSON: {e}")

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()

            # Extract numeric suffix from IDs like "BBC11"
            match = re.search(r"(\d+)$", custom_id)
            if not match:
                continue
            cid_num = int(match.group(1))
            if cid_num < START_ID or cid_num > END_ID:
                continue

            commit = row["breakingCommit"].strip()
            bump_id = row.get("bump_id", commit)
            if not commit:
                continue

            if not _abc_has_any_file(custom_id):
                print(f"[SKIP] No files under {ABC_ROOT}/{custom_id}; skipping.")
                continue

            test_root, real_package = pkg_info.get((custom_id, "pre"), (None, None))
            if not test_root or not real_package:
                print(f"[SKIP] No test_root/package for {custom_id}")
                continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
            staging_root, java_files = prepare_staging_tests(custom_id, real_package)
            print(f"[INFO] Prepared {len(java_files)} candidate test file(s) for {custom_id}")
            if not java_files:
                continue

            per_test_status = {"passed": [], "failed": []}
            good_tests_dir = Path(f"/tmp/llm_exec/{custom_id}/{MODEL_NAME}/LLMTest")
            good_tests_dir.mkdir(parents=True, exist_ok=True)

            for java_file in java_files:
                success, err_info, log_path = run_test_in_isolation(
                    image_tag, custom_id, test_root, staging_root, java_file
                )
                staged_file = None
                for root, _, files in os.walk(staging_root):
                    if java_file in files:
                        staged_file = Path(root) / java_file
                        break

                if success:
                    print(f"[INFO] Canary test PASSED for {custom_id}/{java_file}")
                    success_count += 1
                    per_test_status["passed"].append(java_file)
                    carry_forward_tests[custom_id]["passed"].append(java_file)

                    if staged_file:
                        test_counts[custom_id]["executed"] += _count_test_methods(staged_file)
                        rel_path = staged_file.relative_to(Path(staging_root) / "LLMTest")
                        dest_path = good_tests_dir / rel_path
                        dest_path.mkdir(parents=True, exist_ok=True)
                        shutil.copy(staged_file, dest_path / java_file)
                else:
                    print(f"[ERROR] Canary test FAILED for {custom_id}/{java_file}")
                    failure_count += 1
                    per_test_status["failed"].append(java_file)
                    carry_forward_tests[custom_id]["failed"].append(java_file)

            if per_test_status["passed"]:
                carry_forward_instances.add(custom_id)

            # Update results incrementally (appending, not replacing everything)
            results[custom_id] = {
                "tests": per_test_status,
                "test_counts": test_counts[custom_id]
            }

            # Save merged state back to JSON
            TRANSPLANT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            TRANSPLANT_OUTPUT.write_text(json.dumps({
                "results": results,
                "carry_forward_instances": list(carry_forward_instances),
                "carry_forward_tests": carry_forward_tests,
                "test_counts": test_counts
            }, indent=2), encoding="utf-8")

            print(f"[INFO] Appended results for {custom_id}")

    # Print summary
    total_generated = sum(v["generated"] for v in test_counts.values())
    total_executed = sum(v["executed"] for v in test_counts.values())
    print(f"[SUMMARY] Successes: {success_count}, Failures: {failure_count}")
    print(f"[SUMMARY] Generated tests: {total_generated}, Executed tests: {total_executed}")
    for cid, counts in test_counts.items():
        print(f"  {cid}: generated={counts['generated']} executed={counts['executed']}")



if __name__ == "__main__":
    main()
