import csv
import json
import re
from pathlib import Path
from collections import defaultdict



## **Key Changes:**

# 1. **Reads CSV file** to get BUMP instance IDs
# 2. **Uses both inputs**: CSV + Log Directory
# 3. **Finds ONE log per instance** using `find_log_for_instance()`
# 4. **Efficient**: Only parses one log file per instance
# 5. **Clear reporting**: Shows multi vs single module counts

# ## **Algorithm Flow:**
# ```
# 1. Read CSV → Get list of BUMP IDs (BBC111, BBC112, ...)
# 2. For each ID:
#    - Find first matching log: BBC111_*.log
#    - Parse log → Check if "multi-module"
#    - If yes → Extract info and add to JSON
#    - If no → Skip (single-module)
# 3. Save JSON with all multi-module instances

# === CONFIG ===
CSV_PATH = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv"
LOG_DIR = Path("/Volumes/Rachna-HD/Exp7BatchResults/bre/logs")
OUTPUT_JSON = Path("/Volumes/Rachna-HD/multi_module_instances.json")

def find_log_for_instance(custom_id: str, log_dir: Path) -> Path:
    """
    Find ONE log file for the given BUMP instance.
    Pattern: {custom_id}_*_breaking_exec.log
    """
    pattern = f"{custom_id}_*_breaking_exec.log"
    matches = list(log_dir.glob(pattern))
    
    if matches:
        return matches[0]  # Return the first match
    return None


def parse_log_file(log_path: Path) -> dict:
    """
    Parse a single log file to extract multi-module information.
    Returns dict with: is_multi_module, first_module, first_module_package, etc.
    """
    content = log_path.read_text(encoding="utf-8", errors="ignore")
    
    info = {
        "is_multi_module": False,
        "first_module": None,
        "first_module_package": None,
        "project_root": None,
        "original_module": None,
        "original_package": None
    }
    
    # Check if multi-module
    project_type_match = re.search(r"Project Type:\s*multi-module\s*\(module:\s*([\w\-]+)\)", content)
    if project_type_match:
        info["is_multi_module"] = True
        info["original_module"] = project_type_match.group(1)
    else:
        # Not multi-module, return early
        return info
    
    # Extract project root
    project_root_match = re.search(r"Project Root:\s*(/[\w\-/]+)", content)
    if project_root_match:
        info["project_root"] = project_root_match.group(1)
    
    # Extract original package
    package_match = re.search(r"Package:\s*([\w\.]+)", content)
    if package_match:
        info["original_package"] = package_match.group(1)
        # Remove .LLMTest suffix if present
        if info["original_package"].endswith(".LLMTest"):
            info["original_package"] = info["original_package"][:-8]
    
    # Detect first module from Maven reactor build order
    first_module = detect_first_module_from_reactor_order(content)
    
    if not first_module:
        # Fallback: use common first module names.. I ended up doing this manually based on logs from pre image. since it is just 18 projects I need to care about.
        common_first_modules = ["core", "common", "base", "api", "model", "utils"]
        for candidate in common_first_modules:
            if info["project_root"] and candidate in info["project_root"].lower():
                first_module = candidate
                break
            if info["original_package"] and candidate in info["original_package"].lower():
                first_module = candidate
                break
        
        # Last resort: use "core"
        if not first_module:
            first_module = "core"
    
    info["first_module"] = first_module
    
    # Infer first module package
    info["first_module_package"] = infer_first_module_package(
        info["project_root"],
        first_module,
        info["original_package"]
    )
    
    return info


