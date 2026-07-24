"""
Recover GPTOSS-120B / Qwen-480B "no_code" entries.

compile_results_pre.json marks a failed test as error_category == "no_code"
when the pipeline's code-extraction step found no ```java fenced block in the
LLM's raw response and gave up *before* ever invoking javac. Sampling showed
this is a false negative for GPTOSS-120B (~90% of its failures) and Qwen-480B
(5-16%): the raw response is usually complete, valid-looking Java that was
just never wrapped in a markdown fence. GPT4o has 0% no_code in every variant
(it always fences) and is untouched by anything in this file.

This script is fully self-contained — it does not import from or modify any
of the production OptimizationPre/Phase1Compilationv1.py scripts or common.py
for any model, and it never writes to the existing compile_results_pre.json
or logs/ folders. It only reads them (to find which entries are no_code) plus
the shared, model-independent CSV/package-summary config files.

Outputs, per (model, variant), are written to NEW locations only:
  - no_code_recovery_results.json  (next to the original compile_results_pre.json)
  - logs_recovered/                (next to the original logs/ folder)

Usage: python3 RecoverNoCodeEntries.py
Docker must be running locally with the breaking-updates images reachable.
Safe to interrupt (Ctrl+C) and re-run — already-recovered entries are skipped.
"""

import csv
import json
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

CSV_PATH = PRIMARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
SUMMARY_PATH = PRIMARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
COMPILE_TIMEOUT = 300
MAX_WORKERS = 8

RECOVERY_TARGETS = [
    {
        "model": "GPTOSS-120B", "variant": "Class",
        "abc_root": PRIMARY_DRIVE / "FilteredDataset/Exp7LLMOutput/GPT_OSS_120b",
        "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/compile_results_pre.json",
        "output_json": PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/no_code_recovery_results.json",
        "logs_dir": PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/logs_recovered",
    },
    {
        "model": "GPTOSS-120B", "variant": "Method",
        "abc_root": PRIMARY_DRIVE / "FilteredDataset/Exp6LLMOutput/GPT_OSS_120b",
        "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/compile_results_pre.json",
        "output_json": PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/no_code_recovery_results.json",
        "logs_dir": PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/logs_recovered",
    },
    {
        "model": "GPTOSS-120B", "variant": "Minimal",
        "abc_root": PRIMARY_DRIVE / "FilteredDataset/Exp3LLMOutput/GPT_OSS_120b",
        "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/compile_results_pre.json",
        "output_json": PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/no_code_recovery_results.json",
        "logs_dir": PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/logs_recovered",
    },
    {
        "model": "Qwen-480B", "variant": "Class",
        "abc_root": PRIMARY_DRIVE / "FilteredDataset/Exp7LLMOutput/Qwen3_480b_cloud",
        "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/compile_results_pre.json",
        "output_json": PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/no_code_recovery_results.json",
        "logs_dir": PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/logs_recovered",
    },
    {
        "model": "Qwen-480B", "variant": "Method",
        "abc_root": PRIMARY_DRIVE / "FilteredDataset/Exp6LLMOutput/Qwen3_480b_cloud",
        "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/compile_results_pre.json",
        "output_json": PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/no_code_recovery_results.json",
        "logs_dir": PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/logs_recovered",
    },
    {
        # Note: Exp3's Qwen output folder is spelled without the "3" —
        # verified on disk, not a typo.
        "model": "Qwen-480B", "variant": "Minimal",
        "abc_root": PRIMARY_DRIVE / "FilteredDataset/Exp3LLMOutput/Qwen_480b_cloud",
        "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/compile_results_pre.json",
        "output_json": PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/no_code_recovery_results.json",
        "logs_dir": PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/logs_recovered",
    },
]

results_lock = threading.Lock()
print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)


# ============================================================
# Copied, read-only utilities (from common.py) — no shared state, no imports
# from the production pipeline files.
# ============================================================

def parse_package_summary(path):
    """dict: {(custom_id, stage): (test_root, package)}"""
    info = {}
    current_id = current_type = None
    test_root = package = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("===="):
                parts = line.split(" | ")
                if len(parts) >= 2:
                    current_id = parts[0].replace("====", "").strip()
                    current_type = parts[1].strip()
                    test_root = package = None
            elif line.startswith("Test root:"):
                test_root = line.split("Test root:")[1].strip()
            elif line.startswith("package:"):
                package = line.split("package:")[1].strip()
            if current_id and current_type and test_root and package:
                info[(current_id, current_type)] = (test_root, package)
                current_id = current_type = test_root = package = None
    return info


def get_last_lines(log_content, num_lines=50):
    lines = log_content.strip().splitlines()
    return "\n".join(lines[-num_lines:]).strip()


