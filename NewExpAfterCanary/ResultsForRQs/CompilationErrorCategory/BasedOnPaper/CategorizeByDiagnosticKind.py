"""
Categorize compile errors using the DeepDelta diagnostic-kind taxonomy
(Mesbah et al., "DeepDelta: Learning to Repair Compilation Errors",
Table 2, https://doi.org/10.1145/3338906.3340455).

"""

import re
import json
from pathlib import Path
from collections import defaultdict

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE
from GPT4o import MODELS

OUTPUT_DIR = PRIMARY_DRIVE / "RQResultsForPaper/CompilErrorAnalysisResults/DiagnosticKindStats"

ERROR_MSG_RE = re.compile(r"^.*?:\d+: error: (.+)$", re.MULTILINE)

# Order matters — more specific kinds are checked before broader ones.
KIND_PATTERNS = [
    ("already.defined",           re.compile(r"is already defined in|^duplicate class:", re.IGNORECASE)),
    ("does.not.override.abstract", re.compile(r"is not abstract and does not override abstract method", re.IGNORECASE)),
    ("unreported.exception",      re.compile(r"^unreported exception .+?; must be caught or declared to be thrown", re.IGNORECASE)),
    ("inconvertible.types",       re.compile(r"incompatible types", re.IGNORECASE)),
    ("cant.apply.symbols",        re.compile(r"^no suitable (method|constructor) found for", re.IGNORECASE)),
    ("cant.apply.symbol",         re.compile(r"cannot be applied to given types", re.IGNORECASE)),
    ("doesnt.exist",              re.compile(r"^package [\w.]+ does not exist", re.IGNORECASE)),
    ("cant.resolve",              re.compile(r"^cannot find symbol", re.IGNORECASE)),
    ("expected",                  re.compile(r"expected$|^illegal start of|^reached end of file while parsing|^not a statement|^unclosed ", re.IGNORECASE)),
]

KIND_DESCRIPTIONS = {
    "cant.resolve":               "Use of undefined symbol",
    "cant.apply.symbol":          "No method decl. found with matching signature",
    "strict":                     "Incorrectly declared dependencies in Bazel (N/A — Maven/javac builds)",
    "doesnt.exist":               "Use of undefined package",
    "cant.apply.symbols":         "No method decl. found with matching signature (multiple candidates)",
    "expected":                   "Syntax error",
    "inconvertible.types":        "Attempt to cast between inconvertible types",
    "unreported.exception":       "Checked exception, which must be handled",
    "does.not.override.abstract": "Failed to implement inherited abstract method",
    "already.defined":            "Symbol already defined",
    "other":                      "Diagnostic present but not in DeepDelta's top-10 kinds",
    "build.environment":          "Failure with no per-line javac diagnostic (e.g. dependency/classpath resolution)",
    "no_code":                    "LLM output had no parseable Java code — never reached the compiler",
}

# Denominator for every percentage below is ALL failed tests per model
# (including no_code), not just the ones that produced a compile log —
# see error_category in compile_results_pre.json. Conditioning on "reached
# the compiler" would silently compare very different-sized, non-equivalent
# subsets across models (e.g. GPTOSS-120B's failures are 86% no_code, so
# its "reached compiler" set is a small, unrepresentative slice of near-misses).
ALL_KINDS = [
    "no_code",
    "cant.resolve", "cant.apply.symbol", "strict", "doesnt.exist",
    "cant.apply.symbols", "expected", "inconvertible.types",
    "unreported.exception", "does.not.override.abstract", "already.defined",
    "other", "build.environment",
]


def classify_message(msg: str) -> str:
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(msg):
            return kind
    return "other"


def classify_log(log_path: Path):
    """Returns (set_of_kinds_present, list_of_kind_per_instance)."""
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set(), []

    matches = ERROR_MSG_RE.findall(content)
    if not matches:
        return {"build.environment"}, ["build.environment"]

    kinds = [classify_message(m) for m in matches]
    return set(kinds), kinds


