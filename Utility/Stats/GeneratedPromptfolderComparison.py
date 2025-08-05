from pathlib import Path

# === Configuration ===
dir_A = Path("/Volumes/Rachna-HD/Dataset/GeneratedPromptLibrary")
dir_B = Path("/Volumes/Rachna-HD/Dataset/GeneratedPromptClient")
output_file = Path("/Volumes/Rachna-HD/Dataset/folder_comparison_report.txt")  # Change if needed

# === Collect first-level folders ===
folders_A = {f.name for f in dir_A.iterdir() if f.is_dir()}
folders_B = {f.name for f in dir_B.iterdir() if f.is_dir()}

# === Compare ===
common = folders_A & folders_B
only_in_A = folders_A - folders_B
only_in_B = folders_B - folders_A

# === Prepare output ===
lines = [
    f" Folders in A ({dir_A}): {len(folders_A)}",
    f" Folders in B ({dir_B}): {len(folders_B)}",
    "",
    f" Common folders ({len(common)}):",
    *sorted(common),
    "",
    f" Found in A but not in B ({len(only_in_A)}):",
    *sorted(only_in_A),
    "",
    f" Found in B but not in A ({len(only_in_B)}):",
    *sorted(only_in_B),
]

# === Write to file ===
output_file.write_text("\n".join(lines), encoding="utf-8")
print(f" Report saved to: {output_file.resolve()}")