def classify_compilation_error(log_content):
    log_lower = log_content.lower()
    if "lambda expressions are not supported in" in log_lower:
        return {"category": "syntax error", "subtype": "java version incompatibility",
                "reason": "LLM-generated code uses Java 8+ features (e.g., lambdas), "
                          "but the target project compiles with -source 1.7"}
    elif "illegal character: '`'" in log_lower:
        return {"category": "syntax error", "subtype": "invalid character",
                "reason": "Backticks (`) are not valid in Java. Possibly introduced by LLM formatting."}
    elif "cannot find symbol" in log_lower or "symbol:   class" in log_lower:
        return {"category": "dependency error",
                "reason": "Missing or unrecognized classes; possibly a dependency issue."}
    elif " ';' expected" in log_lower or "illegal start of type" in log_lower:
        return {"category": "syntax error", "reason": "Likely syntax issue in the Java code."}
    else:
        return {"category": "unknown", "reason": get_last_lines(log_content, 10)}


def clean_llm_code(lines):
    cleaned = []
    for line in lines:
        if line.strip().startswith("```"):
            continue
        line = line.replace("`", "")
        line = line.replace("‘", "'").replace("’", "'")
        line = line.replace("“", '"').replace("”", '"')
        cleaned.append(line)
    return cleaned


# ============================================================
# The fix: extraction with a fallback for unfenced-but-valid Java
# ============================================================

def extract_java_block(text: str) -> str:
    """Same fence-scan as the original _extract_llm_java_block, but also
    accepts a bare ``` fence, and — if no fence exists at all — falls back
    to treating the whole response as code when it structurally looks like
    a real Java file. Purely additive: a properly ```java-fenced response
    (GPT4o's case, always) behaves identically to before."""
    lines = text.splitlines()
    in_block, buf = False, []
    for line in lines:
        s = line.strip()
        if not in_block:
            if s.lower() in ("```java", "```"):
                in_block = True
        else:
            if s == "```":
                break
            buf.append(line)
    fenced = "\n".join(buf).strip()
    if fenced:
        return fenced

    if re.search(r"^\s*(package\s+[\w.]+;|import\s+[\w.]+;|public\s+(final\s+)?class\s+\w+)",
                 text, re.MULTILINE):
        return text.strip()
    return ""


# ============================================================
# Copied, adapted from Phase1Compilationv1.py
# ============================================================

def _sanitize_class_name(name: str) -> str:
    cleaned = [ch if (ch.isalnum() or ch == "_") else "_" for ch in name]
    if not cleaned:
        return "XEmpty"
    base = "".join(cleaned)
    if base[0].isdigit():
        base = "X" + base
    return base


def _to_java_filename(txt_name: str):
    base = txt_name
    if base.endswith("_prompt.txt"):
        base = base[: -len("_prompt.txt")]
    elif base.endswith(".txt"):
        base = base[: -len(".txt")]
    base = _sanitize_class_name(base)
    return f"{base}.java", base


def _rewrite_package_and_class(code_text: str, package_decl: str, class_name: str) -> str:
    code = code_text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"^\s*package\s+[\w\.]+;\s*$", code, flags=re.MULTILINE):
        code = re.sub(r"^\s*package\s+[\w\.]+;\s*$", f"package {package_decl};",
                       code, count=1, flags=re.MULTILINE)
    else:
        code = f"package {package_decl};\n\n{code}"
    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)", r"\1" + class_name, code, count=1)
    return code


def _count_test_methods(java_path: Path) -> int:
    if not java_path.exists():
        return 0
    text = java_path.read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r"@Test\b", text))


def cleanup_stale_containers():
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=recover_", "--filter", "status=exited",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        names = [n for n in result.stdout.strip().split("\n") if n.startswith("recover_")]
        for name in names:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
        if names:
            safe_print(f"[CLEANUP] Removed {len(names)} stuck container(s)")
    except Exception as e:
        safe_print(f"[CLEANUP] Warning: could not check for stuck containers: {e}")


