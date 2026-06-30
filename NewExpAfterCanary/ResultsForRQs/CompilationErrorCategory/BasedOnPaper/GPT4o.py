"""
Compilation Error Categorizer
- Error taxonomy aligned with DeepDelta (Mesbah et al., ESEC/FSE 2019)
  doi: 10.1145/3338906.3340455
- Supports 3 models × 3 variants → one CSV per model (3 variants as column groups)
- Tracks: total files generated, compiled, failed
- Unmatched errors recorded as 'other' with raw message preserved for review
"""

import re
import json
import csv
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict
import os

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config import PRIMARY_DRIVE


# ---------------------------------------------------------------------------
# Matching is done in explicit ordered passes — no priority numbers.
# Pass 1: Highly specific patterns (test framework, mockito, override)
# Pass 2: Signature / access / type patterns
# Pass 3: DeepDelta core kinds (cant.resolve, doesnt.exist, expected, etc.)
# Pass 4: Catch-all → 'other' (raw message logged for post-hoc review)
#
# DeepDelta top-10 diagnostic kinds (Table 2, Mesbah et al. 2019):
#   cant.resolve          51%  – undefined symbol
#   doesnt.exist           9%  – undefined package
#   expected               9%  – syntax error
#   cant.apply.symbol      8%  – wrong method/ctor signature
#   cant.apply.symbols     3%  – (same root cause, plural form)
#   inconvertible.types    2%  – type incompatibility
#   unreported.exception   1%  – unchecked exception
#   does.not.override.abstract <1%
#   already.defined        1%  – duplicate symbol
#   strict                 6%  – build-tool dependency (Bazel-specific)
# ---------------------------------------------------------------------------

