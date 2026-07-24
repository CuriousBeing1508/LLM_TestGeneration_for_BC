# """
# Error type (BUMP ground-truth exception vs LLM-detected exception) per test
# file / test case, for every DETECTED instance — same population as
# export_oracle_type_per_testcase.py (both read the same oracle_types_detected_
# success.json, so the two CSVs are joinable on
# context_variant/model/instance/test_file/test_case).

# Refines RQ2.2_ErrorTypComp's whole-file exception extraction
# (ErrorExtractor.extract_errors_from_log, reused as-is here) down to one
# Surefire "Time elapsed: ... <<< FAILURE!/ERROR!" block per test method, so
# each test case gets its own detected-exception set instead of the whole
# file's exceptions being attributed to every method in it. A test case with
# no such block in the log is a PASS: it contributed nothing to detection.

# bump_bc_errors is inherently instance-level (BUMP's ground truth doesn't
# distinguish by test case) and is repeated for every test case of that
# instance; matched/missed/new are computed per test case against it.
# """
# import csv
# import json
# import re
# import sys
# from pathlib import Path
# from typing import Set

# import pandas as pd

# sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
# from config import PRIMARY_DRIVE

# OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "success_cases"
# ORACLE_JSON = OUTPUT_DIR / "oracle_types_detected_success.json"
# OUTPUT_CSV = OUTPUT_DIR / "error_type_per_testcase_detected.csv"

# BUMP_CSV = PRIMARY_DRIVE / "RQResultsForPaper/RQ2/BUMPErrorLogs/RQ4_resultsBUMP.csv"

# # context/model labels used in oracle_types_detected_success.json -> bre logs dir
# BRE_LOGS_DIR = {
#     ("Class", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/bre/logs",
#     ("Method", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/bre/logs",
#     ("Minimal", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/logs",
#     ("Class", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/bre/logs",
#     ("Method", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/bre/logs",
#     ("Minimal", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/bre/logs",
#     ("Class", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/bre/logs",
#     ("Method", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/bre/logs",
#     ("Minimal", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/bre/logs",
# }

# # Surefire per-method failure header. Three formats depending on which surefire
# # provider ran the project and whether the class has one or several @Test
# # methods:
# #   JUnit5/Platform:        [ERROR] de.retest.recheck.BBC162U1Test.test_loginPerformed  Time elapsed: 0.083 s  <<< FAILURE!
# #   JUnit4:                 [ERROR] test_calculateMD5_nullInput(selenium.BBC06U112Test)  Time elapsed: 0.369 s  <<< ERROR!
# #   JUnit4, single-method:  [ERROR] test_serializeAndDeserialize  Time elapsed: 0.007 s  <<< ERROR!
# HEADER_RE = re.compile(
#     r"^\[ERROR\]\s+(?:"
#     r"(?P<fqn1>[\w$]+(?:\.[\w$]+)+)"          # package.Class.method
#     r"|"
#     r"(?P<method2>[\w$]+)\((?P<fqn2>[\w$.]+)\)"  # method(package.Class)
#     r"|"
#     r"(?P<method3>[\w$]+)"                    # bare method, no class qualifier
#     r")\s+Time elapsed:.*?<<<\s*(?P<kind>FAILURE|ERROR)!\s*$",
#     re.MULTILINE,
# )
# BLOCK_END_MARKERS = ("\n[INFO] \n[INFO] Results:", "\n=== STDERR ===")