def compile_test_in_docker(image_tag: str, custom_id: str, test_root: str,
                            java_file: str, package_decl: str, txt_path: Path,
                            logs_dir: Path):
    """Same Docker invocation / javac command / timeout handling as the
    original compile_test_in_docker, with the fixed extract_java_block and
    a caller-supplied logs_dir instead of the shared LOG_DIR_BATCH global."""
    temp_dir = Path(f"/tmp/recover_{custom_id}_{java_file.replace('.java', '')}")
    container_name = f"recover_{custom_id}_{java_file.replace('.java', '')}_{int(time.time() * 1000)}"

    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        raw = txt_path.read_text(encoding="utf-8", errors="ignore")
        java_only = extract_java_block(raw)
        if not java_only:
            return False, {"category": "no_code", "reason": "No Java code block found (even with fallback)"}, "", 0

        cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
        if not cleaned:
            return False, {"category": "empty_code", "reason": "Empty after cleaning"}, "", 0

        _, class_base = _to_java_filename(txt_path.name)
        final_code = _rewrite_package_and_class(cleaned, package_decl, class_base)

        llm_test_dir = temp_dir / "LLMTest"
        pkg_path = Path(*package_decl.split("."))
        java_dir = llm_test_dir / pkg_path
        java_dir.mkdir(parents=True, exist_ok=True)

        java_file_path = java_dir / java_file
        java_file_path.write_text(final_code, encoding="utf-8")
        test_method_count = _count_test_methods(java_file_path)

        log_path = logs_dir / f"{custom_id}_{java_file}_compile.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        project_root = None
        test_root_parts = test_root.strip("/").split("/")
        if "src" in test_root_parts:
            src_index = test_root_parts.index("src")
            if src_index >= 1:
                project_root = "/" + "/".join(test_root_parts[:src_index])
        if not project_root or project_root == "/":
            project_root = "/workspace"

        compile_cmd = (
            f"cd {project_root} && "
            f"javac -cp \"target/classes:target/test-classes:"
            f"$(mvn dependency:build-classpath -q -DincludeScope=test -Dmdep.outputFile=/dev/stdout 2>/dev/null)\" "
            f"-d target/test-classes "
            f"{test_root}/{pkg_path.as_posix()}/{java_file}"
        )

        cmd = [
            "docker", "run", "--name", container_name, "--rm",
            "--platform", "linux/amd64",
            "-v", f"{llm_test_dir}:{test_root}:ro",
            image_tag, "sh", "-c", compile_cmd,
        ]

        safe_print(f"[RECOVER] {custom_id}/{java_file} ...")

        log_lines = [
            "=" * 80,
            f"RECOVERY COMPILATION - {java_file} for {custom_id}",
            f"Container: {container_name}",
            "=" * 80,
            f"Image: {image_tag}",
            f"Package: {package_decl}",
            f"Test methods: {test_method_count}",
            f"Project root: {project_root}",
            f"Test root: {test_root}",
            f"Timeout: {COMPILE_TIMEOUT}s",
            "=" * 80,
            f"Command: {compile_cmd}",
            "=" * 80,
            "",
        ]

        success = False
        err_info = None
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT)
            stdout, stderr = proc.stdout, proc.stderr
            combined = stdout + "\n" + stderr

            log_lines += ["=== STDOUT ===", stdout, "", "=== STDERR ===", stderr, ""]

            has_compilation_error = (
                "COMPILATION ERROR" in combined
                or ("error:" in combined.lower() and "javac" in combined)
                or "cannot find symbol" in combined
                or ("package" in combined and "does not exist" in combined)
            )

            if has_compilation_error:
                log_lines.append("[RESULT] ✗ Compilation FAILED")
                success = False
            elif proc.returncode == 0:
                log_lines.append("[RESULT] ✓ Compilation SUCCESS")
                success = True
            else:
                log_lines.append(f"[RESULT] ✗ Compilation FAILED (return code: {proc.returncode})")
                success = False

        except subprocess.TimeoutExpired:
            log_lines.append(f"[ERROR] ✗ Timeout after {COMPILE_TIMEOUT}s - Force killing container")
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=10)
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
            except Exception:
                pass
            err_info = {"category": "timeout", "reason": f"Compilation timeout ({COMPILE_TIMEOUT}s)"}
            success = False
        except Exception as e:
            log_lines.append(f"[EXCEPTION] {e}")
            success = False
            err_info = {"category": "exception", "reason": str(e)}

        log_lines.append("=" * 80)
        log_text = "\n".join(log_lines)
        log_path.write_text(log_text, encoding="utf-8")

        if not success and err_info is None:
            err_info = classify_compilation_error(log_text)

        safe_print(f"[RECOVER] {custom_id}/{java_file} -> {'COMPILED' if success else 'FAILED'}")
        return success, err_info, str(log_path), test_method_count

    finally:
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                safe_print(f"[WARN] Failed to remove temp dir {temp_dir}: {e}")


# ============================================================
# Recovery orchestration
# ============================================================

def load_commit_map(csv_path: Path) -> dict:
    commit_map = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            custom_id = row["custom_id"].strip()
            commit = row["breakingCommit"].strip()
            if custom_id and commit:
                commit_map[custom_id] = commit
    return commit_map


