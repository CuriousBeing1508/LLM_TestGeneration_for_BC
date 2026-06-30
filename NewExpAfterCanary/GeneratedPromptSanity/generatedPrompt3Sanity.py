import os
import csv
from pathlib import Path
from collections import defaultdict
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import SECONDARY_DRIVE

# === CONFIGURATION ===
CSV_PATH = SECONDARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"
PROMPT_DIR = SECONDARY_DRIVE / "FilteredDataset/Exp3Prompts"

# Output report file
REPORT_FILE = SECONDARY_DRIVE / "SanityReport/Exp3_GenPrompt_report.txt"

# Define all output folders to check
# Format: {folder_name: folder_path}
OUTPUT_FOLDERS = {
    "GPT4o_Exp3": SECONDARY_DRIVE / "FilteredDataset/Exp3LLMOutput/GPT4o",
    "Qwen_Exp3": SECONDARY_DRIVE / "FilteredDataset/Exp3LLMOutput/Qwen_480b_cloud",
    # Add more output folders as needed
}

# Alternatively, auto-discover all output folders (uncomment to use)
# OUTPUT_FOLDERS = {}
# for exp_root in [SECONDARY_DRIVE / "FilteredDataset/Exp6LLMOutput",
#                  SECONDARY_DRIVE / "FilteredDataset/Exp7LLMOutput"]:
#     if exp_root.exists():
#         for model_folder in exp_root.iterdir():
#             if model_folder.is_dir():
#                 folder_name = f"{model_folder.name}_{exp_root.name.split('LLMOutput')[0]}"
#                 OUTPUT_FOLDERS[folder_name] = model_folder

# === LOGGER CLASS ===
class ReportLogger:
    """Dual output: console and file"""
    def __init__(self, filepath):
        self.terminal = __import__('sys').stdout
        self.log_file = open(filepath, 'w', encoding='utf-8')
        
    def write(self, message):
        self.terminal.write(message)
        self.log_file.write(message)
    
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()
    
    def close(self):
        self.log_file.close()

# === CSV LOADER ===
def load_bumps_from_csv(csv_path: Path):
    """Load all bump instance IDs from CSV."""
    bump_ids = []
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found at {csv_path}")
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("custom_id") or "").strip()
            if cid:
                bump_ids.append(cid)
    return bump_ids

# === GET EXPECTED PROMPTS ===
def get_expected_prompts(prompt_dir: Path, bump_id: str):
    """Get list of expected .txt files for a bump instance."""
    bump_folder = prompt_dir / bump_id
    if not bump_folder.exists():
        return []
    return sorted([f.name for f in bump_folder.glob("*.txt")])

# === ANALYZE OUTPUT FOLDER ===
def analyze_output_folder(output_folder: Path, prompt_dir: Path, bump_ids: list):
    """Analyze a single model output folder."""
    results = {
        'total_files': 0,
        'by_instance': {},
        'missing': defaultdict(list),
        'extra': defaultdict(list)
    }
    
    for bump_id in bump_ids:
        expected_prompts = get_expected_prompts(prompt_dir, bump_id)
        instance_folder = output_folder / bump_id
        
        if instance_folder.exists():
            actual_files = sorted([f.name for f in instance_folder.glob("*.txt")])
        else:
            actual_files = []
        
        # Count files
        file_count = len(actual_files)
        results['total_files'] += file_count
        results['by_instance'][bump_id] = {
            'expected': len(expected_prompts),
            'actual': file_count,
            'files': actual_files
        }
        
        # Find missing files
        expected_set = set(expected_prompts)
        actual_set = set(actual_files)
        missing = expected_set - actual_set
        extra = actual_set - expected_set
        
        if missing:
            results['missing'][bump_id] = sorted(missing)
        if extra:
            results['extra'][bump_id] = sorted(extra)
    
    return results

# === PRINT REPORT ===
def print_report(folder_name: str, results: dict, bump_ids: list):
    """Print detailed report for an output folder."""
    print(f"\n{'='*80}")
    print(f"OUTPUT FOLDER: {folder_name}")
    print(f"{'='*80}")
    print(f"Total .txt files generated: {results['total_files']}\n")
    
    # Summary by instance
    print(f"{'Instance ID':<30} {'Expected':<12} {'Actual':<12} {'Status'}")
    print("-" * 80)
    for bump_id in bump_ids:
        info = results['by_instance'].get(bump_id, {'expected': 0, 'actual': 0})
        expected = info['expected']
        actual = info['actual']
        
        if actual == 0 and expected > 0:
            status = "❌ MISSING ALL"
        elif actual < expected:
            status = f"⚠️  INCOMPLETE ({actual}/{expected})"
        elif actual > expected:
            status = f"⚠️  EXTRA FILES ({actual}/{expected})"
        else:
            status = "✅ COMPLETE"
        
        print(f"{bump_id:<30} {expected:<12} {actual:<12} {status}")
    
    # Detailed missing files
    if results['missing']:
        print(f"\n{'='*80}")
        print(f"MISSING FILES DETAILS")
        print(f"{'='*80}")
        for bump_id, missing_files in sorted(results['missing'].items()):
            print(f"\n{bump_id}:")
            for f in missing_files:
                print(f"  - {f}")
    
    # Extra files (shouldn't normally happen)
    if results['extra']:
        print(f"\n{'='*80}")
        print(f"EXTRA/UNEXPECTED FILES")
        print(f"{'='*80}")
        for bump_id, extra_files in sorted(results['extra'].items()):
            print(f"\n{bump_id}:")
            for f in extra_files:
                print(f"  - {f}")

