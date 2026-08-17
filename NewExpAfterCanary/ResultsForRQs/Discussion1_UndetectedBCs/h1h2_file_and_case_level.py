"""
h1h2_file_and_case_level.py
=============================
Reports H1/H2 (and H2 root-cause) counts at BOTH test-file and test-case
level, instead of the instance-level counts in hypothesis_analysis_results.csv
/ h1h2_instance_level.pdf. Also cross-checks this population against the
RQ3 and funnel (test_count_aggregate.csv) numbers to make the scope explicit.

Population: this reuses InvestigateUndetected.py's output as-is (no new
Docker/Maven reruns). That script reran every test file that PASSED on the
breaking version (i.e. missed the BC) for instances in
MissedBC/ManualBrokenAPICodingBumpUndetected.csv (BUMP instances where
EVERY generated test missed the BC, across a given model x context) with
`-verbose:class`, and labeled each file:
  H1 = the broken OSS API class never loaded while the test ran
  H2 = it loaded, but the test still passed (weak/missing/misdirected oracle)

IMPORTANT SCOPE NOTE: this only covers missed tests belonging to FULLY
undetected instances (89 BUMP instances x 3 models x 3 contexts, restricted
to the ~111 configs where the whole instance was missed). It does NOT cover
individual missed tests that sit inside an otherwise partially-detected
instance (some tests found the BC, this one didn't) -- those were never
rerun with -verbose:class, so there's no log to classify them from. See the
coverage check this script prints against the funnel totals.

Inputs (read directly from the external drive, not copied into the repo):
  <PRIMARY_DRIVE>/*Results*/Exp*BatchResults*/investigate_undetected/investigation_*.csv
    -- 9 files (3 models x 3 contexts), one row per (custom_id, model,
       context_variant, java_file), from InvestigateUndetected.py
  <PRIMARY_DRIVE>/RQResultsForPaper/RQ3/H2_manual/investigation_H2_*Coded.csv
    -- 36 rows total (20 GPT-4o + 4 GPT-OSS + 12 Qwen), a manually root-caused
       SAMPLE of the 232 H2 files, with a "Root Cause" column in
       {Transitive Dependency, Wrong Target, Weak Oracle}
  <PRIMARY_DRIVE>/RQResultsForPaper/RQ3/MissedBC/ManualBrokenAPICodingBumpUndetected.csv
    -- source population list, used only for a population-completeness check
  ../CountingNoOfTestCasesToReport/output/test_count_aggregate.csv
    -- pipeline funnel (Executed/Detected at file + case level), used for the
       scope/coverage cross-check against RQ2/RQ3
  ../CountingNoOfTestCasesToReport/RQ3-OracleTypeAndStats/output/success_cases/RQ3-table1.csv
    -- detected test file/case counts, used for the same cross-check

Outputs (written next to this script):
  h1h2_file_and_case_level.csv       -- H1 vs H2, file count + case count
  h2_root_cause_file_and_case_level.csv -- H2 root-cause breakdown (the 36-file
                                           sample), file count + case count
"""

import glob
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

BASE_DIR = Path(__file__).resolve().parent
RQ_DIR = BASE_DIR.parent

INVESTIGATION_GLOB = str(
    PRIMARY_DRIVE / "*Results*" / "Exp*BatchResults*" / "investigate_undetected" / "investigation_*.csv"
)
H2_MANUAL_GLOB = str(PRIMARY_DRIVE / "RQResultsForPaper" / "RQ3" / "H2_manual" / "*.csv")
UNDETECTED_CSV = PRIMARY_DRIVE / "RQResultsForPaper" / "RQ3" / "MissedBC" / "ManualBrokenAPICodingBumpUndetected.csv"

FUNNEL_CSV = RQ_DIR / "CountingNoOfTestCasesToReport" / "output" / "test_count_aggregate.csv"
RQ3_TABLE1 = (
    RQ_DIR / "CountingNoOfTestCasesToReport" / "RQ3-OracleTypeAndStats"
    / "output" / "success_cases" / "RQ3-table1.csv"
)

ROOT_CAUSE_ORDER = ["Transitive Dependency", "Wrong Target", "Weak Oracle"]


def load_investigation() -> pd.DataFrame:
    paths = glob.glob(INVESTIGATION_GLOB)
    if not paths:
        raise FileNotFoundError(f"No investigation_*.csv found under {INVESTIGATION_GLOB}")
    inv = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    return inv


def load_root_cause() -> pd.DataFrame:
    paths = sorted(glob.glob(H2_MANUAL_GLOB))
    if not paths:
        raise FileNotFoundError(f"No H2_manual coded CSVs found under {H2_MANUAL_GLOB}")
    rc = pd.concat([pd.read_csv(p, encoding="latin-1") for p in paths], ignore_index=True)
    rc = rc[["custom_id", "model", "context_variant", "java_file", "Root Cause"]]
    return rc.drop_duplicates(subset=["custom_id", "model", "context_variant", "java_file"])


