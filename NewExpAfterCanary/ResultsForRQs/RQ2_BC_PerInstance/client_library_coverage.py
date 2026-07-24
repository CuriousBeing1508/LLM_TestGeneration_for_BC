"""
client_library_coverage.py
===========================
For the breaking changes (BCs) detected by each LLM / context-variant config,
reports how many unique client projects and unique libraries (and library
families) are covered.

Inputs (already local, no /Volumes dependency):
  RQ2_data/Stats/bc_detection_per_instance.csv   — per-instance metadata (client, library, ...)
  RQ2_data/Stats/detected_bc_across_models.csv   — long format, one row per (custom_id, model, context_variant)
                                                    that had bc_detected == True

Outputs (written to RQ2_data/Stats/ClientLibraryCoverage/):
  coverage_by_model_context.csv   — unique clients/libraries/categories per (model, context_variant)
  coverage_by_model.csv           — same, aggregated across context variants per model
  coverage_by_context.csv         — same, aggregated across models per context variant
  coverage_overall.csv            — single row, aggregated across every detected BC
  library_category_map.csv        — dependencyArtifactID -> library family, with the
                                     GitHub repo evidence (description/topics) used to assign it
"""

import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_CSV = BASE_DIR / "RQ2_data" / "Stats" / "bc_detection_per_instance.csv"
DETECTED_CSV = BASE_DIR / "RQ2_data" / "Stats" / "detected_bc_across_models.csv"
OUTPUT_DIR = BASE_DIR / "RQ2_data" / "Stats" / "ClientLibraryCoverage"

# dependencyArtifactID -> (library family, evidence).
# Manually verified from Github page and maven.

LIBRARY_CATEGORY = {
    "jackson-databind":             ("Serialization / Data binding", "GitHub desc: 'General data-binding package for Jackson'"),
    "jackson-core":                 ("Serialization / Data binding", "GitHub topics: jackson, json; desc: 'Streaming API'"),
    "jackson-dataformat-cbor":      ("Serialization / Data binding", "GitHub topics: avro, cbor, protobuf, smile (binary serialization formats)"),
    "xstream":                      ("Serialization / Data binding", "GitHub desc: 'Serialize Java objects to XML and back again'"),
    "org.eclipse.persistence.moxy": ("Serialization / Data binding", "Domain knowledge (MOXy = JAXB XML/JSON binding); repo-level topics not informative"),
    "json-smart":                   ("Data structures / Parsing", "Lightweight JSON parser (no repo topics; name/purpose only)"),
    "json":                         ("Data structures / Parsing", "org.json reference JSON data-structure library (no repo topics)"),
    "jsoup":                        ("Data structures / Parsing", "GitHub desc: 'the Java HTML parser'; topics: parser, dom, html"),
    "slf4j-api":                    ("Logging API", "GitHub desc: 'Simple Logging Facade for Java'"),
    "log4j-api":                    ("Logging API", "GitHub desc: 'logging API and backend for Java'; topics: logging, logger"),
    "log4j-core":                   ("Logging API", "GitHub desc: 'logging API and backend for Java'; topics: logging, logger"),
    "google-extensions":            ("Logging API", "GitHub desc: 'A Fluent Logging API for Java' (flogger); topic: logging"),
    "jetty-server":                 ("Web server / Servlet", "GitHub desc: 'Web Container & Clients'; topics: http-server, servlet"),
    "spring-tx":                    ("Application framework", "GitHub topics: framework, spring, spring-framework"),
    "spring-webmvc":                ("Application framework", "GitHub topics: framework, spring, spring-framework"),
    "antlr4-runtime":               ("Parser / Code generation", "GitHub desc: 'parser generator'; topics: parser-generator, parsing, grammar"),
    "mockito-core":                 ("Mocking library", "GitHub desc: 'Most popular Mocking framework for unit tests'; topics: mocking-framework"),
    "plexus-io":                    ("Maven utility / tooling", "GitHub topic: maven; Plexus build-tooling utility"),
    "plexus-utils":                 ("Maven utility / tooling", "GitHub topic: maven; Plexus build-tooling utility"),
    "versions-maven-plugin":        ("Maven utility / tooling", "GitHub desc: 'Versions Maven Plugin'; topics: maven, maven-plugin"),
    "sshd-common":                  ("SSH", "GitHub desc: 'comprehensive Java library for client/server SSH'"),
    "httpclient":                   ("HTTP client", "Apache HttpClient — HTTP client library"),
    "h2":                           ("Database", "GitHub desc: 'embeddable RDBMS'; topics: database, jdbc, sql"),
    "dss-pades-pdfbox":             ("Other", "GitHub desc: 'Digital Signature Service' (PAdES/XAdES/CAdES); no dedicated category"),
}