def iter_failed_entries(model_tag: str):
    """Yields (variant, error_category, log_path_or_None) for EVERY failed
    test recorded in compile_results_pre.json — including no_code entries,
    which have no log because generation never produced parseable Java and
    the compiler never ran. Sourced straight from the JSON + logs dir,
    independent of the (human-editable) raw extraction CSVs."""
    for variant, cfg in MODELS[model_tag].items():
        with open(cfg["compilation_json"]) as f:
            compile_results = json.load(f).get("compilation_results", {})
        logs_path = Path(cfg["logs_dir"])
        for instance, res in compile_results.items():
            for test_name, info in res.get("failed", {}).items():
                error_category = info.get("error_category")
                if error_category == "no_code":
                    yield variant, error_category, None
                    continue
                log_path = logs_path / f"{instance}_{test_name}_compile.log"
                yield variant, error_category, (log_path if log_path.exists() else None)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # instance_counts[model][kind] = total individual error occurrences
    # file_counts[model][kind]     = number of distinct failing files touched
    # totals[model] = (total_files, total_instances)
    instance_counts = defaultdict(lambda: defaultdict(int))
    file_counts = defaultdict(lambda: defaultdict(int))
    totals = {}

    # also broken out per (model, variant) for a finer-grained table
    instance_counts_v = defaultdict(lambda: defaultdict(int))
    file_counts_v = defaultdict(lambda: defaultdict(int))
    totals_v = {}

    for model_tag in MODELS:
        total_files = 0
        total_instances = 0

        for variant, error_category, log_path in iter_failed_entries(model_tag):
            if log_path is None:
                # no_code (no compile attempt) or a log that vanished on disk
                kind = "no_code" if error_category == "no_code" else "other"
                kinds_present, kind_per_instance = {kind}, [kind]
            else:
                kinds_present, kind_per_instance = classify_log(log_path)

            total_files += 1
            total_instances += len(kind_per_instance)
            totals_v.setdefault((model_tag, variant), [0, 0])
            totals_v[(model_tag, variant)][0] += 1
            totals_v[(model_tag, variant)][1] += len(kind_per_instance)

            for kind in kinds_present:
                file_counts[model_tag][kind] += 1
                file_counts_v[(model_tag, variant)][kind] += 1
            for kind in kind_per_instance:
                instance_counts[model_tag][kind] += 1
                instance_counts_v[(model_tag, variant)][kind] += 1

        totals[model_tag] = (total_files, total_instances)
        print(f"{model_tag}: {total_files} failing files, {total_instances} error instances classified")

    # ---------------------------------------------------------------
    # Cross-model summary table (all variants combined)
    # ---------------------------------------------------------------
    rows = []
    for kind in ALL_KINDS:
        row = {"diagnostic_kind": kind, "description": KIND_DESCRIPTIONS[kind]}
        for model_tag in MODELS:
            total_files, total_instances = totals[model_tag]
            f_cnt = file_counts[model_tag].get(kind, 0)
            i_cnt = instance_counts[model_tag].get(kind, 0)
            row[f"{model_tag}_builds_pct"] = round(f_cnt / max(total_files, 1) * 100, 1)
            row[f"{model_tag}_instances_pct"] = round(i_cnt / max(total_instances, 1) * 100, 1)
            row[f"{model_tag}_builds_n"] = f_cnt
            row[f"{model_tag}_instances_n"] = i_cnt
        rows.append(row)

    summary_df = pd.DataFrame(rows)
    summary_df.to_csv(OUTPUT_DIR / "diagnostic_kind_prevalence_by_model.csv", index=False)

    # ---------------------------------------------------------------
    # Per-variant breakdown
    # ---------------------------------------------------------------
    variant_rows = []
    for model_tag in MODELS:
        for variant in sorted({v for (m, v) in totals_v if m == model_tag}):
            total_files, total_instances = totals_v[(model_tag, variant)]
            for kind in ALL_KINDS:
                f_cnt = file_counts_v[(model_tag, variant)].get(kind, 0)
                i_cnt = instance_counts_v[(model_tag, variant)].get(kind, 0)
                if f_cnt == 0 and i_cnt == 0:
                    continue
                variant_rows.append({
                    "model": model_tag,
                    "variant": variant,
                    "diagnostic_kind": kind,
                    "builds_n": f_cnt,
                    "builds_pct": round(f_cnt / max(total_files, 1) * 100, 1),
                    "instances_n": i_cnt,
                    "instances_pct": round(i_cnt / max(total_instances, 1) * 100, 1),
                })
    variant_df = pd.DataFrame(variant_rows)
    variant_df.to_csv(OUTPUT_DIR / "diagnostic_kind_prevalence_by_model_variant.csv", index=False)

    # ---------------------------------------------------------------
    # Console report
    # ---------------------------------------------------------------
    print("\n=== Diagnostic-kind prevalence by model (all variants combined) ===")
    print("(Builds % = share of failing files where this kind appears at least once;")
    print(" Instances % = share of all individual javac error occurrences)\n")
    for model_tag in MODELS:
        total_files, total_instances = totals[model_tag]
        print(f"  {model_tag}  ({total_files} failing files, {total_instances} error instances)")
        ranked = sorted(ALL_KINDS, key=lambda k: -instance_counts[model_tag].get(k, 0))
        for kind in ranked:
            i_cnt = instance_counts[model_tag].get(kind, 0)
            f_cnt = file_counts[model_tag].get(kind, 0)
            if i_cnt == 0 and f_cnt == 0:
                continue
            i_pct = i_cnt / max(total_instances, 1) * 100
            f_pct = f_cnt / max(total_files, 1) * 100
            print(f"    {kind:28s} instances={i_cnt:6d} ({i_pct:5.1f}%)   "
                  f"builds={f_cnt:6d} ({f_pct:5.1f}%)")
        print()

    print(f"CSV outputs -> {OUTPUT_DIR}")