# # Base set reused, unmodified, from RQ2.2_ErrorTypComp/Exp7ClassErrorTypeComp.py's
# # ErrorExtractor. All four require at least one prefix character before the
# # Exception/Error suffix (e.g. "Runtime" in RuntimeException), so a genuinely
# # unqualified "Exception"/"Error" (no class-name prefix) never matches any of
# # them. That's rare in real stack traces (Java always prints a package-qualified
# # name), but does happen for the "Caused by:" line and for the first line of a
# # thrown-exception block, so the two extra patterns below cover it — anchored
# # to those specific structural positions (not "anywhere in the text") to avoid
# # matching the word "Error"/"Exception" inside ordinary prose/messages.
# EXCEPTION_PATTERNS = [
#     r'(?:^|\s)([\w\.]+(?:Exception|Error))(?:\s|:|\(|$)',
#     r'Caused by:\s+([\w\.]+(?:Exception|Error))',
#     r'^\s*at\s+.*?\(([\w]+(?:Exception|Error))\.java',
#     r'(?:throws|threw|raised)\s+([\w\.]+(?:Exception|Error))',
#     # Bare "Exception"/"Error" (no prefix), only at the two spots a Java stack
#     # trace actually prints a bare class name: right after "Caused by:", or
#     # alone at the start of a line (the top-of-block thrown-exception header).
#     r'Caused by:\s+(Exception|Error)(?:\s|:|\(|$)',
#     r'^\s*(Exception|Error)(?:\s|:|\(|$)',
# ]
# def simple_name(name: str) -> str:
#     return name.split(".")[-1] if "." in name else name


# def extract_exceptions_from_text(text: str) -> Set[str]:
#     """No exception/error class is excluded (Mojo* included) — every error
#     actually thrown is captured. The two checks below are not exclusions of
#     real errors; they're false-positive guards so a test *method* name
#     incidentally matching the regex (e.g. 'test_handlesException') isn't
#     mistaken for a thrown exception."""
#     found = set()
#     for pattern in EXCEPTION_PATTERNS:
#         for match in re.findall(pattern, text, re.MULTILINE):
#             name = simple_name(match.strip())
#             if name.startswith('test_') or name.startswith('test'):
#                 continue
#             if '_' in name and not name.endswith('Exception') and not name.endswith('Error'):
#                 continue
#             if name and ('Exception' in name or 'Error' in name):
#                 found.add(name)
#     return found


# def extract_bump_errors(exception_types_str) -> Set[str]:
#     if not exception_types_str or str(exception_types_str).strip() in ('', 'nan'):
#         return set()
#     errors = set()
#     for e in str(exception_types_str).split('|'):
#         e = e.strip()
#         if not e:
#             continue
#         errors.add(simple_name(e))
#     return errors


# def load_bump_lookup(csv_path: Path) -> dict:
#     df = pd.read_csv(csv_path)
#     lookup = {}
#     for _, row in df.iterrows():
#         lookup[str(row["custom_id"]).strip()] = extract_bump_errors(row.get("exception_types", ""))
#     return lookup


# def per_testcase_results(log_path: Path, test_case_names):
#     """dict: test_case -> (execution_result, {exception_names}).
#     test_case_names not found in the log's failure headers are PASSED."""
#     if not log_path.exists():
#         return {name: ("log_not_found", set()) for name in test_case_names}

#     content = log_path.read_text(encoding="utf-8", errors="ignore")

#     # Container/build was interrupted before Maven ever reached the test phase
#     # (e.g. timeout kill) — distinct from a genuine pass, which "PASSED" would
#     # misleadingly imply.
#     if "Test did not execute" in content:
#         return {name: ("not_executed", set()) for name in test_case_names}

#     headers = list(HEADER_RE.finditer(content))

#     # method name (last dotted segment of the FQN) -> (kind, block_text)
#     by_method = {}
#     for i, h in enumerate(headers):
#         kind = h.group("kind")
#         if h.group("method2"):
#             method = h.group("method2")
#         elif h.group("fqn1"):
#             method = h.group("fqn1").rsplit(".", 1)[-1]
#         else:
#             method = h.group("method3")
#         block_start = h.end()
#         block_end = len(content)
#         if i + 1 < len(headers):
#             block_end = min(block_end, headers[i + 1].start())
#         for marker in BLOCK_END_MARKERS:
#             idx = content.find(marker, block_start)
#             if idx != -1:
#                 block_end = min(block_end, idx)
#         by_method[method] = (kind, content[block_start:block_end])

#     results = {}
#     for name in test_case_names:
#         if name in by_method:
#             kind, block = by_method[name]
#             results[name] = (kind, extract_exceptions_from_text(block))
#         else:
#             results[name] = ("PASSED", set())
#     return results