def detect_first_module_from_reactor_order(log_content: str) -> str:
    """
    Detect first module from Maven reactor build order.
    Looks for pattern like:
    [INFO] Reactor Build Order:
    [INFO] 
    [INFO] messaging-services                                                 [pom]
    [INFO] core                                                               [jar]
    [INFO] messaging                                                          [jar]
    
    Returns the first [jar] module (skips [pom] parent)
    """
    # Look for reactor build order
    reactor_match = re.search(
        r"Reactor Build Order:.*?\[INFO\]\s+([\w\-]+)\s+\[jar\]",
        log_content,
        re.DOTALL | re.IGNORECASE
    )
    if reactor_match:
        return reactor_match.group(1)
    
    # Alternative: Look for first module being built
    # Pattern: [INFO] Building core 6.1.1                                                [2/8]
    # We want the FIRST one (smallest number)
    building_matches = re.findall(
        r"\[INFO\] Building ([\w\-]+)\s+[\d\.]+\s+\[(\d+)/\d+\]",
        log_content
    )
    if building_matches:
        # Sort by build order number and get first
        building_matches.sort(key=lambda x: int(x[1]))
        # Skip if first is index 1 (usually parent pom), take index 2
        if len(building_matches) > 1 and building_matches[0][1] == "1":
            return building_matches[1][0]
        return building_matches[0][0]
    
    return None


def infer_first_module_package(project_root: str, first_module: str, original_package: str) -> str:
    """
    Infer the first module's package from the original package.
    Examples:
    - de.fraunhofer.ids.messaging.appstore → de.fraunhofer.ids.messaging.core
    - org.example.module1 → org.example.core
    """
    if not original_package:
        return f"com.example.{first_module}"
    
    # Split the package
    parts = original_package.split(".")
    
    # Replace the last component (module name) with first_module
    if len(parts) > 1:
        parts[-1] = first_module
        return ".".join(parts)
    else:
        return f"{original_package}.{first_module}"


def generate_multi_module_json():
    """
    Read CSV file, parse one log per instance, generate multi-module JSON.
    """
    print(f"{'='*80}")
    print(f"Generating Multi-Module Instances JSON")
    print(f"{'='*80}")
    print(f"CSV: {CSV_PATH}")
    print(f"Log Directory: {LOG_DIR}")
    print(f"Output: {OUTPUT_JSON}")
    print(f"{'='*80}\n")
    
    if not LOG_DIR.exists():
        print(f"[ERROR] Log directory not found: {LOG_DIR}")
        return
    
    if not Path(CSV_PATH).exists():
        print(f"[ERROR] CSV file not found: {CSV_PATH}")
        return
    
    multi_module_data = {}
    total_instances = 0
    multi_module_count = 0
    single_module_count = 0
    no_log_count = 0
    
    # Read CSV file
    with open(CSV_PATH, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            custom_id = row["custom_id"].strip()
            total_instances += 1
            
            # Find one log file for this instance
            log_file = find_log_for_instance(custom_id, LOG_DIR)
            
            if not log_file:
                print(f"{custom_id} - [SKIP] No log file found")
                no_log_count += 1
                continue
            
            # Parse the log file
            info = parse_log_file(log_file)
            
            if info["is_multi_module"]:
                print(f"{custom_id} - MULTI-MODULE")
                print(f"  Log: {log_file.name}")
                print(f"  Original Module: {info['original_module']}")
                print(f"  First Module: {info['first_module']}")
                print(f"  First Module Package: {info['first_module_package']}")
                print(f"  Project Root: {info['project_root']}")
                print()
                
                multi_module_data[custom_id] = {
                    "first_module": info["first_module"],
                    "first_module_package": info["first_module_package"]
                }
                multi_module_count += 1
            else:
                print(f"{custom_id} - single-module")
                single_module_count += 1
    
    # Save to JSON
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(multi_module_data, indent=2), encoding="utf-8")
    
    print(f"\n{'='*80}")
    print(f"✓ COMPLETE")
    print(f"{'='*80}")
    print(f"Total instances in CSV: {total_instances}")
    print(f"Multi-module instances: {multi_module_count}")
    print(f"Single-module instances: {single_module_count}")
    print(f"No log file found: {no_log_count}")
    print(f"Output file: {OUTPUT_JSON}")
    print(f"{'='*80}\n")
    
    # Show sample of generated JSON
    if multi_module_data:
        print("Sample of generated JSON (first 5 entries):")
        sample = dict(list(multi_module_data.items())[:5])
        print(json.dumps(sample, indent=2))


if __name__ == "__main__":
    generate_multi_module_json()
