"""
library_scope_table.py
=======================
Generates the "Library categories table" 

Input:
  RQ2_data/Stats/bc_detection_per_instance.csv
      One row per retained instance (89 rows), same custom_id/dependencyArtifactID
      values as the source dataset FinalCandidateBUMPUsed.csv.

Category mapping:
  Reuses LIBRARY_CATEGORY from client_library_coverage.py (the single
  source of truth for dependencyArtifactID -> library_category).The categories are defined in client_library_coverage.py to fix.

Small-category merge:
  Any category whose total instance count is = 1 gets
  folded into a single "Other" row, so the table stays compact.

Outputs (written to RQ2_data/Stats/ClientLibraryCoverage/):
  library_scope_table.csv   — category, example libraries, instance count
"""

import pandas as pd
from pathlib import Path

from client_library_coverage import LIBRARY_CATEGORY

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_CSV = BASE_DIR / "RQ2_data" / "Stats" / "bc_detection_per_instance.csv"
OUTPUT_DIR = BASE_DIR / "RQ2_data" / "Stats" / "ClientLibraryCoverage"

OTHER_THRESHOLD = 3   # categories with total instance count <= this are merged into "Other"
N_EXAMPLES = 2         # example libraries shown per non-"Other" row
ALWAYS_SEPARATE_CATEGORIES = {"Maven utility / tooling"}   # never merged into "Other"


def load_instances() -> pd.DataFrame:
    df = pd.read_csv(INSTANCE_CSV)

    category_only = {k: v[0] for k, v in LIBRARY_CATEGORY.items()}
    df["library_category"] = df["dependencyArtifactID"].map(category_only)

    unmapped = sorted(df.loc[df["library_category"].isna(), "dependencyArtifactID"].unique())
    if unmapped:
        raise ValueError(
            f"No library_category mapping for: {unmapped}. "
            "Add these to LIBRARY_CATEGORY in client_library_coverage.py."
        )
    return df


def build_table(df: pd.DataFrame) -> pd.DataFrame:
    cat_counts = df["library_category"].value_counts()

    small_cats = set(cat_counts[cat_counts <= OTHER_THRESHOLD].index) - ALWAYS_SEPARATE_CATEGORIES

    rows = []
    for category, count in cat_counts.items():
        if category in small_cats:
            continue
        libs = df.loc[df["library_category"] == category, "dependencyArtifactID"]
        examples = libs.value_counts().index[:N_EXAMPLES].tolist()
        rows.append({"library_category": category, "examples": examples, "instances": int(count)})

    if small_cats:
        # One representative (most frequent) library per merged-in category,
        # ordered by that category's instance count, so "Other" still names
        # what it covers rather than just showing its two most common libraries.
        merged_cats_by_count = sorted(small_cats, key=lambda c: cat_counts[c], reverse=True)
        examples = [
            df.loc[df["library_category"] == cat, "dependencyArtifactID"].value_counts().index[0]
            for cat in merged_cats_by_count
        ]
        rows.append({
            "library_category": "Other",
            "examples": examples,
            "instances": int(sum(cat_counts[cat] for cat in small_cats)),
        })

    table = pd.DataFrame(rows).sort_values("instances", ascending=False, ignore_index=True)
    # Keep "Other" as the last row regardless of its count.
    table = pd.concat(
        [table[table["library_category"] != "Other"], table[table["library_category"] == "Other"]],
        ignore_index=True,
    )
    return table


def write_csv(table: pd.DataFrame) -> None:
    out = table.copy()
    out["examples"] = out["examples"].apply(", ".join)
    out.to_csv(OUTPUT_DIR / "library_scope_table.csv", index=False)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_instances()
    table = build_table(df)

    write_csv(table)

    print(table.to_string(index=False))
    print(f"\nTotal instances: {table['instances'].sum()}")
    print(f"Written to: {OUTPUT_DIR / 'library_scope_table.csv'}")
    


if __name__ == "__main__":
    main()