# def main():
#     oracle_data = json.load(open(ORACLE_JSON, encoding="utf-8"))
#     bump_lookup = load_bump_lookup(BUMP_CSV)

#     rows = []
#     missing_logs = []
#     missing_bump = []

#     for context, models in oracle_data.items():
#         for model, instances in models.items():
#             logs_dir = BRE_LOGS_DIR.get((context, model))
#             for instance, test_files in instances.items():
#                 bump_errors = bump_lookup.get(instance)
#                 if bump_errors is None:
#                     missing_bump.append((context, model, instance))
#                     bump_errors = set()

#                 for test_file, test_cases in test_files.items():
#                     test_case_names = list(test_cases.keys())
#                     log_path = logs_dir / f"{instance}_{test_file}_breaking_single.log" if logs_dir else None

#                     if log_path is None or not log_path.exists():
#                         missing_logs.append((context, model, instance, test_file))

#                     tc_results = per_testcase_results(log_path, test_case_names) if log_path else \
#                         {n: ("log_not_found", set()) for n in test_case_names}

#                     for test_case in test_case_names:
#                         exec_result, llm_errors = tc_results[test_case]
#                         matched = sorted(llm_errors & bump_errors)
#                         missed = sorted(bump_errors - llm_errors)
#                         new = sorted(llm_errors - bump_errors)
#                         rows.append({
#                             "context_variant": context,
#                             "model": model,
#                             "instance": instance,
#                             "test_file": test_file,
#                             "test_case": test_case,
#                             "execution_result": exec_result,
#                             "llm_detected_errors": "|".join(sorted(llm_errors)),
#                             "bump_bc_errors": "|".join(sorted(bump_errors)),
#                             "matched_errors": "|".join(matched),
#                             "missed_errors": "|".join(missed),
#                             "new_errors": "|".join(new),
#                             "match_count": len(matched),
#                             "miss_count": len(missed),
#                             "new_count": len(new),
#                         })

#     rows.sort(key=lambda r: (r["context_variant"], r["model"], r["instance"], r["test_file"], r["test_case"]))

#     OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
#     with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
#         writer = csv.DictWriter(f, fieldnames=[
#             "context_variant", "model", "instance", "test_file", "test_case",
#             "execution_result", "llm_detected_errors", "bump_bc_errors",
#             "matched_errors", "missed_errors", "new_errors",
#             "match_count", "miss_count", "new_count",
#         ])
#         writer.writeheader()
#         writer.writerows(rows)

#     print(f"Wrote {len(rows)} rows -> {OUTPUT_CSV}")
#     if missing_bump:
#         print(f"{len(missing_bump)} (context, model, instance) triples had no BUMP row (0 bump_bc_errors used):")
#         for entry in sorted(set(missing_bump)):
#             print(f"  {entry}")
#     if missing_logs:
#         by_key = {}
#         for context, model, instance, test_file in missing_logs:
#             by_key.setdefault((context, model), 0)
#             by_key[(context, model)] += 1
#         print(f"{len(missing_logs)} (instance, test_file) pairs had no bre log on disk:")
#         for k, n in sorted(by_key.items()):
#             print(f"  {k}: {n}")


# if __name__ == "__main__":
#     main()





import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, Set

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "success_cases"
ORACLE_JSON = OUTPUT_DIR / "oracle_types_detected_success.json"
OUTPUT_CSV = OUTPUT_DIR / "error_type_per_testcase_detected.csv"

BUMP_CSV = PRIMARY_DRIVE / "RQResultsForPaper/RQ2/BUMPErrorLogs/RQ4_resultsBUMP.csv"

