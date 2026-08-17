#!/usr/bin/env python3
"""
Script: execute_undetected_investigation.py
Outputs one CSV row per (custom_id, model, context_variant, java_file).
"""

import os
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from collections import defaultdict
from common import parse_package_summary, clean_llm_code
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

results_lock = threading.Lock()
print_lock   = threading.Lock()

def safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)

# ============================================================
# CONFIG PER (model, context_variant)
# ============================================================
CONFIGS = {
    # Minimal variant
    # ("GPT-4o",      "Minimal"): {
    #     "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp3LLMOutput/GPT4o",
    #     "pre_results_path": PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/transplant_results_final_pre.json",
    #     "output_dir":       PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/investigate_undetected",
    #     "log_dir":          PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/investigate_undetected/logs",
    # },
    # ("Qwen-480B",   "Minimal"): {
    #     "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp3LLMOutput/Qwen_480b_cloud",
    #     "pre_results_path": PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/transplant_results_final_pre.json",
    #     "output_dir":       PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/investigate_undetected",
    #     "log_dir":          PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/investigate_undetected/logs",
    # },
    # ("GPTOSS-120B", "Minimal"): {
    #     "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp3LLMOutput/GPT_OSS_120b",
    #     "pre_results_path": PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/transplant_results_final_pre.json",
    #     "output_dir":       PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/investigate_undetected",
    #     "log_dir":          PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/investigate_undetected/logs",
    # },

    # Method variant
    # ("GPT-4o",      "Method"): {
    #     "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp6LLMOutput/GPT4o",
    #     "pre_results_path": PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/pre/transplant_results_final_pre.json",
    #     "output_dir":       PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/investigate_undetected",
    #     "log_dir":          PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/investigate_undetected/logs",
    # },
    ("Qwen-480B",   "Method"): {
        "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp6LLMOutput/Qwen3_480b_cloud",
        "pre_results_path": PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/transplant_results_final_pre.json",
        "output_dir":       PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/investigate_undetected",
        "log_dir":          PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/investigate_undetected/logs",
    },
    # ("GPTOSS-120B", "Method"): {
    #     "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp6LLMOutput/GPT_OSS_120b",
    #     "pre_results_path": PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/transplant_results_final_pre.json",
    #     "output_dir":       PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/investigate_undetected",
    #     "log_dir":          PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/investigate_undetected/logs",
    # },

    # Class variant fixed keys (were wrongly "Minimal" before)
    # ("GPT-4o",      "Class"): {
    #     "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp7LLMOutput/GPT4o",
    #     "pre_results_path": PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/transplant_results_final_pre.json",
    #     "output_dir":       PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/investigate_undetected",
    #     "log_dir":          PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/investigate_undetected/logs",
    # },
    ("Qwen-480B",   "Class"): {
        "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp7LLMOutput/Qwen3_480b_cloud",
        "pre_results_path": PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/transplant_results_final_pre.json",
        "output_dir":       PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/investigate_undetected",
        "log_dir":          PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/investigate_undetected/logs",
    },
    # ("GPTOSS-120B", "Class"): {
    #     "abc_root":         PRIMARY_DRIVE / "FilteredDataset/Exp7LLMOutput/GPT_OSS_120b",
    #     "pre_results_path": PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/transplant_results_final_pre.json",
    #     "output_dir":       PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/investigate_undetected",
    #     "log_dir":          PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/investigate_undetected/logs",
    # },
}

UNDETECTED_CSV    = PRIMARY_DRIVE / "RQResultsForPaper/RQ3/MissedBC/ManualBrokenAPICodingBumpUndetected.csv"
SUMMARY_PATH      = PRIMARY_DRIVE / "ConfigFiles/package_structure_summary.txt"
CSV_PATH          = PRIMARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
MULTI_MODULE_LIST = PRIMARY_DRIVE / "ConfigFiles/multi_module_instances.json"

MAX_WORKERS = 4
TIMEOUT     = 600

# CSV output columns — one row per (custom_id, model, variant, java_file)
CSV_FIELDS = [
    "custom_id", "model", "context_variant",
    "broken_oss_api",
    "java_file", "fqn",
    "status",           # investigated | compilation_error | did_not_run | timeout | exception | skip | parse_error
    "test_passed",      # True | False | ""
    "tests_run", "failures", "errors",
    "hypothesis",       # H1 | H2 | ""
    "hypothesis_reason",
    "matched_tokens",   # pipe-joined
    "missing_tokens",   # pipe-joined
    "needs_review",     # True | False | ""
    "log_path",
]

pkg_info = parse_package_summary(SUMMARY_PATH)


