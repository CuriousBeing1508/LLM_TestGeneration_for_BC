"""
rq2_category_table.py
======================
Builds the data for the RQ2 "breaking update instances detected per library
category, across models and context variants" table (tab:rq2_category).

For every library category, reports:
  - Instances: total number of dataset instances in that category (out of 89)
  - detected count per (model, context_variant): how many of those instances
    were flagged as bc_detected == True by the generated tests

Inputs (same as client_library_coverage.py):
  RQ2_data/Stats/bc_detection_per_instance.csv   — per-instance metadata (client, library, ...)
  RQ2_data/Stats/detected_bc_across_models.csv   — long format, one row per (custom_id, model,
                                                    context_variant) that had bc_detected == True

Output:
  RQ2_data/Stats/CategoryTable/rq2_category_table.csv
    Wide format: one row per library category (plus a Total row), columns are
    Instances and <Model>_<ContextVariant> detected counts, in the same
    column order as the LaTeX table.
"""

import pandas as pd
from pathlib import Path

from client_library_coverage import LIBRARY_CATEGORY

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_CSV = BASE_DIR / "RQ2_data" / "Stats" / "bc_detection_per_instance.csv"
DETECTED_CSV = BASE_DIR / "RQ2_data" / "Stats" / "detected_bc_across_models.csv"
OUTPUT_DIR = BASE_DIR / "RQ2_data" / "Stats" / "CategoryTable"

# Fine-grained categories that get folded into "Other" for the paper table,
# since each has only a single dataset instance (SSH, HTTP client, Database)
# alongside the original "Other" (dss-pades-pdfbox).
OTHER_MERGE = {"SSH", "HTTP client", "Database", "Other"}

# Row order matching the LaTeX table.
CATEGORY_ORDER = [
    "Logging API",
    "Serialization / Data binding",
    "Application framework",
    "Web server / Servlet",
    "Parser / Code generation",
    "Data structures / Parsing",
    "Mocking library",
    "Other",
    "Maven utility / tooling",
]

# Model column order/labels matching the LaTeX table.
MODEL_DISPLAY = {
    "GPT-4o": "GPT-4o",
    "Qwen-480B": "Qwen3-coder",
    "GPTOSS-120b": "GPT-OSS",
}
MODEL_ORDER = ["GPT-4o", "Qwen3-coder", "GPT-OSS"]
CONTEXT_ORDER = ["Minimal", "Method", "Class"]


def table_category(fine_cat: str) -> str:
    return "Other" if fine_cat in OTHER_MERGE else fine_cat


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    inst = pd.read_csv(INSTANCE_CSV)
    det = pd.read_csv(DETECTED_CSV)

    cat_only = {k: v[0] for k, v in LIBRARY_CATEGORY.items()}
    unmapped = set(inst["dependencyArtifactID"]) - set(cat_only)
    if unmapped:
        raise ValueError(f"No library_category mapping for: {sorted(unmapped)}")

    inst["fine_category"] = inst["dependencyArtifactID"].map(cat_only)
    inst["table_category"] = inst["fine_category"].map(table_category)

    # ── Instances per category (dataset scope, Table library-scope) ───────
    instances_per_cat = inst.groupby("table_category")["custom_id"].nunique()

    # ── Detected counts per (category, model, context_variant) ────────────
    merged = det.merge(
        inst[["custom_id", "table_category"]], on="custom_id", how="left"
    )
    merged["model_disp"] = merged["model"].map(MODEL_DISPLAY)
    if merged["model_disp"].isna().any():
        bad = merged.loc[merged["model_disp"].isna(), "model"].unique()
        raise ValueError(f"Unmapped model name(s): {sorted(bad)}")

    detected_counts = (
        merged.groupby(["table_category", "model_disp", "context_variant"])[
            "custom_id"
        ]
        .nunique()
    )

    # ── Assemble wide table ────────────────────────────────────────────────
    rows = []
    for cat in CATEGORY_ORDER:
        row = {
            "Library category": cat,
            "Instances": int(instances_per_cat.get(cat, 0)),
        }
        for model in MODEL_ORDER:
            for ctx in CONTEXT_ORDER:
                col = f"{model}_{ctx}"
                row[col] = int(
                    detected_counts.get((cat, model, ctx), 0)
                )
        rows.append(row)

    table = pd.DataFrame(rows)

    # ── Total row ───────────────────────────────────────────────────────────
    total_row = {"Library category": "Total", "Instances": int(table["Instances"].sum())}
    for model in MODEL_ORDER:
        for ctx in CONTEXT_ORDER:
            col = f"{model}_{ctx}"
            total_row[col] = int(table[col].sum())
    table = pd.concat([table, pd.DataFrame([total_row])], ignore_index=True)

    out_path = OUTPUT_DIR / "rq2_category_table.csv"
    table.to_csv(out_path, index=False)

    print(table.to_string(index=False))
    print(f"\nWritten to: {out_path}")

    # Sanity check: category instance counts should sum to the dataset size.
    total_instances = inst["custom_id"].nunique()
    if int(table.loc[table["Library category"] == "Total", "Instances"].iloc[0]) != total_instances:
        raise AssertionError("Category instance counts do not sum to total dataset size")


if __name__ == "__main__":
    main()