def main():
    inv = load_investigation()
    rc = load_root_cause()

    # Only rows that actually got rerun successfully (excludes timeouts / no result).
    investigated = inv[inv["status"] == "investigated"].copy()
    investigated["tests_run"] = pd.to_numeric(investigated["tests_run"], errors="coerce").fillna(0).astype(int)

    # ── Population completeness check ───────────────────────────────────────
    manual = pd.read_csv(UNDETECTED_CSV)
    source_file_count = int(manual["tests_files_passed_v2"].sum())
    print(f"[check] Source population (fully-undetected-instance missed test files): {source_file_count}")
    print(f"[check] Files present in investigation_*.csv output:                     {len(inv)}")
    print(f"[check]   -> investigated: {len(investigated)}, timeout/no-result: {len(inv) - len(investigated)}")
    if source_file_count != len(inv):
        print(f"[WARNING] Population mismatch: {source_file_count} vs {len(inv)} — investigate before trusting totals.")

    # ── H1 vs H2, file + case level ─────────────────────────────────────────
    h1h2_files = investigated["hypothesis"].value_counts()
    h1h2_cases = investigated.groupby("hypothesis")["tests_run"].sum()

    table_h1h2 = pd.DataFrame({
        "hypothesis": ["H1", "H2"],
        "description": [
            "Broken OSS API class never loaded",
            "Class loaded, test still passed (weak/missing/misdirected oracle)",
        ],
        "test_files": [int(h1h2_files.get(h, 0)) for h in ["H1", "H2"]],
        "test_cases": [int(h1h2_cases.get(h, 0)) for h in ["H1", "H2"]],
    })
    total_row = pd.DataFrame([{
        "hypothesis": "Total",
        "description": "",
        "test_files": int(table_h1h2["test_files"].sum()),
        "test_cases": int(table_h1h2["test_cases"].sum()),
    }])
    table_h1h2 = pd.concat([table_h1h2, total_row], ignore_index=True)

    out_h1h2 = BASE_DIR / "h1h2_file_and_case_level.csv"
    table_h1h2.to_csv(out_h1h2, index=False)
    print(f"\n{table_h1h2.to_string(index=False)}")
    print(f"[saved] {out_h1h2}")

    # ── H2 root cause, file + case level (the 36-file manually coded sample) ──
    merged = investigated.merge(
        rc, on=["custom_id", "model", "context_variant", "java_file"], how="left"
    )
    h2 = merged[merged["hypothesis"] == "H2"]

    rc_files = h2["Root Cause"].value_counts()
    rc_cases = h2.groupby("Root Cause")["tests_run"].sum()

    rows = []
    for label in ROOT_CAUSE_ORDER:
        rows.append({
            "root_cause": label,
            "test_files": int(rc_files.get(label, 0)),
            "test_cases": int(rc_cases.get(label, 0)),
        })
    coded_files = sum(r["test_files"] for r in rows)
    coded_cases = sum(r["test_cases"] for r in rows)
    rows.append({
        "root_cause": "Not yet manually coded",
        "test_files": len(h2) - coded_files,
        "test_cases": int(h2["tests_run"].sum()) - coded_cases,
    })
    rows.append({
        "root_cause": "Total H2",
        "test_files": len(h2),
        "test_cases": int(h2["tests_run"].sum()),
    })
    table_rc = pd.DataFrame(rows)

    out_rc = BASE_DIR / "h2_root_cause_file_and_case_level.csv"
    table_rc.to_csv(out_rc, index=False)
    print(f"\n{table_rc.to_string(index=False)}")
    print(f"[saved] {out_rc}")

    # ── Cross-check against RQ3 / the pipeline funnel ───────────────────────
    print("\n=== Cross-check against RQ3 / funnel numbers ===")
    funnel = pd.read_csv(FUNNEL_CSV)
    for col in ["ExecutedTestFileCount", "ExecutedTestCount", "DetectedTestFileCount", "DetectedBCTestCount"]:
        funnel[col] = funnel[col].replace({"NOT_COMPILED": 0, "NOT_VALID": 0}).astype(int)

    executed_files = int(funnel["ExecutedTestFileCount"].sum())
    executed_cases = int(funnel["ExecutedTestCount"].sum())
    detected_files_funnel = int(funnel["DetectedTestFileCount"].sum())
    detected_cases_funnel = int(funnel["DetectedBCTestCount"].sum())

    rq3 = pd.read_csv(RQ3_TABLE1)
    rq3_total = rq3[rq3["model"] == "Total"].iloc[0]
    detected_files_rq3 = int(rq3_total["test_files"])
    detected_cases_rq3 = int(rq3_total["crash_tests"] + rq3_total["assert_tests"])

    missed_files_funnel = executed_files - detected_files_funnel
    missed_cases_funnel = executed_cases - detected_cases_funnel

    print(f"Funnel (test_count_aggregate.csv): executed(valid) files={executed_files:,} cases={executed_cases:,}")
    print(f"Funnel: detected files={detected_files_funnel:,} cases={detected_cases_funnel:,}")
    print(f"Funnel: missed (executed - detected) files={missed_files_funnel:,} cases={missed_cases_funnel:,}")
    print(f"RQ3-table1.csv: detected files={detected_files_rq3:,} cases={detected_cases_rq3:,} (different snapshot/scope than the funnel)")
    print()
    print(f"This H1/H2 investigation covers {len(inv):,} missed files / {int(investigated['tests_run'].sum()):,} missed cases")
    print(f"  -> {len(inv) / missed_files_funnel:.1%} of all missed files, {int(investigated['tests_run'].sum()) / missed_cases_funnel:.1%} of all missed cases")
    print("  (the rest are missed tests inside PARTIALLY-detected instances, which were never rerun with -verbose:class)")


if __name__ == "__main__":
    main()
