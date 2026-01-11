import json
from pathlib import Path
import shutil
import os
#  This step is very important to ecexute before executing maven tests. 
# Paths for poc
# PROJECT_ROOT = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ExecutableProjects_Client")
# SKIP_LOG_JSON = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ResultClientExecution/step2_test_compile_results_prev.json")
# SKIPPED_DIR = Path("/Users/rachnaraj/Documents/Research/Poc/Dataset/ResultClientExecution/skipped_classes")

# Path Exp 1
PROJECT_ROOT = Path("/Volumes/Rachna-HD/Dataset/Exp1/ExecutableProjects_Client")
SKIP_LOG_JSON = Path("/Volumes/Rachna-HD/Dataset/Exp1/ResultClientExecutionPrev/step2_test_compile_results_prev.json")
SKIPPED_DIR = Path("/Volumes/Rachna-HD/Dataset/Exp1/ResultClientExecution/skipped_classes")

# Ensure output directory exists
SKIPPED_DIR.mkdir(parents=True, exist_ok=True)

# Load skipped class data
with open(SKIP_LOG_JSON, "r", encoding="utf-8") as f:
    skipped_data = json.load(f)

moved = []

for bump_id, details in skipped_data.items():
    project_name = bump_id.replace("_prev", "")
    base_project_dir = PROJECT_ROOT / project_name / bump_id
    java_src_dir = base_project_dir / "src/test/java"

    for rel_path in details.get("skipped_test_classes", []):
        src_file = java_src_dir / rel_path
        dest_file = SKIPPED_DIR / project_name / rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        if src_file.exists():
            shutil.move(str(src_file), str(dest_file))
            print(f"Moved: {src_file} → {dest_file}")
            moved.append(str(dest_file))
            
            # Check if the source file still exists before deleting it
            if src_file.exists():
                os.remove(str(src_file))
                print(f"🗑️  Deleted: {src_file}")
            else:
                print(f" File not found for deletion: {src_file}")
        else:
            print(f"File not found: {src_file}")

print(f"\n Moved and deleted {len(moved)} skipped Java test classes from: {PROJECT_ROOT} to: {SKIPPED_DIR}")
