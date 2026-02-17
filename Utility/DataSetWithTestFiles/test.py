import json

JSON_PATH = "/Volumes/Rachna-HD/GPTResults/Exp3BatchResults/pre/compile_results_pre.json"

with open(JSON_PATH, "r") as f:
    data = json.load(f)

# ── Summary section ──────────────────────────────────────
print("=== SUMMARY ===")
print(json.dumps(data.get("summary", {}), indent=2))

# ── file_counts total ────────────────────────────────────
print("\n=== FILE_COUNTS TOTALS ===")
total_generated = sum(v.get("files_generated", 0) for v in data.get("file_counts", {}).values())
total_compiled  = sum(v.get("files_compiled",  0) for v in data.get("file_counts", {}).values())
print(f"Sum of files_generated across all BBC: {total_generated}")
print(f"Sum of files_compiled  across all BBC: {total_compiled}")

# ── Actual entries in compilation_results ────────────────
print("\n=== COMPILATION_RESULTS ACTUAL COUNT ===")
actual_compiled = sum(
    len(v.get("compiled", []))
    for v in data.get("compilation_results", {}).values()
)
print(f"Total filenames listed under 'compiled': {actual_compiled}")

# ── Per-instance breakdown where file_counts vs compiled differ ──
print("\n=== MISMATCHES (file_counts.files_compiled != len(compiled)) ===")
fc  = data.get("file_counts", {})
cr  = data.get("compilation_results", {})
for bbc in sorted(fc.keys()):
    count_in_fc = fc[bbc].get("files_compiled", 0)
    count_in_cr = len(cr.get(bbc, {}).get("compiled", []))
    if count_in_fc != count_in_cr:
        print(f"  {bbc}: file_counts={count_in_fc}, len(compiled)={count_in_cr}, diff={count_in_fc - count_in_cr}")