_RAW_PATTERNS = [

    # ── PASS 1: Specific / unambiguous patterns ────────────────────────────

    ("test.framework.annotation",
     "cant.resolve", "Test Framework",
     r"cannot find symbol\s+symbol:\s+class\s+Test\b"),

    ("test.framework.assertion",
     "cant.resolve", "Test Framework",
     r"cannot find symbol\s+symbol:\s+method\s+"
     r"(assert\w+|assertEquals|assertTrue|assertFalse|assertNotNull|assertThat)\b"),

    ("test.framework.package",
     "doesnt.exist", "Test Framework",
     r"package (org\.junit|junit|org\.testng|org\.hamcrest|org\.assertj) does not exist"),

    ("mocking.dependency",
     "doesnt.exist", "Mocking/Dependency",
     r"package org\.mockito|package org\.easymock"
     r"|import .*[Mm]ock.*does not exist"),

    ("does.not.override.abstract",
     "does.not.override.abstract", "Override/Implementation",
     r"is not abstract and does not override abstract method"
     r"|method does not override or implement a method from a supertype"
     r"|cannot override .{0,120} attempting to use incompatible return type"
     r"|in .{0,120} cannot override .{0,120} in .{0,120}"),

    ("static.import",
     "doesnt.exist", "Import/Package",
     r"static import only from classes and interfaces"),

    # ── PASS 2: Signature, access, type, exception ─────────────────────────

    ("cant.apply.symbol",
     "cant.apply.symbol", "Signature Mismatch",
     r"method .* cannot be applied to given types"
     r"|no suitable method found for"
     r"|no suitable constructor found"
     r"|constructor .* cannot be applied"),

    ("access.not.public",
     "access.violation", "Access Violation",
     r"is not public in .*?; cannot be accessed from outside package"),

    ("access.protected",
     "access.violation", "Access Violation",
     r"has protected access in"),

    ("access.private",
     "access.violation", "Access Violation",
     r"has private access in"),

    ("access.abstract.instantiation",
     "access.violation", "Access Violation",
     r"is abstract; cannot be instantiated"),

    ("access.cannot.access",
     "access.violation", "Access Violation",
     r"cannot access \w+"),

    ("cannot.inherit.final",
     "access.violation", "Inheritance Error",
     r"cannot inherit from final"),

    ("cannot.assign.final",
     "access.violation", "Final Variable Error",
     r"cannot assign a value to final variable"),

    ("inconvertible.types",
     "inconvertible.types", "Type Error",
     r"incompatible types|requires type argument"),

    ("unreported.exception",
     "unreported.exception", "Exception Handling",
     r"unreported exception [\w.]+; must be caught or declared"
     r"|exception .{0,80} is never thrown in body of corresponding try statement"),

    ("static.context",
     "cant.resolve", "Static Context Error",
     r"non-static .* cannot be referenced from a? static context"),

    ("ambiguous.reference",
     "cant.resolve", "Ambiguous Reference",
     r"reference to .* is ambiguous"),

    ("name.clash",
     "already.defined", "Duplicate Definition",
     r"name clash:"),

    ("already.defined",
     "already.defined", "Duplicate Definition",
     r"variable .*? is already defined|is already defined in"),

    ("enclosing.instance",
     "cant.resolve", "Inner Class Error",
     r"an enclosing instance that contains .* is required"),

    # ── PASS 3: DeepDelta core kinds ───────────────────────────────────────

    ("cant.resolve.class",
     "cant.resolve", "Symbol Not Found",
     r"cannot find symbol\s+symbol:\s+class\s+\w+"),

    ("cant.resolve.method",
     "cant.resolve", "Symbol Not Found",
     r"cannot find symbol\s+symbol:\s+method\s+\w+"),

    ("cant.resolve.variable",
     "cant.resolve", "Symbol Not Found",
     r"cannot find symbol\s+symbol:\s+variable\s+\w+"),

    ("cant.resolve.package",
     "cant.resolve", "Symbol Not Found",
     r"cannot find symbol\s+symbol:\s+package\s+\w+"),

    ("doesnt.exist",
     "doesnt.exist", "Import/Package",
     r"package [\w.]+ does not exist"),

    ("expected.class",
     "expected", "Syntax Error",
     r"class, interface, or enum expected"),

    ("expected.interface",
     "expected", "Syntax Error",
     r"interface expected here|no interface expected here"),

    ("expected.eof",
     "expected", "Syntax Error",
     r"reached end of file while parsing"),

    ("expected.unreachable",
     "expected", "Syntax Error",
     r"unreachable statement"),

    ("expected.illegal",
     "expected", "Syntax Error",
     r"illegal character|illegal start of expression"),

    ("expected.delimiter",
     "expected", "Syntax Error",
     r"['\"]?[;)\]}{(]['\"]? expected"),

    # ── PASS 4: catch-all – populated at runtime ───────────────────────────
    # category = "other", deepdelta_kind = "other", raw message stored
]

# Compile regexes once at import time — re.MULTILINE so ^ matches line starts,
# re.IGNORECASE for case-insensitive matching
PATTERNS: List[Tuple[str, str, str, re.Pattern]] = [
    (key, dk, main, re.compile(pat, re.IGNORECASE | re.MULTILINE))
    for key, dk, main, pat in _RAW_PATTERNS
]

# Lookup tables
_CAT_META: Dict[str, Tuple[str, str]] = {
    key: (dk, main) for key, dk, main, _ in _RAW_PATTERNS
}
_CAT_META["other"] = ("other", "Other")


def get_deepdelta_kind(category: str) -> str:
    return _CAT_META.get(category, ("other", "Other"))[0]


def get_main_category(category: str) -> str:
    return _CAT_META.get(category, ("other", "Other"))[1]