# ============================================================
# HELPERS
# ============================================================

def _sanitize_class_name(name: str) -> str:
    cleaned = [(c if (c.isalnum() or c == "_") else "_") for c in name]
    if not cleaned:
        return "XEmpty"
    base = "".join(cleaned)
    return ("X" + base) if base[0].isdigit() else base


def _to_java_filename(txt_name: str) -> tuple[str, str]:
    base = txt_name
    for suffix in ("_prompt.txt", ".txt"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    base = _sanitize_class_name(base)
    return f"{base}.java", base


def _extract_llm_java_block(text: str) -> str:
    lines, in_block, buf = text.splitlines(), False, []
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


def _rewrite_package_and_class(code: str, package_decl: str, class_name: str) -> str:
    code = code.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"^\s*package\s+[\w\.]+;\s*$", code, flags=re.MULTILINE):
        code = re.sub(r"^\s*package\s+[\w\.]+;\s*$",
                      f"package {package_decl};", code, count=1, flags=re.MULTILINE)
    else:
        code = f"package {package_decl};\n\n{code}"
    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)",
                  r"\1" + class_name, code, count=1)
    return code


def _abc_has_any_file(abc_root: Path, custom_id: str) -> bool:
    d = abc_root / custom_id
    return d.is_dir() and any(True for p in d.rglob("*") if p.is_file())


def _load_multi_module_list() -> set:
    if not MULTI_MODULE_LIST.exists():
        return set()
    try:
        return set(json.loads(MULTI_MODULE_LIST.read_text(encoding="utf-8")).keys())
    except Exception as e:
        safe_print(f"[ERROR] Failed to load multi-module list: {e}")
        return set()


def cleanup_stale_containers(prefix="investigation_"):
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
            capture_output=True, text=True
        )
        names = [n for n in result.stdout.strip().split("\n") if n.startswith(prefix)]
        for name in names:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
        if names:
            safe_print(f"[CLEANUP] Removed {len(names)} stale containers")
    except Exception as e:
        safe_print(f"[CLEANUP] Warning: {e}")


# ============================================================
# CLASSIFY H1 vs H2
# ============================================================

def _api_in_log(log_text: str, broken_oss_api: str) -> dict:
    if not broken_oss_api:
        return {"found": False, "matched_tokens": [], "missing_tokens": []}

    tokens = [t.strip() for t in broken_oss_api.split("|") if t.strip()]

    # [info][class,load] lines from dumpstream
    loaded_classes = set(
        m.group(1)
        for m in re.finditer(
            r"\[info\]\[class,load\]\s+([\w\.$]+)",
            log_text, re.IGNORECASE,
        )
    )

    # Also catch failed loads and stack trace lines
    for m in re.finditer(
        r"(?:NoClassDefFoundError|ClassNotFoundException)[:\s]+([\w\.$]+|[\w/$]+)",
        log_text, re.IGNORECASE,
    ):
        loaded_classes.add(m.group(1).replace("/", "."))

    for m in re.finditer(r"\bat\s+([\w\.$]+)\.\w+\(", log_text):
        loaded_classes.add(m.group(1))

    matched, missing = [], []
    for token in tokens:
        class_part = token.rsplit(".", 1)[0] if "." in token else token
        if token in loaded_classes or class_part in loaded_classes:
            matched.append(token)
        else:
            missing.append(token)

    return {
        "found":          bool(matched),
        "matched_tokens": matched,
        "missing_tokens": missing,
    }


def classify_result(
    test_passed: bool,
    tests_run: int,
    failures: int,
    errors: int,
    log_text: str,
    broken_oss_api: str,
) -> dict:
    api_result = _api_in_log(log_text, broken_oss_api)
    api_seen   = api_result["found"]

    if test_passed:
        hypothesis   = "H2" if api_seen else "H1"
        needs_review = False
        reason = (
            f"Test PASSED, broken API {'WAS' if api_seen else 'NOT'} loaded. "
            f"Matched: {api_result['matched_tokens']}  "
            f"Missing: {api_result['missing_tokens']}"
        )
    else:
        hypothesis   = "H2" if api_seen else "H1"
        needs_review = api_seen   # H2 + failed = unexpected, flag it
        reason = (
            f"Test FAILED, broken API {'WAS' if api_seen else 'NOT'} loaded. "
            f"Matched: {api_result['matched_tokens']}  "
            f"Missing: {api_result['missing_tokens']}"
            + (" → flag for review" if needs_review else "")
        )

    return {
        "hypothesis":     hypothesis,
        "reason":         reason,
        "needs_review":   needs_review,
        "matched_tokens": api_result["matched_tokens"],
        "missing_tokens": api_result["missing_tokens"],
    }