def get_no_code_entries(compilation_json: Path):
    """[(instance, test_name), ...] for every failed entry with error_category == 'no_code'."""
    with open(compilation_json) as f:
        data = json.load(f)
    entries = []
    for instance, res in data.get("compilation_results", {}).items():
        for test_name, info in res.get("failed", {}).items():
            if info.get("error_category") == "no_code":
                entries.append((instance, test_name))
    return entries


def load_existing_recovery(output_json: Path) -> dict:
    if output_json.exists():
        try:
            return json.loads(output_json.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"recovered_results": {}}


def save_recovery(output_json: Path, recovered_results: dict):
    total = processed = compiled = failed_with_diag = still_no_code = other = 0
    for instance, tests in recovered_results.items():
        for test_name, r in tests.items():
            total += 1
            processed += 1
            if r["success"]:
                compiled += 1
            elif r.get("error_category") == "no_code":
                still_no_code += 1
            elif r.get("error_category") in ("timeout", "exception", "empty_code"):
                other += 1
            else:
                failed_with_diag += 1

    output_data = {
        "recovered_results": recovered_results,
        "summary": {
            "processed": processed,
            "recovered_compiled": compiled,
            "recovered_failed_with_diagnostic": failed_with_diag,
            "still_no_code": still_no_code,
            "other_errors": other,
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(output_data, indent=2), encoding="utf-8")


def recover_target(target: dict, commit_map: dict, pkg_info: dict):
    model, variant = target["model"], target["variant"]
    safe_print(f"\n{'=' * 80}\n{model} / {variant}\n{'=' * 80}")

    no_code_entries = get_no_code_entries(target["compilation_json"])
    safe_print(f"  no_code entries in source JSON: {len(no_code_entries)}")

    existing = load_existing_recovery(target["output_json"])
    recovered_results = existing["recovered_results"]
    already_done = {
        (instance, test_name)
        for instance, tests in recovered_results.items()
        for test_name in tests
    }
    todo = [e for e in no_code_entries if e not in already_done]
    safe_print(f"  already recovered (resume): {len(already_done)}")
    safe_print(f"  remaining to process: {len(todo)}")

    if not todo:
        safe_print("  Nothing to do.")
        return

    by_instance = defaultdict(list)
    for instance, test_name in todo:
        by_instance[instance].append(test_name)

    for instance, test_names in by_instance.items():
        commit = commit_map.get(instance)
        test_root, package_decl = pkg_info.get((instance, "pre"), (None, None))
        if not commit or not test_root or not package_decl:
            safe_print(f"  [SKIP] {instance} - missing commit/test_root/package in shared config")
            continue

        image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
        tasks = []
        for test_name in test_names:
            txt_path = target["abc_root"] / instance / f"{test_name.replace('.java', '')}_prompt.txt"
            if not txt_path.exists():
                safe_print(f"  [SKIP] {instance}/{test_name} - raw output file not found: {txt_path}")
                continue
            tasks.append((test_name, txt_path))

        if not tasks:
            continue

        safe_print(f"  {instance}: recovering {len(tasks)} entr{'y' if len(tasks)==1 else 'ies'}")

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(tasks))) as executor:
            future_to_test = {
                executor.submit(
                    compile_test_in_docker, image_tag, instance, test_root,
                    test_name, package_decl, txt_path, target["logs_dir"]
                ): test_name
                for test_name, txt_path in tasks
            }
            for future in as_completed(future_to_test):
                test_name = future_to_test[future]
                try:
                    success, err_info, log_path, test_count = future.result()
                except Exception as exc:
                    success, err_info, log_path, test_count = (
                        False, {"category": "exception", "reason": str(exc)}, "", 0
                    )
                with results_lock:
                    recovered_results.setdefault(instance, {})[test_name] = {
                        "success": success,
                        "error_category": err_info.get("category", "") if err_info else "",
                        "error_info": err_info,
                        "test_method_count": test_count,
                        "log_path": log_path,
                    }

        with results_lock:
            save_recovery(target["output_json"], recovered_results)
        safe_print(f"  [SAVED] {instance} -> {target['output_json']}")

    summary = load_existing_recovery(target["output_json"]).get("summary", {})
    safe_print(f"  DONE. {summary}")


def main():
    safe_print("Loading shared config (CSV + package summary)...")
    commit_map = load_commit_map(CSV_PATH)
    pkg_info = parse_package_summary(SUMMARY_PATH)

    cleanup_stale_containers()
    try:
        for target in RECOVERY_TARGETS:
            recover_target(target, commit_map, pkg_info)
    except KeyboardInterrupt:
        safe_print("\n[INTERRUPTED] Progress has been saved incrementally per instance. "
                    "Re-run this script to resume.")
    finally:
        cleanup_stale_containers()


if __name__ == "__main__":
    main()
