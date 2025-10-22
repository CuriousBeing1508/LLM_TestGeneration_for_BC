import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
from common import (
    parse_package_summary,
    classify_compilation_error,
    LOG_DIR_BATCH_BRE,
    clean_llm_code,
)

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = "/Volumes/Rachna-HD/package_structure_summary.txt"
PRE_RESULTS_PATH = "/Volumes/Rachna-HD/Exp8BatchResults/pre/transplant_results_final_pre.json"
BREAKING_OUTPUT = Path("/Volumes/Rachna-HD/Exp8BatchResults/breaking/transplant_results_final_breaking.json")
ABC_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp7Batch/GPT4o")
MODEL_NAME = ABC_ROOT.name  # e.g., "GPT4o"

pkg_info = parse_package_summary(SUMMARY_PATH)
results = {}

success_count = 0
failure_count = 0
failure_categories = Counter()

# Carry forward info (loaded from pre)
carry_forward_instances = set()
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
    if txt_name.endswith("_prompt.txt"):
        base = txt_name[:-len("_prompt.txt")]
    elif txt_name.endswith(".txt"):
        base = txt_name[:-len(".txt")]
    else:
        base = txt_name
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
    code = code_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"^\s*package\s+[\w\.]+;\s*$", code, flags=re.MULTILINE):
        code = re.sub(
            r"^\s*package\s+[\w\.]+;\s*$",
            f"package {package_decl};",
            code,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        code = f"package {package_decl};\n\n{code}"
    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)", r"\1" + class_name, code, count=1)
    return code


def run_test_in_isolation(image_tag: str, custom_id: str, test_root: str,
                          package_decl: str, java_file: str):
    """
    Transplant one passed test from ABC_ROOT into scratch, then run it inside breaking image.
    """
    # Find source .txt file in ABC_ROOT
    txt_path = None
    src_dir = ABC_ROOT / custom_id
    for p in src_dir.rglob("*.txt"):
        fname, _ = _to_java_filename(p.name)
        if fname == java_file:
            txt_path = p
            break
    if not txt_path:
        print(f"[WARN] Could not find txt for {custom_id}/{java_file}")
        return False, None, ""

    # Prepare scratch dir
    scratch_dir = Path(f"/tmp/llm_exec_breaking/{custom_id}/scratch/{java_file}")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_pkg_dir = scratch_dir / "LLMTest"
    scratch_pkg_dir.mkdir(parents=True, exist_ok=True)

    # Transplant: extract + clean + rewrite
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    java_only = _extract_llm_java_block(raw)
    if not java_only:
        return False, None, ""
    cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
    if not cleaned:
        return False, None, ""
    _, class_base = _to_java_filename(txt_path.name)
    final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)

    pkg_path = Path(*package_decl.split(".")) if package_decl else Path(".")
    out_file = scratch_pkg_dir / pkg_path / java_file
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(final_code, encoding="utf-8")

    # Run docker
    log_path = LOG_DIR_BATCH_BRE / f"{custom_id}_{java_file}_breaking_exec.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    container_mount = f"{test_root}/LLMTest"
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{scratch_pkg_dir}:{container_mount}:ro",
        image_tag,
    ]

    log_lines = [f"[INFO] Running BREAKING test {java_file} for {custom_id} using {image_tag}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        log_lines.append(proc.stdout)
        log_lines.append(proc.stderr)
        test_class = java_file.replace(".java", "")
        m = re.search(
            rf"Running .*{test_class}.*?Tests run: (\d+), Failures: (\d+), Errors: (\d+)",
            proc.stdout,
            flags=re.DOTALL,
        )
        if m:
            failures = int(m.group(2))
            errors = int(m.group(3))
            success = (failures == 0 and errors == 0)
        else:
            success = proc.returncode == 0 and "BUILD SUCCESS" in proc.stdout
    except Exception as e:
        log_lines.append(f"[EXCEPTION] {e}")
        success = False

    log_text = "\n".join(log_lines)
    log_path.write_text(log_text, encoding="utf-8")
    err_info = classify_compilation_error(log_text)
    return success, err_info, str(log_path)


def main():
    global success_count, failure_count, results, carry_forward_instances, carry_forward_tests

    # === BATCH CONFIG ===
    START_ID = 106
    END_ID = 190

    # Load pre results
    pre_data = json.loads(Path(PRE_RESULTS_PATH).read_text(encoding="utf-8"))
    carry_forward_tests.update(pre_data.get("carry_forward_tests", {}))
    carry_forward_instances.update(pre_data.get("carry_forward_instances", []))

    # Load existing JSON if resuming
    if BREAKING_OUTPUT.exists():
        try:
            existing = json.loads(BREAKING_OUTPUT.read_text(encoding="utf-8"))
            results = existing.get("results", {})
            success_count = existing.get("summary", {}).get("total_pass", 0)
            failure_count = existing.get("summary", {}).get("total_fail", 0)
            print(f"[INFO] Loaded existing breaking results with {len(results)} custom_ids")
        except Exception as e:
            print(f"[WARN] Failed to load existing JSON: {e}")

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()

            # numeric range filter
            match = re.search(r"(\d+)$", custom_id)
            if not match:
                continue
            cid_num = int(match.group(1))
            if cid_num < START_ID or cid_num > END_ID:
                continue

            commit = row["breakingCommit"].strip()
            if not commit:
                continue

            print(f"[DEBUG] Checking custom_id={custom_id}, commit={commit}")

            if custom_id not in carry_forward_instances:
                print(f"  -> skipped (not in carry_forward_instances)")
                continue
            if not _abc_has_any_file(custom_id):
                print(f"  -> skipped (no abc files in ABC_ROOT)")
                continue

            test_root, real_package = pkg_info.get((custom_id, "breaking"), (None, None))
            if not test_root or not real_package:
                print(f"  -> skipped (missing test_root/package)")
                continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-breaking"
            per_test_status = {"passed": [], "failed": []}

            for java_file in carry_forward_tests[custom_id]["passed"]:
                success, err_info, log_path = run_test_in_isolation(
                    image_tag, custom_id, test_root, real_package, java_file
                )
                if success:
                    print(f"[INFO] Breaking test PASSED for {custom_id}/{java_file}")
                    success_count += 1
                    per_test_status["passed"].append(java_file)
                else:
                    print(f"[ERROR] Breaking test FAILED for {custom_id}/{java_file}")
                    failure_count += 1
                    per_test_status["failed"].append(java_file)

            results[custom_id] = {"tests": per_test_status}

            # Save incrementally
            BREAKING_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            BREAKING_OUTPUT.write_text(json.dumps({
                "results": results,
                "summary": {
                    "total_pass": success_count,
                    "total_fail": failure_count
                },
                "carry_forward_instances": list(carry_forward_instances),
                "carry_forward_tests": carry_forward_tests
            }, indent=2), encoding="utf-8")

            print(f"[INFO] Saved partial results after {custom_id}")

    print(f"[SUMMARY] Breaking stage: Pass={success_count}, Fail={failure_count}")


if __name__ == "__main__":
    main()