# ---------------------------------------------------------------------------
class CompilationErrorCategorizer:

    def parse_log_file(self, log_path: Path) -> Dict:
        result = {
            "instance": "",
            "test_name": "",
            "log_path": str(log_path),
            "error_counts": defaultdict(int),  # 1 per matched category (presence)
            "other_messages": [],
            "total_errors": 0,                 # count of error: lines in log
            "primary_category": "",
            "primary_deepdelta_kind": "",
            "unique_categories": set(),
        }

        # Filename: BBC01_BBC01U2Test.java_compile.log
        stem = log_path.name.replace("_compile.log", "")
        parts = stem.split("_", 1)
        if len(parts) == 2:
            result["instance"], result["test_name"] = parts

        try:
            log_content = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            result["error_counts"]["READ_ERROR"] += 1
            return result

        # Count raw error lines for total_errors
        result["total_errors"] = len(re.findall(r"^.*?:\d+: error:", log_content, re.MULTILINE))

        # Check each pattern once against full log — record presence (1 per category)
        matched_any = False
        for key, dk, main, regex in PATTERNS:
            if regex.search(log_content):
                result["error_counts"][key] = 1
                result["unique_categories"].add(key)
                matched_any = True

        if not matched_any and result["total_errors"] > 0:
            # Has error lines but no pattern matched — capture first error line
            first = re.search(r"^.*?:\d+: error: (.+)$", log_content, re.MULTILINE)
            result["error_counts"]["other"] = 1
            result["unique_categories"].add("other")
            result["other_messages"].append(first.group(0).strip() if first else "unknown")

        if result["error_counts"]:
            result["primary_category"] = max(result["error_counts"], key=result["error_counts"].get)
            result["primary_deepdelta_kind"] = get_deepdelta_kind(result["primary_category"])

        result["unique_categories"] = list(result["unique_categories"])
        return result


# ============================================================
# Multi-model / multi-variant orchestration
# ============================================================

def process_variant(
    logs_dir: str,
    compilation_json: str,
    variant_tag: str,
    categorizer: CompilationErrorCategorizer,
) -> Tuple[List[Dict], Dict]:

    data = load_compilation_json(compilation_json)
    compile_results = data.get("compilation_results", {})

    total_generated = total_compiled = total_failed = 0
    failed_tests = []

    for instance, res in compile_results.items():
        file_counts      = res.get("file_counts", {})
        total_generated += file_counts.get("files_generated", 0)
        total_compiled  += file_counts.get("files_compiled", 0)
        for test_name in res.get("failed", {}):
            total_failed += 1
            failed_tests.append({"instance": instance, "test_name": test_name})

    print(f"  [{variant_tag}] generated={total_generated}  "
          f"compiled={total_compiled}  failed={total_failed}")

    logs_path = Path(logs_dir)
    all_results = []
    missing_logs = 0

    for ft in failed_tests:
        # test_name already includes .java, e.g. BBC01U2Test.java
        log_path = logs_path / f"{ft['instance']}_{ft['test_name']}_compile.log"
        if not log_path.exists():
            missing_logs += 1
            continue
        r = categorizer.parse_log_file(log_path)
        r["variant"] = variant_tag
        all_results.append(r)

    if missing_logs:
        print(f"    NOTE: {missing_logs} failed tests had no compile log "
              f"(process likely crashed before writing — excluded from error analysis)")

    summary = _build_summary(
        all_results, total_generated, total_compiled, total_failed, variant_tag
    )
    return all_results, summary