# ============================================================
# CSV helpers
# ============================================================

def _load_existing_csv(path: Path) -> set[tuple]:
    """Return set of (custom_id, java_file) already written."""
    done = set()
    if not path.exists():
        return done
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add((row.get("custom_id", ""), row.get("java_file", "")))
    return done


def _append_rows(path: Path, rows: list[dict]) -> None:
    """Append rows to CSV, writing header only if file is new."""
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(rows)


# ============================================================
# CORE EXECUTION
# ============================================================

def run_investigation_test(
    image_tag: str,
    custom_id: str,
    test_root: str,
    package_decl: str,
    java_file: str,
    txt_path: Path,
    broken_oss_api: str,
    log_dir: Path,
    model: str,
    context_variant: str,
) -> dict:
    """Returns one flat dict matching CSV_FIELDS."""

    temp_dir = Path(f"/tmp/investigation_{custom_id}_{java_file.replace('.java', '')}")
    container_name = (
        f"investigation_{custom_id}_{java_file.replace('.java', '')}"
        f"_{os.getpid()}_{threading.get_ident()}"
    )

    # Base row — always present regardless of outcome
    base = {
        "custom_id":        custom_id,
        "model":            model,
        "context_variant":  context_variant,
        "broken_oss_api":   broken_oss_api,
        "java_file":        java_file,
        "fqn":              "",
        "status":           "",
        "test_passed":      "",
        "tests_run":        "",
        "failures":         "",
        "errors":           "",
        "hypothesis":       "",
        "hypothesis_reason":"",
        "matched_tokens":   "",
        "missing_tokens":   "",
        "needs_review":     "",
        "log_path":         "",
    }

    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        subprocess.run(["docker", "rm", "-f", container_name],
                       capture_output=True, timeout=10)

        raw       = txt_path.read_text(encoding="utf-8", errors="ignore")
        java_only = _extract_llm_java_block(raw)
        if not java_only:
            return {**base, "status": "skip", "hypothesis_reason": "No Java code block found"}

        cleaned = "\n".join(clean_llm_code(java_only.splitlines())).strip()
        if not cleaned:
            return {**base, "status": "skip", "hypothesis_reason": "Empty after cleaning"}

        _, class_base = _to_java_filename(txt_path.name)
        final_code    = _rewrite_package_and_class(cleaned, package_decl, class_base)

        pkg_path = Path(*package_decl.split("."))
        java_dir = temp_dir / "LLMTest" / pkg_path
        java_dir.mkdir(parents=True, exist_ok=True)
        (java_dir / java_file).write_text(final_code, encoding="utf-8")

        llm_test_dir = temp_dir / "LLMTest"
        log_path     = log_dir / f"{custom_id}_{java_file}_investigation.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        parts = test_root.strip("/").split("/")
        project_root = (
            "/" + "/".join(parts[: parts.index("src")])
            if "src" in parts and parts.index("src") >= 1
            else "/workspace"
        )

        test_class = java_file.replace(".java", "")
        fqn        = f"{package_decl}.{test_class}"
        base["fqn"]      = fqn
        base["log_path"] = str(log_path)

        exec_cmd = (
            f"cd {project_root} && "
            f"javac -cp \"target/classes:target/test-classes:"
            f"$(mvn dependency:build-classpath -q -DincludeScope=test "
            f"-Dmdep.outputFile=/dev/stdout 2>/dev/null)\" "
            f"-d target/test-classes "
            f"{test_root}/{pkg_path.as_posix()}/{java_file} 2>&1 && "
            f"mvn surefire:test "
            f"-Dtest={fqn} "
            f"-DfailIfNoTests=false "
            f"-Dpmd.skip=true -Dcheckstyle.skip=true -Denforcer.skip=true "
            f"-DforkCount=1 "
            f"-DreuseForks=false "
            f"-Dsurefire.useFile=false "
            f"\"-DargLine=-verbose:class\" "
            f"2>&1; "
            f"echo '=== DUMPSTREAM ==='; "
            f"cat {project_root}/target/surefire-reports/*.dumpstream 2>/dev/null"
        )

        cmd = [
            "docker", "run",
            "--name", container_name,
            "--rm",
            "--platform", "linux/amd64",
            "-v", f"{llm_test_dir}:{test_root}:ro",
            image_tag,
            "sh", "-c", exec_cmd,
        ]

        try:
            proc     = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
            combined = proc.stdout + "\n" + proc.stderr

            log_path.write_text(
                f"{'='*80}\nINVESTIGATION: {java_file} | {custom_id}\n{'='*80}\n"
                f"FQN: {fqn}\nBroken OSS API: {broken_oss_api}\n{'='*80}\n"
                f"CMD: {exec_cmd}\n{'='*80}\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}\n"
                f"EXIT CODE: {proc.returncode}\n{'='*80}\n",
                encoding="utf-8",
            )

            # ── compilation check ────────────────────────────────────────────
            test_ran = bool(
                re.search(rf"Running\s+{re.escape(fqn)}", combined) or
                re.search(rf"Running\s+\S*{re.escape(test_class)}", combined)
            )
            if "COMPILATION ERROR" in combined or (
                "error:" in combined.lower() and "javac" in combined.lower()
                and not test_ran
            ):
                return {**base, "status": "compilation_error",
                        "hypothesis_reason": "Test code failed to compile"}

            if not test_ran:
                reason = (
                    "BUILD SUCCESS but test did not execute"
                    if "BUILD SUCCESS" in combined
                    else "BUILD FAILURE before test could run"
                )
                return {**base, "status": "did_not_run", "hypothesis_reason": reason}

            # ── parse results ────────────────────────────────────────────────
            block_pattern = re.compile(
                rf"Running\s+\S*{re.escape(test_class)}.*?Tests run:\s*(\d+)[^\n]*"
                rf"Failures:\s*(\d+)[^\n]*Errors:\s*(\d+)",
                re.DOTALL,
            )
            m = block_pattern.search(combined)
            if not m:
                return {**base, "status": "parse_error",
                        "hypothesis_reason": "Could not parse 'Tests run' line"}

            tests_run = int(m.group(1))
            failures  = int(m.group(2))
            errors    = int(m.group(3))
            passed    = (tests_run > 0 and failures == 0 and errors == 0)

            clf = classify_result(passed, tests_run, failures, errors, combined, broken_oss_api)

            return {
                **base,
                "status":           "investigated",
                "test_passed":      passed,
                "tests_run":        tests_run,
                "failures":         failures,
                "errors":           errors,
                "hypothesis":       clf["hypothesis"],
                "hypothesis_reason":clf["reason"],
                "matched_tokens":   "|".join(clf["matched_tokens"]),
                "missing_tokens":   "|".join(clf["missing_tokens"]),
                "needs_review":     clf["needs_review"],
            }

        except subprocess.TimeoutExpired:
            for sub in [["docker", "kill", container_name],
                        ["docker", "rm", "-f", container_name]]:
                subprocess.run(sub, capture_output=True, timeout=10)
            return {**base, "status": "timeout",
                    "hypothesis_reason": "Exceeded 600 seconds"}

        except Exception as e:
            subprocess.run(["docker", "rm", "-f", container_name],
                           capture_output=True, timeout=10)
            return {**base, "status": "exception", "hypothesis_reason": str(e)}

    finally:
        subprocess.run(["docker", "rm", "-f", container_name],
                       capture_output=True, timeout=10)
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


