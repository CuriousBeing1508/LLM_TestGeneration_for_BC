import os
import csv
import shutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import SECONDARY_DRIVE

def read_matched_files_from_csv(csv_file):
    """Read the CSV file and extract the list of matched files (folder + filename)."""
    matched_files = set()
    
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Store as tuple: (relative_folder, filename)
                folder_path = os.path.normpath(row['folder_path'])
                filename = row['filename']
                matched_files.add((folder_path, filename))
        
        print(f"Loaded {len(matched_files)} matched files from CSV")
        print("\nSample matched files:")
        for i, (folder, file) in enumerate(list(matched_files)[:5], 1):
            print(f"  {i}. {folder}/{file}")
        if len(matched_files) > 5:
            print(f"  ... and {len(matched_files) - 5} more")
        
        return matched_files
    except FileNotFoundError:
        print(f"ERROR: CSV file '{csv_file}' not found!")
        return None
    except Exception as e:
        print(f"ERROR reading CSV: {e}")
        return None

def copy_non_matched_files(source_folder, destination_folder, matched_files):
    """
    Copy all files and folder structure from source to destination,
    excluding the matched files from the CSV (based on relative path + filename).
    """
    copied_count = 0
    skipped_count = 0
    total_files = 0
    
    print(f"\nCopying files from: {source_folder}")
    print(f"Destination folder: {destination_folder}")
    print("=" * 80)
    
    # Create destination folder if it doesn't exist
    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)
        print(f"✓ Created destination folder: {destination_folder}\n")
    
    # Walk through all directories and subdirectories
    for dirpath, dirnames, filenames in os.walk(source_folder):
        # Get relative path from source folder
        relative_dir = os.path.relpath(dirpath, source_folder)
        relative_dir_normalized = os.path.normpath(relative_dir)
        
        # Create corresponding directory in destination
        if relative_dir != '.':
            dest_dir = os.path.join(destination_folder, relative_dir)
        else:
            dest_dir = destination_folder
        
        # Create the directory if it doesn't exist
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        
        # Process each file
        for filename in filenames:
            total_files += 1
            source_file = os.path.join(dirpath, filename)
            dest_file = os.path.join(dest_dir, filename)
            
            # Create tuple for comparison (relative_folder, filename)
            file_key = (relative_dir_normalized, filename)
            
            # Check if this file is in the matched files list
            if file_key in matched_files:
                skipped_count += 1
                print(f"✗ SKIP: {relative_dir}/{filename} (matched in CSV)")
            else:
                # Copy the file
                try:
                    shutil.copy2(source_file, dest_file)
                    copied_count += 1
                    if filename.endswith('.txt'):
                        print(f"✓ COPY: {relative_dir}/{filename}")
                except Exception as e:
                    print(f"ERROR copying {source_file}: {e}")
    
    print("\n" + "=" * 80)
    print("Copy operation complete!")
    print(f"Total files processed: {total_files}")
    print(f"Files copied: {copied_count}")
    print(f"Files skipped (matched): {skipped_count}")
    
    return copied_count, skipped_count

def remove_empty_directories(root_folder):
    """Remove empty directories from the folder structure."""
    removed_count = 0
    
    # Walk bottom-up to remove empty directories
    for dirpath, dirnames, filenames in os.walk(root_folder, topdown=False):
        # Skip the root directory
        if dirpath == root_folder:
            continue
        
        # Check if directory is empty
        if not os.listdir(dirpath):
            try:
                os.rmdir(dirpath)
                removed_count += 1
                print(f"  Removed empty directory: {os.path.relpath(dirpath, root_folder)}")
            except Exception as e:
                print(f"  ERROR removing directory {dirpath}: {e}")
    
    if removed_count > 0:
        print(f"\n✓ Removed {removed_count} empty director(ies)")
    else:
        print("\n✓ No empty directories to remove")

if __name__ == "__main__":
    # ==================== CONFIGURATION ====================
    # Input: CSV file from the previous scan script
    csv_file = SECONDARY_DRIVE / "GPTResults/Exp3BatchResults/test_files_filter.csv"
    
    # Source folder (the original folder with all files)
    source_folder = SECONDARY_DRIVE / "oldDataOnDRive/GeneratedOutputClientsExp3/Qwen3_480b_cloud"

    # Destination folder (where non-matched files will be copied)
    destination_folder = SECONDARY_DRIVE / "FilteredDataset/Exp3LLMOutput/Qwen_480b_cloud"
    # =======================================================
    
    print("=" * 80)
    print("FILE FILTER AND COPY SCRIPT")
    print("=" * 80)
    print(f"CSV file: {csv_file}")
    print(f"Source: {source_folder}")
    print(f"Destination: {destination_folder}")
    print("=" * 80)
    
    # Verify source folder exists
    if not os.path.exists(source_folder):
        print(f"\nERROR: Source folder '{source_folder}' does not exist!")
        exit(1)
    
    # Read matched files from CSV
    matched_files = read_matched_files_from_csv(csv_file)
    
    if matched_files is None:
        print("\nCannot proceed without CSV file. Exiting.")
        exit(1)
    
    if len(matched_files) == 0:
        print("\nWARNING: No matched files found in CSV.")
        response = input("Do you want to copy all files? (yes/no): ")
        if response.lower() != 'yes':
            print("Operation cancelled.")
            exit(0)
    
    # Ask for confirmation before proceeding
    print(f"\nThis will copy all files EXCEPT the {len(matched_files)} matched file(s)")
    print(f"from '{source_folder}' to '{destination_folder}'")
    response = input("\nProceed? (yes/no): ")
    
    if response.lower() != 'yes':
        print("Operation cancelled.")
        exit(0)
    
    # Perform the copy operation
    copied, skipped = copy_non_matched_files(source_folder, destination_folder, matched_files)
    
    # Remove empty directories from destination
    print("\nCleaning up empty directories...")
    remove_empty_directories(destination_folder)
    
    print("\n" + "=" * 80)
    print(" ALL DONE!")
    print(f" Filtered files are in: {destination_folder}")
    print("=" * 80)