def load_compilation_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _build_summary(
    results: List[Dict],
    total_generated: int,
    total_compiled: int,
    total_failed: int,
    variant_tag: str,
) -> Dict:

    cat_dist     = defaultdict(int)
    file_cat     = defaultdict(int)
    primary_dist = defaultdict(int)
    main_cat_files = defaultdict(set)
    other_msgs   = []

    for r in results:
        key = f"{r['instance']}_{r['test_name']}"
        for cat, cnt in r["error_counts"].items():
            cat_dist[cat] += cnt
        for cat in r["unique_categories"]:
            file_cat[cat] += 1
            main_cat_files[get_main_category(cat)].add(key)
        if r["primary_category"]:
            primary_dist[r["primary_category"]] += 1
        if "other" in r["unique_categories"]:
            other_msgs.extend(r.get("other_messages", []))

    total_errs = sum(cat_dist.values())

    dk_dist = defaultdict(int)
    for cat, cnt in cat_dist.items():
        dk_dist[get_deepdelta_kind(cat)] += cnt

    return {
        "variant": variant_tag,
        "total_generated": total_generated,
        "total_compiled": total_compiled,
        "total_failed": total_failed,
        "compilation_rate_pct": round(total_compiled / max(total_generated, 1) * 100, 1),
        "total_errors_in_failed": total_errs,
        "other_error_count": cat_dist.get("other", 0),
        "other_error_messages": list(set(other_msgs)),
        "category_distribution": dict(sorted(cat_dist.items(), key=lambda x: -x[1])),
        "deepdelta_kind_distribution": dict(sorted(dk_dist.items(), key=lambda x: -x[1])),
        "files_with_category": dict(sorted(file_cat.items(), key=lambda x: -x[1])),
        "main_category_files": {
            k: len(v)
            for k, v in sorted(main_cat_files.items(), key=lambda x: -len(x[1]))
        },
        "primary_category_distribution": dict(sorted(primary_dist.items(), key=lambda x: -x[1])),
    }


# ============================================================
# CSV writer
# ============================================================

def write_model_csv(
    model_tag: str,
    variants_data: Dict[str, Tuple[List[Dict], Dict]],
    output_path: str,
):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Fixed columns + one column per error category
    all_cats = sorted({
        cat
        for results, _ in variants_data.values()
        for r in results
        for cat in r["error_counts"]
    })

    fieldnames = (
        ["context_variant", "instance", "test_file",
         "total_errors", "primary_category", "primary_deepdelta_kind",
         "other_error_line"]
        + all_cats
    )

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for vt, (results, smry) in variants_data.items():
            for r in results:
                row = {
                    "context_variant":        vt,
                    "instance":               r["instance"],
                    "test_file":              r["test_name"],
                    "total_errors":           r["total_errors"],
                    "primary_category":       r["primary_category"],
                    "primary_deepdelta_kind": r.get("primary_deepdelta_kind", ""),
                    "other_error_line":       "; ".join(r.get("other_messages", [])),
                }
                for cat in all_cats:
                    row[cat] = r["error_counts"].get(cat, 0)
                w.writerow(row)

        # ── Summary block ────────────────────────────────────────────────
        w.writerow({})
        w.writerow({"context_variant": f"=== SUMMARY: {model_tag} ==="})

        for vt, (_, smry) in variants_data.items():
            w.writerow({
                "context_variant": vt,
                "instance": (
                    f"generated={smry['total_generated']}  "
                    f"compiled={smry['total_compiled']}  "
                    f"failed={smry['total_failed']}  "
                    f"compile_rate={smry['compilation_rate_pct']}%  "
                    f"unmatched={smry['other_error_count']}"
                ),
            })

        w.writerow({})
        w.writerow({"context_variant": "DeepDelta diagnostic kind distribution"})
        for vt, (_, smry) in variants_data.items():
            for dk, cnt in smry["deepdelta_kind_distribution"].items():
                pct = round(cnt / max(smry["total_errors_in_failed"], 1) * 100, 1)
                w.writerow({
                    "context_variant": vt,
                    "instance": dk,
                    "total_errors": cnt,
                    "primary_category": f"{pct}%",
                })

        w.writerow({})
        w.writerow({"context_variant": "Primary category per failing file"})
        for vt, (_, smry) in variants_data.items():
            for cat, cnt in smry["primary_category_distribution"].items():
                pct = round(cnt / max(smry["total_failed"], 1) * 100, 1)
                w.writerow({
                    "context_variant": vt,
                    "instance": cat,
                    "total_errors": cnt,
                    "primary_category": f"{pct}%",
                })

        w.writerow({})
        w.writerow({"context_variant": "Unmatched 'other' error lines"})
        for vt, (_, smry) in variants_data.items():
            for msg in smry.get("other_error_messages", []):
                w.writerow({"context_variant": vt, "instance": msg})

    print(f"  ✓ CSV → {output_path}")


