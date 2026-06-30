import json
import csv
from pathlib import Path
from collections import defaultdict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

def load_json(json_path):
    """Load JSON file"""
    with open(json_path, 'r') as f:
        return json.load(f)

def load_csv(csv_path):
    """Load CSV and create a mapping of instance -> test files to remove"""
    to_remove = defaultdict(set)
    
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f, fieldnames=['folder_path', 'filename', 'full_path', 'test_class'])
        
        for row in reader:
            folder_path = row['folder_path'].strip()
            filename = row['filename'].strip()
            
            # Extract test file name (e.g., BBC79U0Test.java from BBC79U0Test_prompt.txt)
            if filename.endswith('_prompt.txt'):
                test_name = filename.replace('_prompt.txt', '.java')
                to_remove[folder_path].add(test_name)
    
    return to_remove

def update_json(data, to_remove):
    """Update JSON data by removing matching tests"""
    updated_data = data.copy()
    instances_to_remove = []
    
    # Get current carry_forward_instances
    carry_forward_instances = updated_data.get('carry_forward_instances', [])
    carry_forward_tests = updated_data.get('carry_forward_tests', {})
    
    # Process each instance in carry_forward_tests
    for instance, tests in carry_forward_tests.items():
        if instance in to_remove:
            tests_to_remove = to_remove[instance]
            
            # Remove matching tests from 'passed' list
            if 'passed' in tests:
                tests['passed'] = [t for t in tests['passed'] if t not in tests_to_remove]
            
            # Remove matching tests from 'failed' list
            if 'failed' in tests:
                tests['failed'] = [t for t in tests['failed'] if t not in tests_to_remove]
            
            # Check if passed list is empty
            if not tests.get('passed'):
                instances_to_remove.append(instance)
    
    # Remove instances that have no passed tests left from carry_forward_instances
    for instance in instances_to_remove:
        if instance in carry_forward_instances:
            carry_forward_instances.remove(instance)
    
    # After updating carry_forward_instances, remove any instances from carry_forward_tests
    # that are not in carry_forward_instances
    instances_to_remove_from_tests = [
        instance for instance in carry_forward_tests.keys()
        if instance not in carry_forward_instances
    ]
    
    for instance in instances_to_remove_from_tests:
        del carry_forward_tests[instance]
        print(f"Removed {instance} from carry_forward_tests (not in carry_forward_instances)")
    
    # Update the data
    updated_data['carry_forward_instances'] = carry_forward_instances
    updated_data['carry_forward_tests'] = carry_forward_tests
    
    return updated_data

def save_json(data, output_path):
    """Save updated JSON to file"""
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

def main():
    # File paths - update these to your actual file paths
    json_path = PRIMARY_DRIVE / "GPTResults/Exp7BatchResults/pre/transplant_results_final_pre.json"
    csv_path = PRIMARY_DRIVE / "GPTResults/Exp7BatchResults/test_files_filter.csv"
    output_path = PRIMARY_DRIVE / "GPTResults/Exp7BatchResults/pre/transplant_results_pre_filteredExp7.json"
    
    # Load data
    print("Loading JSON file...")
    json_data = load_json(json_path)
    
    print("Loading CSV file...")
    to_remove = load_csv(csv_path)
    
    print(f"Found {len(to_remove)} instances with tests to remove")
    
    # Update JSON
    print("Updating JSON data...")
    updated_data = update_json(json_data, to_remove)
    
    # Save updated JSON
    print(f"Saving updated JSON to {output_path}...")
    save_json(updated_data, output_path)
    
    print("Done!")

if __name__ == "__main__":
    main()