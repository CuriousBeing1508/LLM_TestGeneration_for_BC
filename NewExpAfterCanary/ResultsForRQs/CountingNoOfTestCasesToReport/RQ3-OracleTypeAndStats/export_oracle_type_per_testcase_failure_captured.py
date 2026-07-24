# """
# Oracle type + predicate per test file / test case, restricted to test cases
# that actually captured a FAILURE — i.e. per-method Surefire log parsing (same
# as export_error_type_per_testcase.py) finds a "<<< FAILURE!"/"<<< ERROR!"
# block for that specific test method, not just "this test file is in the
# detected population" (export_oracle_type_per_testcase.py includes every test
# method of a detected file, whether or not that particular method is the one
# that caught the break).

# Population check: every (context, model, instance, test_file) in the detected
# population (oracle_types_detected_success.json) is expected to contribute at
# least one FAILURE-captured row here, since "detected" means the file's run
# did catch the break somewhere. Files that contribute zero rows are printed at
# the end — a log-parsing gap, not a real 0.

# Source: same oracle_types_detected_success.json as export_oracle_type_per_testcase.py,
# cross-referenced against the same bre/logs used by export_error_type_per_testcase.py
# (per_testcase_results reused unmodified from there).
# """
# import csv
# import json
# import sys
# from pathlib import Path

# sys.path.insert(0, str(Path(__file__).resolve().parent))
# from predicate_extractor import CALL_SEP, format_call
# from export_error_type_per_testcase import BRE_LOGS_DIR, per_testcase_results

# OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "success_cases"
# INPUT_JSON = OUTPUT_DIR / "oracle_types_detected_success.json"
# OUTPUT_CSV = OUTPUT_DIR / "oracle_type_per_testcase_failure_captured.csv"

# FAILURE_KINDS = {"FAILURE", "ERROR"}


# def main():
#     data = json.load(open(INPUT_JSON, encoding="utf-8"))

#     rows = []
#     all_detected_files = set()
#     files_with_failure_row = set()
#     missing_logs = []

#     for context, models in data.items():
#         for model, instances in models.items():
#             logs_dir = BRE_LOGS_DIR.get((context, model))
#             for instance, test_files in instances.items():
#                 for test_file, test_cases in test_files.items():
#                     file_key = (context, model, instance, test_file)
#                     all_detected_files.add(file_key)

#                     test_case_names = list(test_cases.keys())
#                     log_path = logs_dir / f"{instance}_{test_file}_breaking_single.log" if logs_dir else None
#                     if log_path is None or not log_path.exists():
#                         missing_logs.append(file_key)
#                         continue

#                     tc_results = per_testcase_results(log_path, test_case_names)

#                     for test_case, detail in test_cases.items():
#                         exec_result, llm_errors = tc_results[test_case]
#                         if exec_result not in FAILURE_KINDS:
#                             continue

#                         files_with_failure_row.add(file_key)
#                         calls = detail.get("assert_calls", [])
#                         methods_used = sorted({c["method"] for c in calls})
#                         rows.append({
#                             "context_variant": context,
#                             "model": model,
#                             "instance": instance,
#                             "test_file": test_file,
#                             "test_case": test_case,
#                             "execution_result": exec_result,
#                             "llm_detected_errors": "|".join(sorted(llm_errors)),
#                             "num_assert_calls": len(calls),
#                             "oracle_methods_used": "|".join(methods_used),
#                             "has_trycatch_assert": any(c.get("in_trycatch") for c in calls),
#                             "assert_calls": CALL_SEP.join(format_call(c) for c in calls),
#                             "note": detail.get("note", ""),
#                         })

#     rows.sort(key=lambda r: (r["context_variant"], r["model"], r["instance"], r["test_file"], r["test_case"]))

#     OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
#     with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
#         writer = csv.DictWriter(f, fieldnames=[
#             "context_variant", "model", "instance", "test_file", "test_case",
#             "execution_result", "llm_detected_errors",
#             "num_assert_calls", "oracle_methods_used", "has_trycatch_assert",
#             "assert_calls", "note",
#         ])
#         writer.writeheader()
#         writer.writerows(rows)

#     print(f"Wrote {len(rows)} rows -> {OUTPUT_CSV}")
#     print(f"Detected test files: {len(all_detected_files)}; contributed >=1 FAILURE-captured row: {len(files_with_failure_row)}")

#     missing_failure_row = sorted(all_detected_files - files_with_failure_row - set(missing_logs))
#     if missing_failure_row:
#         print(f"{len(missing_failure_row)} detected files had a log on disk but NO test method matched FAILURE/ERROR in it:")
#         for entry in missing_failure_row:
#             print(f"  {entry}")

#     if missing_logs:
#         by_key = {}
#         for context, model, instance, test_file in missing_logs:
#             by_key.setdefault((context, model), 0)
#             by_key[(context, model)] += 1
#         print(f"{len(missing_logs)} detected files had no bre log on disk:")
#         for k, n in sorted(by_key.items()):
#             print(f"  {k}: {n}")


# if __name__ == "__main__":
#     main()




import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicate_extractor import CALL_SEP, format_call
from export_error_type_per_testcase import BRE_LOGS_DIR, per_testcase_results

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "success_cases"
INPUT_JSON = OUTPUT_DIR / "oracle_types_detected_success.json"
OUTPUT_CSV = OUTPUT_DIR / "oracle_type_per_testcase_failure_captured.csv"

FAILURE_KINDS = {"FAILURE", "ERROR"}


def main():
    data = json.load(open(INPUT_JSON, encoding="utf-8"))
    rows, missing_logs = [], []

    for context, models in data.items():
        for model, instances in models.items():
            logs_dir = BRE_LOGS_DIR.get((context, model))
            for instance, test_files in instances.items():
                for test_file, test_cases in test_files.items():
                    names = list(test_cases.keys())
                    log_path = logs_dir / f"{instance}_{test_file}_breaking_single.log" if logs_dir else None
                    if log_path is None or not log_path.exists():
                        missing_logs.append((context, model, instance, test_file)); continue

                    tc = per_testcase_results(log_path, names)
                    for name, detail in test_cases.items():
                        r = tc[name]
                        if r["execution_result"] not in FAILURE_KINDS:
                            continue
                        calls = detail.get("assert_calls", [])
                        rows.append({
                            "context_variant": context, "model": model,
                            "instance": instance, "test_file": test_file, "test_case": name,
                            "execution_result": r["execution_result"],      # FAILURE / ERROR
                            "root_cause": r["root_cause"],                   # faithful class name
                            "root_cause_source": r["root_cause_source"],     # caused_by/top/wrapper/none
                            "reflective": r["reflective"],
                            "llm_detected_errors": "|".join(sorted(r["exceptions"])),  # full union, non-lossy
                            "num_assert_calls": len(calls),
                            "oracle_methods_used": "|".join(sorted({c["method"] for c in calls})),
                            "has_trycatch_assert": any(c.get("in_trycatch") for c in calls),
                            "assert_calls": CALL_SEP.join(format_call(c) for c in calls),
                            "note": detail.get("note", ""),
                        })

    rows.sort(key=lambda r: (r["context_variant"], r["model"], r["instance"],
                             r["test_file"], r["test_case"]))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader(); w.writerows(rows)
    print(f"Wrote {len(rows)} detecting test-case rows -> {OUTPUT_CSV}")
    if missing_logs:
        print(f"{len(missing_logs)} files had no bre log on disk")


if __name__ == "__main__":
    main()