# ============================================================
# Console summary
# ============================================================

def print_model_summary(model_tag: str, variants_data: Dict):
    SEP = "=" * 70
    print(f"\n{SEP}\nMODEL: {model_tag}\n{SEP}")
    for vt, (_, smry) in variants_data.items():
        print(f"\n  Variant : {vt}")
        print(f"  Generated : {smry['total_generated']}")
        print(f"  Compiled  : {smry['total_compiled']}  ({smry['compilation_rate_pct']}%)")
        print(f"  Failed    : {smry['total_failed']}")
        print(f"  Total errors (in failed files): {smry['total_errors_in_failed']}")
        print(f"  Unmatched ('other') errors    : {smry['other_error_count']}")
        print("  DeepDelta diagnostic kinds:")
        for dk, cnt in smry["deepdelta_kind_distribution"].items():
            pct = cnt / max(smry["total_errors_in_failed"], 1) * 100
            print(f"    {dk:40s}: {cnt:5d}  ({pct:5.1f}%)")
        print("  Primary category per failing file (top 10):")
        for cat, cnt in list(smry["primary_category_distribution"].items())[:10]:
            pct = cnt / max(smry["total_failed"], 1) * 100
            print(f"    {cat:40s}: {cnt:4d}  ({pct:5.1f}%)")
        if smry.get("other_error_messages"):
            print("  Sample unmatched messages (extend PATTERNS if frequent):")
            for msg in smry["other_error_messages"][:5]:
                print(f"    → {msg}")


# ============================================================
# Config
# ============================================================

MODELS = {
    "GPT4o": {
        "Class": {
            "logs_dir":         PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/compile_results_pre.json",
        },
        "Method": {
            "logs_dir":         PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTResults/Exp6BatchResults/pre/compile_results_pre.json",
        },
        "Minimal": {
            "logs_dir":         PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/compile_results_pre.json",
        },
    },
    "Qwen-480B": {
        "Class": {
            "logs_dir":         PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp7BatchResults/pre/compile_results_pre.json",
        },
        "Method": {
            "logs_dir":         PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp6BatchResults/pre/compile_results_pre.json",
        },
        "Minimal": {
            "logs_dir":         PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/compile_results_pre.json",
        },
    },
    "GPTOSS-120B": {
        "Class": {
            "logs_dir":         PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp7BatchResults/pre/compile_results_pre.json",
        },
        "Method": {
            "logs_dir":         PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp6BatchResults/pre/compile_results_pre.json",
        },
        "Minimal": {
            "logs_dir":         PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/logs",
            "compilation_json": PRIMARY_DRIVE / "GPTOSSResults/Exp3BatchResults/pre/compile_results_pre.json",
        },
    },
}

OUTPUT_DIR = PRIMARY_DRIVE / "RQResultsForPaper/CompilErrorAnalysisResults"


if __name__ == "__main__":
    categorizer = CompilationErrorCategorizer()

    for model_tag, variants_cfg in MODELS.items():
        print(f"\nProcessing model: {model_tag}")
        variants_data = {}

        for vt, cfg in variants_cfg.items():
            print(f"  Variant: {vt}")
            results, summary = process_variant(
                logs_dir=cfg["logs_dir"],
                compilation_json=cfg["compilation_json"],
                variant_tag=vt,
                categorizer=categorizer,
            )
            variants_data[vt] = (results, summary)

        out_csv = os.path.join(OUTPUT_DIR, f"compilation_errors_{model_tag}.csv")
        write_model_csv(model_tag, variants_data, out_csv)
        print_model_summary(model_tag, variants_data)

    print(f"\n✓ Done. Outputs in: {OUTPUT_DIR}")