def load_merged() -> pd.DataFrame:
    inst = pd.read_csv(INSTANCE_CSV)
    det = pd.read_csv(DETECTED_CSV)

    missing = set(det["custom_id"]) - set(inst["custom_id"])
    if missing:
        raise ValueError(f"detected custom_ids missing from instance metadata: {missing}")

    merged = det.merge(
        inst[["custom_id", "clientProject", "clientProjectOrganisation", "dependencyArtifactID"]],
        on="custom_id",
        how="left",
    )

    category_only = {k: v[0] for k, v in LIBRARY_CATEGORY.items()}
    merged["library_category"] = merged["dependencyArtifactID"].map(category_only)
    unmapped = merged.loc[merged["library_category"].isna(), "dependencyArtifactID"].unique()
    if len(unmapped):
        raise ValueError(f"No library_category mapping for: {sorted(unmapped)}")

    return merged


def coverage_stats(df: pd.DataFrame) -> dict:
    return {
        "detected_bcs":            df["custom_id"].nunique(),
        "unique_clients":          df["clientProject"].nunique(),
        "unique_libraries":        df["dependencyArtifactID"].nunique(),
        "unique_library_categories": df["library_category"].nunique(),
        "library_categories":      "|".join(sorted(df["library_category"].unique())),
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    merged = load_merged()

    # ── Per (model, context_variant) ──────────────────────────────────────
    by_model_context = (
        merged.groupby(["model", "context_variant"])
        .apply(lambda g: pd.Series(coverage_stats(g)), include_groups=False)
        .reset_index()
    )
    by_model_context.to_csv(OUTPUT_DIR / "coverage_by_model_context.csv", index=False)

    # ── Per model (all context variants combined) ─────────────────────────
    by_model = (
        merged.groupby("model")
        .apply(lambda g: pd.Series(coverage_stats(g)), include_groups=False)
        .reset_index()
    )
    by_model.to_csv(OUTPUT_DIR / "coverage_by_model.csv", index=False)

    # ── Per context variant (all models combined) ─────────────────────────
    by_context = (
        merged.groupby("context_variant")
        .apply(lambda g: pd.Series(coverage_stats(g)), include_groups=False)
        .reset_index()
    )
    by_context.to_csv(OUTPUT_DIR / "coverage_by_context.csv", index=False)

    # ── Overall (every detected BC, any model/context) ────────────────────
    overall = pd.DataFrame([coverage_stats(merged)])
    overall.to_csv(OUTPUT_DIR / "coverage_overall.csv", index=False)

    # ── Library category map used (for auditing) ──────────────────────────
    cat_map_df = pd.DataFrame(
        [(k, v[0], v[1]) for k, v in sorted(LIBRARY_CATEGORY.items())],
        columns=["dependencyArtifactID", "library_category", "evidence"],
    )
    cat_map_df.to_csv(OUTPUT_DIR / "library_category_map.csv", index=False)

    print("=== Overall ===")
    print(overall.to_string(index=False))
    print("\n=== By model ===")
    print(by_model.to_string(index=False))
    print("\n=== By context ===")
    print(by_context.to_string(index=False))
    print("\n=== By model x context ===")
    print(by_model_context.to_string(index=False))
    print(f"\nWritten to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