# === CROSS-MODEL COMPARISON ===
def print_cross_folder_summary(all_results: dict, bump_ids: list):
    """Print summary comparing all output folders."""
    print(f"\n{'='*80}")
    print(f"CROSS-FOLDER SUMMARY")
    print(f"{'='*80}\n")
    
    # Total counts
    print(f"{'Output Folder':<35} {'Total Files':<15} {'Complete':<12} {'Incomplete':<12} {'Missing All'}")
    print("-" * 100)
    
    for folder_name, results in all_results.items():
        total = results['total_files']
        complete = sum(1 for bid in bump_ids 
                      if results['by_instance'].get(bid, {}).get('actual', 0) == 
                         results['by_instance'].get(bid, {}).get('expected', 0)
                      and results['by_instance'].get(bid, {}).get('expected', 0) > 0)
        incomplete = sum(1 for bid in bump_ids 
                        if 0 < results['by_instance'].get(bid, {}).get('actual', 0) < 
                           results['by_instance'].get(bid, {}).get('expected', 0))
        missing_all = sum(1 for bid in bump_ids 
                         if results['by_instance'].get(bid, {}).get('actual', 0) == 0
                         and results['by_instance'].get(bid, {}).get('expected', 0) > 0)
        
        print(f"{folder_name:<35} {total:<15} {complete:<12} {incomplete:<12} {missing_all}")
    
    # Instance-level comparison across folders
    print(f"\n{'='*80}")
    print(f"INSTANCE-LEVEL COMPARISON ACROSS ALL FOLDERS")
    print(f"{'='*80}\n")
    
    # Build header
    folder_names = list(all_results.keys())
    header = f"{'Instance ID':<30}"
    for fname in folder_names:
        header += f" {fname[:15]:<16}"
    print(header)
    print("-" * (30 + 16 * len(folder_names)))
    
    # Print each instance
    for bump_id in bump_ids:
        expected = None
        row = f"{bump_id:<30}"
        for folder_name in folder_names:
            info = all_results[folder_name]['by_instance'].get(bump_id, {'expected': 0, 'actual': 0})
            if expected is None:
                expected = info['expected']
            actual = info['actual']
            
            if actual == expected and expected > 0:
                status = f"✅ {actual}/{expected}"
            elif actual == 0 and expected > 0:
                status = f"❌ 0/{expected}"
            elif actual < expected:
                status = f"⚠️  {actual}/{expected}"
            elif actual > expected:
                status = f"⚠️+ {actual}/{expected}"
            else:
                status = f"-- {actual}/{expected}"
            
            row += f" {status:<16}"
        print(row)

# === MISSING INSTANCES REPORT ===
def print_missing_instances_report(all_results: dict, bump_ids: list):
    """Print detailed report of which instances are missing in each folder."""
    print(f"\n{'='*80}")
    print(f"DETAILED MISSING INSTANCES REPORT")
    print(f"{'='*80}\n")
    
    for folder_name, results in sorted(all_results.items()):
        # Find instances with missing files
        completely_missing = []
        partially_missing = []
        
        for bump_id in bump_ids:
            info = results['by_instance'].get(bump_id, {'expected': 0, 'actual': 0})
            expected = info['expected']
            actual = info['actual']
            
            if expected > 0:
                if actual == 0:
                    completely_missing.append(bump_id)
                elif actual < expected:
                    partially_missing.append((bump_id, actual, expected))
        
        # Print report for this folder
        print(f"\n{'─'*80}")
        print(f"OUTPUT FOLDER: {folder_name}")
        print(f"{'─'*80}")
        
        if completely_missing:
            print(f"\n❌ INSTANCES WITH ALL FILES MISSING ({len(completely_missing)}):")
            for i, bump_id in enumerate(completely_missing, 1):
                expected = results['by_instance'][bump_id]['expected']
                print(f"   {i:3d}. {bump_id} (0/{expected} files)")
        else:
            print(f"\n✅ No instances with all files missing")
        
        if partially_missing:
            print(f"\n⚠️  INSTANCES WITH PARTIAL FILES ({len(partially_missing)}):")
            for i, (bump_id, actual, expected) in enumerate(partially_missing, 1):
                print(f"   {i:3d}. {bump_id} ({actual}/{expected} files)")
                # Show which specific files are missing
                if bump_id in results['missing']:
                    print(f"        Missing files:")
                    for missing_file in results['missing'][bump_id][:5]:  # Show first 5
                        print(f"          - {missing_file}")
                    if len(results['missing'][bump_id]) > 5:
                        print(f"          ... and {len(results['missing'][bump_id]) - 5} more")
        else:
            print(f"\n✅ No instances with partial files missing")
        
        # Summary stats
        complete_instances = len(bump_ids) - len(completely_missing) - len(partially_missing)
        print(f"\n📊 SUMMARY:")
        print(f"   Complete instances: {complete_instances}/{len(bump_ids)}")
        print(f"   Partially missing:  {len(partially_missing)}/{len(bump_ids)}")
        print(f"   Completely missing: {len(completely_missing)}/{len(bump_ids)}")