# ============================================================
# MAIN
# ============================================================

def main():
    safe_print(f"\n{'='*80}")
    safe_print("Investigation: H1 (never reached) vs H2 (reached, no assert)")
    safe_print(f"{'='*80}\n")

    cleanup_stale_containers()
    multi_module_instances = _load_multi_module_list()

    commit_lookup = {}
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            commit_lookup[row["custom_id"].strip()] = row.get("breakingCommit", "").strip()

    groups: dict[tuple, list] = defaultdict(list)
    with open(UNDETECTED_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=",")
        reader.fieldnames = [n.strip().strip("\ufeff") for n in reader.fieldnames]
        safe_print(f"[DEBUG] CSV headers: {reader.fieldnames}")
        for row in reader:
            row = {k.strip(): (v.strip() if v else "") for k, v in row.items()}
            groups[(row["model"], row["context_variant"])].append(row)

    safe_print(f"[INFO] {sum(len(v) for v in groups.values())} undetected cases")
    safe_print(f"[INFO] {len(commit_lookup)} commit mappings loaded\n")

    for (model, variant), rows in groups.items():
        if (model, variant) not in CONFIGS:
            safe_print(f"[WARN] No config for ({model}, {variant}) — skipping")
            continue

        cfg        = CONFIGS[(model, variant)]
        abc_root   = cfg["abc_root"]
        log_dir    = cfg["log_dir"]
        output_dir = cfg["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        # One CSV per (model, variant)
        output_csv = output_dir / f"investigation_{model}_{variant}.csv".replace(" ", "_")

        # Resume: load already-processed (custom_id, java_file) pairs
        done_keys = _load_existing_csv(output_csv)
        safe_print(f"\n{'='*60}")
        safe_print(f"({model}, {variant}) — {len(rows)} cases, {len(done_keys)} already done")
        safe_print(f"{'='*60}")

        pre_data            = json.loads(cfg["pre_results_path"].read_text(encoding="utf-8"))
        carry_forward_tests = pre_data.get("carry_forward_tests", {})

        for row in rows:
            custom_id  = row["custom_id"].strip()
            broken_oss = row["Broken_oss_API"].strip()

            if custom_id in multi_module_instances:
                safe_print(f"[SKIP] {custom_id} — multi-module"); continue
            if not commit_lookup.get(custom_id):
                safe_print(f"[SKIP] {custom_id} — no breakingCommit"); continue
            if not _abc_has_any_file(abc_root, custom_id):
                safe_print(f"[SKIP] {custom_id} — no LLM output files"); continue
            if not broken_oss:
                safe_print(f"[SKIP] {custom_id} — no Broken_oss_API coded"); continue

            passed_tests = carry_forward_tests.get(custom_id, {}).get("passed", [])
            if not passed_tests:
                safe_print(f"[SKIP] {custom_id} — no passing tests"); continue

            test_root, real_package = pkg_info.get((custom_id, "breaking"), (None, None))
            if not test_root or not real_package:
                safe_print(f"[SKIP] {custom_id} — no test_root/package"); continue

            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit_lookup[custom_id]}-breaking"

            src_dir = abc_root / custom_id
            test_files_info = []
            for java_file in passed_tests:
                # Skip already done
                if (custom_id, java_file) in done_keys:
                    safe_print(f"[SKIP] {custom_id}/{java_file} — already in CSV")
                    continue
                for txt_path in src_dir.rglob("*.txt"):
                    if _to_java_filename(txt_path.name)[0] == java_file:
                        test_files_info.append((java_file, txt_path))
                        break

            if not test_files_info:
                safe_print(f"  [SKIP] {custom_id} — no new files to process")
                continue

            safe_print(f"\n[{custom_id}] broken_oss={broken_oss} files={[f for f,_ in test_files_info]}")

            tasks = [
                (image_tag, custom_id, test_root, real_package,
                 jf, tp, broken_oss, log_dir, model, variant)
                for jf, tp in test_files_info
            ]

            batch_rows = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(run_investigation_test, *t): t[4] for t in tasks}
                for future in as_completed(futures):
                    java_file = futures[future]
                    try:
                        result = future.result()
                        batch_rows.append(result)
                        safe_print(
                            f"  → {java_file} "
                            f"status={result['status']} "
                            f"hypothesis={result.get('hypothesis','?')}"
                            f"{' ⚠' if result.get('needs_review') else ''}"
                        )
                    except Exception as exc:
                        safe_print(f"  → {java_file} EXCEPTION: {exc}")
                        batch_rows.append({
                            "custom_id": custom_id, "model": model,
                            "context_variant": variant, "java_file": java_file,
                            "broken_oss_api": broken_oss,
                            "status": "exception", "hypothesis_reason": str(exc),
                            **{k: "" for k in CSV_FIELDS
                               if k not in ("custom_id","model","context_variant",
                                            "java_file","broken_oss_api",
                                            "status","hypothesis_reason")}
                        })

            # Append this batch immediately — safe against crashes
            _append_rows(output_csv, batch_rows)
            safe_print(f"  [SAVED] {len(batch_rows)} rows → {output_csv}")

    # ── Final summary ────────────────────────────────────────────────────────
    safe_print(f"\n{'='*80}")
    safe_print("INVESTIGATION COMPLETE — Summary")
    safe_print(f"{'='*80}")
    total_h1 = total_h2 = 0
    for (model, variant) in groups:
        cfg = CONFIGS.get((model, variant))
        if not cfg:
            continue
        out = cfg["output_dir"] / f"investigation_{model}_{variant}.csv".replace(" ", "_")
        if not out.exists():
            continue
        with open(out, newline="", encoding="utf-8") as f:
            data = list(csv.DictReader(f))
        h1 = sum(1 for r in data if r.get("hypothesis") == "H1")
        h2 = sum(1 for r in data if r.get("hypothesis") == "H2")
        total_h1 += h1
        total_h2 += h2
        safe_print(f"  ({model}, {variant}): H1={h1} H2={h2} total={len(data)}")

    safe_print(f"\nOverall: H1={total_h1} H2={total_h2}")
    safe_print("  H1: Test never reached broken OSS API")
    safe_print("  H2: Test reached broken API but had no/weak assertion")
    safe_print(f"{'='*80}\n")
    cleanup_stale_containers()


if __name__ == "__main__":
    main()