BRE_LOGS_DIR = {
    ("Class", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/bre/logs",
    ("Method", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/bre/logs",
    ("Minimal", "GPT4o"): PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/logs",
    ("Class", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/bre/logs",
    ("Method", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/bre/logs",
    ("Minimal", "Qwen3-coder"): PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/bre/logs",
    ("Class", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/bre/logs",
    ("Method", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/bre/logs",
    ("Minimal", "GPTOSS"): PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/bre/logs",
}

# Surefire per-method failure header (three provider formats).
HEADER_RE = re.compile(
    r"^\[ERROR\]\s+(?:"
    r"(?P<fqn1>[\w$]+(?:\.[\w$]+)+)"
    r"|"
    r"(?P<method2>[\w$]+)\((?P<fqn2>[\w$.]+)\)"
    r"|"
    r"(?P<method3>[\w$]+)"
    r")\s+Time elapsed:.*?<<<\s*(?P<kind>FAILURE|ERROR)!\s*$",
    re.MULTILINE,
)
BLOCK_END_MARKERS = ("\n[INFO] \n[INFO] Results:", "\n=== STDERR ===")

# Union extractor (unchanged) — captures EVERY exception-shaped token in a block.
EXCEPTION_PATTERNS = [
    r'(?:^|\s)([\w\.]+(?:Exception|Error))(?:\s|:|\(|$)',
    r'Caused by:\s+([\w\.]+(?:Exception|Error))',
    r'^\s*at\s+.*?\(([\w]+(?:Exception|Error))\.java',
    r'(?:throws|threw|raised)\s+([\w\.]+(?:Exception|Error))',
    r'Caused by:\s+(Exception|Error)(?:\s|:|\(|$)',
    r'^\s*(Exception|Error)(?:\s|:|\(|$)',
]

# --- root-cause extraction (NEW) ---------------------------------------------
# Deliberately does NOT map to any taxonomy. It returns the faithful class name
# and a `source` flag so the notebook can audit *where* the cause came from and
# catch anything unexpected. Bucketing is done downstream, not here.
CAUSED_BY_RE = re.compile(r'Caused by:\s+([\w.$]+(?:Exception|Error))')
TOP_EXC_RE = re.compile(r'^([\w.$]+(?:Exception|Error))(?::|\s|$)', re.MULTILINE)

# Wrappers hold no root information. We peel them to reach the real cause, but if
# only a wrapper is present we KEEP it (never return empty just because the block
# had a wrapper) and flag source="wrapper" so it can be inspected.
WRAPPER_EXCEPTIONS = {
    "InvocationTargetException", "UndeclaredThrowableException",
    "ExecutionException", "CompletionException",
}


def simple_name(name: str) -> str:
    return name.split(".")[-1] if "." in name else name


def extract_exceptions_from_text(text: str) -> Set[str]:
    found = set()
    for pattern in EXCEPTION_PATTERNS:
        for match in re.findall(pattern, text, re.MULTILINE):
            name = simple_name(match.strip())
            if name.startswith('test_') or name.startswith('test'):
                continue
            if '_' in name and not name.endswith('Exception') and not name.endswith('Error'):
                continue
            if name and ('Exception' in name or 'Error' in name):
                found.add(name)
    return found


def extract_root_cause(block: str):
    """(root_cause, source). source in {caused_by, top, wrapper, none}."""
    caused = [simple_name(m) for m in CAUSED_BY_RE.findall(block)]
    for name in reversed(caused):                 # deepest real cause wins
        if name not in WRAPPER_EXCEPTIONS:
            return name, "caused_by"
    tops = [simple_name(m) for m in TOP_EXC_RE.findall(block)]
    for name in tops:
        if name not in WRAPPER_EXCEPTIONS:
            return name, "top"
    if caused:                                    # only wrappers -> keep, flag
        return caused[-1], "wrapper"
    if tops:
        return tops[0], "wrapper"
    return "", "none"                             # ERROR row with no exception = inspect


def extract_bump_errors(exception_types_str) -> Set[str]:
    if not exception_types_str or str(exception_types_str).strip() in ('', 'nan'):
        return set()
    return {simple_name(e.strip()) for e in str(exception_types_str).split('|') if e.strip()}


def load_bump_lookup(csv_path: Path) -> dict:
    df = pd.read_csv(csv_path)
    return {str(r["custom_id"]).strip(): extract_bump_errors(r.get("exception_types", ""))
            for _, r in df.iterrows()}


def per_testcase_results(log_path: Path, test_case_names) -> Dict[str, dict]:
    """test_case -> {execution_result, exceptions, root_cause, root_cause_source,
    reflective}. Names absent from failure headers are PASSED."""
    def _uniform(state):
        return {n: {"execution_result": state, "exceptions": set(),
                    "root_cause": "", "root_cause_source": "none",
                    "reflective": False} for n in test_case_names}

    if not log_path.exists():
        return _uniform("log_not_found")

    content = log_path.read_text(encoding="utf-8", errors="ignore")
    if "Test did not execute" in content:
        return _uniform("not_executed")

    headers = list(HEADER_RE.finditer(content))
    by_method = {}
    for i, h in enumerate(headers):
        kind = h.group("kind")
        if h.group("method2"):
            method = h.group("method2")
        elif h.group("fqn1"):
            method = h.group("fqn1").rsplit(".", 1)[-1]
        else:
            method = h.group("method3")
        block_start = h.end()
        block_end = len(content)
        if i + 1 < len(headers):
            block_end = min(block_end, headers[i + 1].start())
        for marker in BLOCK_END_MARKERS:
            idx = content.find(marker, block_start)
            if idx != -1:
                block_end = min(block_end, idx)
        by_method[method] = (kind, content[block_start:block_end])

    results = {}
    for name in test_case_names:
        if name in by_method:
            kind, block = by_method[name]
            root, src = extract_root_cause(block)
            results[name] = {
                "execution_result": kind,
                "exceptions": extract_exceptions_from_text(block),
                "root_cause": root,
                "root_cause_source": src,
                "reflective": ("InvocationTargetException" in block) or ("Class.forName" in block),
            }
        else:
            results[name] = {"execution_result": "PASSED", "exceptions": set(),
                             "root_cause": "", "root_cause_source": "none",
                             "reflective": False}
    return results


def main():
    oracle_data = json.load(open(ORACLE_JSON, encoding="utf-8"))
    bump_lookup = load_bump_lookup(BUMP_CSV)

    rows, missing_logs, missing_bump = [], [], []

    for context, models in oracle_data.items():
        for model, instances in models.items():
            logs_dir = BRE_LOGS_DIR.get((context, model))
            for instance, test_files in instances.items():
                bump_errors = bump_lookup.get(instance)
                if bump_errors is None:
                    missing_bump.append((context, model, instance))
                    bump_errors = set()

                for test_file, test_cases in test_files.items():
                    names = list(test_cases.keys())
                    log_path = logs_dir / f"{instance}_{test_file}_breaking_single.log" if logs_dir else None
                    if log_path is None or not log_path.exists():
                        missing_logs.append((context, model, instance, test_file))
                    tc = per_testcase_results(log_path, names) if log_path else \
                        {n: {"execution_result": "log_not_found", "exceptions": set(),
                             "root_cause": "", "root_cause_source": "none",
                             "reflective": False} for n in names}

                    for name in names:
                        r = tc[name]
                        llm_errors = r["exceptions"]
                        matched = sorted(llm_errors & bump_errors)
                        rows.append({
                            "context_variant": context, "model": model,
                            "instance": instance, "test_file": test_file, "test_case": name,
                            "execution_result": r["execution_result"],
                            "root_cause": r["root_cause"],
                            "root_cause_source": r["root_cause_source"],
                            "reflective": r["reflective"],
                            "llm_detected_errors": "|".join(sorted(llm_errors)),
                            "bump_bc_errors": "|".join(sorted(bump_errors)),
                            "matched_errors": "|".join(matched),
                            "match_count": len(matched),
                        })

    rows.sort(key=lambda r: (r["context_variant"], r["model"], r["instance"],
                             r["test_file"], r["test_case"]))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader(); w.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {OUTPUT_CSV}")
    if missing_bump:
        print(f"{len(set(missing_bump))} (context,model,instance) had no BUMP row:")
        for e in sorted(set(missing_bump)): print(f"  {e}")


if __name__ == "__main__":
    main()