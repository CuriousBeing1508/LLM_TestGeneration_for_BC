import os
import re
import csv
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import PRIMARY_DRIVE

def extract_focal_class(file_path):
    """Extract the Focal class FQN from a txt file."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            # Search for the pattern "Focal class FQN: <fully.qualified.name>"
            match = re.search(r'Focal class FQN:\s*([^\s\n]+)', content)
            if match:
                return match.group(1).strip()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
    return None

def scan_all_folders(root_folder, output_csv):
    """
    Recursively scan all folders and subfolders for txt files.
    Find files where Focal class FQN ends with 'Test' and save to CSV.
    """
    results = []
    total_files_scanned = 0
    
    print(f"Starting scan of: {root_folder}")
    print("=" * 80)
    
    # Walk through all directories and subdirectories recursively
    for dirpath, dirnames, filenames in os.walk(root_folder):
        # Filter only .txt files
        txt_files = [f for f in filenames if f.endswith('.txt')]
        
        if txt_files:
            print(f"\nScanning folder: {dirpath}")
            print(f"Found {len(txt_files)} txt file(s)")
        
        for filename in txt_files:
            total_files_scanned += 1
            file_path = os.path.join(dirpath, filename)
            
            # Get the relative path from root folder
            relative_folder = os.path.relpath(dirpath, root_folder)
            
            # Extract focal class FQN from file content
            focal_class = extract_focal_class(file_path)
            
            # Check if focal class exists and ends with "Test"
            if focal_class and focal_class.endswith('Test'):
                results.append({
                    'folder_path': relative_folder,
                    'filename': filename,
                    'full_path': file_path,
                    'focal_class_fqn': focal_class
                })
                print(f"  ✓ MATCH: {filename} -> {focal_class}")
            else:
                if focal_class:
                    print(f"  ✗ Skip: {filename} -> {focal_class} (doesn't end with 'Test')")
    
    print("\n" + "=" * 80)
    print(f"Scan complete!")
    print(f"Total txt files scanned: {total_files_scanned}")
    print(f"Files matching criteria (ends with 'Test'): {len(results)}")
    
    # Write results to CSV file
    if results:
        with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['folder_path', 'filename', 'full_path', 'focal_class_fqn']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            for row in results:
                writer.writerow(row)
        
        print(f"\n✓ Results saved to: {output_csv}")
        print("\nSample results:")
        for i, row in enumerate(results[:5], 1):
            print(f"  {i}. {row['folder_path']}/{row['filename']}")
            print(f"     Focal class: {row['focal_class_fqn']}")
        
        if len(results) > 5:
            print(f"  ... and {len(results) - 5} more")
    else:
        print("\n✗ No files found with Focal class FQN ending with 'Test'")
        print("  Creating empty CSV file with headers only...")
        with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['folder_path', 'filename', 'full_path', 'focal_class_fqn']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
        print(f"✓ Empty CSV created: {output_csv}")

if __name__ == "__main__":
    # Configuration - Change these paths as needed. This script is filtering the test files from the prompt generation phase. Based on that, we need to remove identified files from Output and execution...
    root_folder = PRIMARY_DRIVE / "GeneratedPromptsExp6"  # Your main folder containing subfolders with txt files
    output_csv = PRIMARY_DRIVE / "Exp6BatchResults/test_files_filter.csv"  # Output CSV file name
    
    # Check if the root folder exists
    if not os.path.exists(root_folder):
        print(f"ERROR: Folder '{root_folder}' does not exist!")
        print(f"Current directory: {os.getcwd()}")
        print("\nPlease update the 'root_folder' variable in the script to point to your folder.")
    else:
        # Run the scanner
        scan_all_folders(root_folder, output_csv)
    
    print("\n" + "=" * 80)
    print("Script completed!")