# === OVERALL STATISTICS ===
def print_overall_statistics(all_results: dict, bump_ids: list):
    """Print overall statistics across all folders."""
    print(f"\n{'='*80}")
    print(f"OVERALL STATISTICS")
    print(f"{'='*80}\n")
    
    total_expected = 0
    total_actual = 0
    
    for folder_name, results in all_results.items():
        for bump_id in bump_ids:
            info = results['by_instance'].get(bump_id, {'expected': 0, 'actual': 0})
            total_expected += info['expected']
            total_actual += info['actual']
    
    print(f"Total files expected across all folders: {total_expected}")
    print(f"Total files generated across all folders: {total_actual}")
    print(f"Total files missing: {total_expected - total_actual}")
    
    if total_expected > 0:
        completion_rate = (total_actual / total_expected) * 100
        print(f"Overall completion rate: {completion_rate:.2f}%")
    
    # Find instances missing in ALL folders
    print(f"\n{'─'*80}")
    print(f"INSTANCES MISSING IN ALL OUTPUT FOLDERS:")
    print(f"{'─'*80}")
    
    missing_in_all = []
    for bump_id in bump_ids:
        missing_everywhere = True
        expected_count = 0
        for folder_name, results in all_results.items():
            info = results['by_instance'].get(bump_id, {'expected': 0, 'actual': 0})
            expected_count = info['expected']
            if info['actual'] > 0:
                missing_everywhere = False
                break
        
        if missing_everywhere and expected_count > 0:
            missing_in_all.append((bump_id, expected_count))
    
    if missing_in_all:
        print(f"\n⚠️  Found {len(missing_in_all)} instances missing in ALL folders:")
        for i, (bump_id, expected) in enumerate(missing_in_all, 1):
            print(f"   {i:3d}. {bump_id} ({expected} files expected)")
    else:
        print(f"\n✅ No instances are missing in ALL folders")

# === MAIN ===
if __name__ == "__main__":
    # Initialize report logger
    report_logger = ReportLogger(REPORT_FILE)
    import sys
    sys.stdout = report_logger
    
    print("="*80)
    print(f"OUTPUT FOLDER ANALYSIS - MULTIPLE FOLDERS SUPPORTED")
    print(f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    # Load bump instances from CSV
    bump_ids = load_bumps_from_csv(CSV_PATH)
    print(f"\nTotal bump instances in CSV: {len(bump_ids)}")
    print(f"Prompt directory: {PROMPT_DIR}")
    print(f"Total output folders to analyze: {len(OUTPUT_FOLDERS)}\n")
    
    # Show all folders being analyzed
    print("Output folders:")
    for folder_name, folder_path in OUTPUT_FOLDERS.items():
        exists = "✅" if folder_path.exists() else "❌"
        print(f"  {exists} {folder_name}: {folder_path}")
    
    # Analyze each output folder
    all_results = {}
    for folder_name, output_folder in OUTPUT_FOLDERS.items():
        if not output_folder.exists():
            print(f"\n⚠️  Warning: Output folder does not exist: {output_folder}")
            continue
        
        results = analyze_output_folder(output_folder, PROMPT_DIR, bump_ids)
        all_results[folder_name] = results
        print_report(folder_name, results, bump_ids)
    
    # Cross-folder summary
    if len(all_results) > 1:
        print_cross_folder_summary(all_results, bump_ids)
    
    # Detailed missing instances report
    if all_results:
        print_missing_instances_report(all_results, bump_ids)
        print_overall_statistics(all_results, bump_ids)
    
    print(f"\n{'='*80}")
    print("ANALYSIS COMPLETE")
    print(f"Report saved to: {REPORT_FILE}")
    print(f"{'='*80}\n")
    
    # Close logger
    report_logger.close()
    sys.stdout = report_logger.terminal