#!/usr/bin/env python3
"""
Failure type extraction from logs - Simple CSV output
"""

import json
import re
import csv
from pathlib import Path
from collections import Counter

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

# === CONFIG ===
PRE_RESULTS = PRIMARY_DRIVE / "GPTResults/Exp7BatchResults/pre/transplant_results_pre_filteredExp7.json"
BREAKING_RESULTS = PRIMARY_DRIVE / "GPTResults/Exp7BatchResults/breaking/transplant_results_final_breaking_hybrid.json"

PROMPT_ROOT = PRIMARY_DRIVE / "FilteredDataset/Exp7Prompts"  

LOG_DIR = PRIMARY_DRIVE / "GPTResults/Exp7BatchResults/bre/logs"
OUTPUT_DIR = PRIMARY_DRIVE / "GPTResults/Exp7BatchResults/breaking"

# Output file
FAILURE_TYPES_CSV = OUTPUT_DIR / "failure_types_analysis.csv"


def extract_metadata_from_prompt(prompt_text: str) -> dict:
    """Extract metadata from prompt file."""
    metadata = {'library': None, 'focal_method': None, 'focal_class': None}
    
    for line in prompt_text.split('\n'):
        line = line.strip()
        
        if 'OSS Library:' in line:
            parts = line.split('OSS Library:')
            if len(parts) > 1:
                lib = parts[1].strip()
                lib_parts = lib.split('.')
                if len(lib_parts) >= 3:
                    metadata['library'] = '.'.join(lib_parts[:3])
                else:
                    metadata['library'] = lib
        
        elif 'Focal method signature:' in line:
            match = re.search(r'(\w+)\s*\(', line)
            if match:
                metadata['focal_method'] = match.group(1)
        
        elif 'Focal class FQN:' in line:
            parts = line.split('Focal class FQN:')
            if len(parts) > 1:
                metadata['focal_class'] = parts[1].strip()
    
    return metadata


def load_metadata_for_tests(carry_forward_instances, carry_forward_tests, prompt_root):
    """Load metadata from prompt files."""
    metadata_db = {}
    
    total_processed = 0
    total_found = 0
    total_extracted = 0
    
    for instance_id in carry_forward_instances:
        passed_tests = carry_forward_tests.get(instance_id, {}).get('passed', [])
        if not passed_tests:
            continue
        
        prompt_dir = prompt_root / instance_id
        if not prompt_dir.exists():
            print(f"[WARN] No prompt dir for {instance_id}: {prompt_dir}")
            continue
        
        for test_file in passed_tests:
            total_processed += 1
            test_id = test_file.replace('.java', '')
            
            prompt_file = None
            for pattern in [f"{test_id}_prompt.txt", f"{test_id}.txt"]:
                candidate = prompt_dir / pattern
                if candidate.exists():
                    prompt_file = candidate
                    total_found += 1
                    break
            
            if not prompt_file:
                continue
            
            try:
                prompt_text = prompt_file.read_text(encoding='utf-8', errors='ignore')
                
                if total_extracted == 0:
                    print(f"\n[DEBUG] First prompt file: {prompt_file.name}")
                
                metadata = extract_metadata_from_prompt(prompt_text)
                
                if total_extracted == 0:
                    print(f"  Extracted metadata:")
                    print(f"    Library: {metadata['library']}")
                
                if metadata['library']:
                    metadata['instance_id'] = instance_id
                    metadata['test_file'] = test_file
                    metadata['test_id'] = test_id
                    metadata_db[test_id] = metadata
                    total_extracted += 1
                    
            except Exception as e:
                print(f"[ERROR] {prompt_file}: {e}")
    
    print(f"\n[SUMMARY]")
    print(f"  Tests to process: {total_processed}")
    print(f"  Prompt files found: {total_found}")
    print(f"  Metadata extracted: {total_extracted}")
    
    return metadata_db


def categorize_failure(log_text: str) -> dict:
    """
    Extract error type indicators from log.
    No hardcoded categories - just flags for observed errors.
    """
    result = {
        'has_compilation_error': False,
        'has_class_not_found': False,
        'has_no_class_def_found': False,
        'has_no_such_method': False,
        'has_no_such_field': False,
        'has_abstract_method_error': False,
        'has_incompatible_class_change': False,
        'has_illegal_access_error': False,
        'has_verify_error': False,
        'has_linkage_error': False,
        'has_other_error': False,
        'primary_error': None,
        'error_details': []
    }
    
    # Check for compilation errors
    compilation_patterns = [
        r'COMPILATION ERROR',
        r'compilation failure',
        r'Failed to execute goal.*maven-compiler-plugin.*compile',
        r'\[ERROR\].*\.java:\[\d+,\d+\]',
        r'cannot find symbol',
        r'package .* does not exist'
    ]
    
    for pattern in compilation_patterns:
        if re.search(pattern, log_text, re.IGNORECASE):
            result['has_compilation_error'] = True
            break
    
    # Check for various runtime errors
    error_checks = [
        ('has_class_not_found', r'ClassNotFoundException'),
        ('has_no_class_def_found', r'NoClassDefFoundError'),
        ('has_no_such_method', r'NoSuchMethodError'),
        ('has_no_such_field', r'NoSuchFieldError'),
        ('has_abstract_method_error', r'AbstractMethodError'),
        ('has_incompatible_class_change', r'IncompatibleClassChangeError'),
        ('has_illegal_access_error', r'IllegalAccessError'),
        ('has_verify_error', r'VerifyError'),
        ('has_linkage_error', r'LinkageError'),
    ]
    
    for key, pattern in error_checks:
        if re.search(pattern, log_text):
            result[key] = True
    
    # Extract actual error messages (up to 3)
    for line in log_text.split('\n'):
        stripped = line.strip()
        if ('Error' in stripped or 'Exception' in stripped) and not stripped.startswith('at '):
            if len(result['error_details']) < 3:
                result['error_details'].append(stripped[:200])
    
    # Check if any other error/exception exists
    if not any([result['has_compilation_error'], result['has_class_not_found'], 
                result['has_no_class_def_found'], result['has_no_such_method'],
                result['has_no_such_field'], result['has_abstract_method_error'],
                result['has_incompatible_class_change'], result['has_illegal_access_error'],
                result['has_verify_error'], result['has_linkage_error']]) and result['error_details']:
        result['has_other_error'] = True
    
    # Determine primary error (first one found in priority order)
    if result['has_compilation_error']:
        result['primary_error'] = 'compilation_error'
    elif result['has_no_class_def_found']:
        result['primary_error'] = 'NoClassDefFoundError'
    elif result['has_class_not_found']:
        result['primary_error'] = 'ClassNotFoundException'
    elif result['has_no_such_method']:
        result['primary_error'] = 'NoSuchMethodError'
    elif result['has_no_such_field']:
        result['primary_error'] = 'NoSuchFieldError'
    elif result['has_abstract_method_error']:
        result['primary_error'] = 'AbstractMethodError'
    elif result['has_incompatible_class_change']:
        result['primary_error'] = 'IncompatibleClassChangeError'
    elif result['has_illegal_access_error']:
        result['primary_error'] = 'IllegalAccessError'
    elif result['has_verify_error']:
        result['primary_error'] = 'VerifyError'
    elif result['has_linkage_error']:
        result['primary_error'] = 'LinkageError'
    elif result['has_other_error']:
        result['primary_error'] = 'other_error'
    else:
        result['primary_error'] = 'unknown'
    
    return result


def analyze_failure_types(metadata_db, breaking_data, log_dir):
    """Extract failure types from all failed tests."""
    failure_analysis = []
    
    for test_id, metadata in metadata_db.items():
        instance_id = metadata['instance_id']
        test_file = metadata['test_file']
        
        # Check if test failed in v2
        instance_results = breaking_data.get('results', {}).get(instance_id, {})
        v2_failed = test_file in instance_results.get('tests', {}).get('failed', [])
        
        if not v2_failed:
            continue  # Only analyze failures
        
        # Find log file
        log_file = None
        for suffix in ['single', 'multi']:
            candidate = log_dir / f"{instance_id}_{test_file}_breaking_{suffix}.log"
            if candidate.exists():
                log_file = candidate
                break
        
        if not log_file:
            candidate = log_dir / f"{instance_id}_{test_file}_breaking.log"
            if candidate.exists():
                log_file = candidate
        
        if not log_file:
            print(f"[WARN] No log file for {instance_id}/{test_file}")
            continue
        
        # Read log and categorize failure
        try:
            log_text = Path(log_file).read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            print(f"[ERROR] Failed to read {log_file}: {e}")
            continue
        
        failure_cat = categorize_failure(log_text)
        
        # Create failure record
        failure_record = {
            'instance_id': instance_id,
            'test_id': test_id,
            'test_file': test_file,
            'library': metadata.get('library', ''),
            'focal_class': metadata.get('focal_class', ''),
            'focal_method': metadata.get('focal_method', ''),
            
            # Error type flags
            'has_compilation_error': failure_cat['has_compilation_error'],
            'has_class_not_found': failure_cat['has_class_not_found'],
            'has_no_class_def_found': failure_cat['has_no_class_def_found'],
            'has_no_such_method': failure_cat['has_no_such_method'],
            'has_no_such_field': failure_cat['has_no_such_field'],
            'has_abstract_method_error': failure_cat['has_abstract_method_error'],
            'has_incompatible_class_change': failure_cat['has_incompatible_class_change'],
            'has_illegal_access_error': failure_cat['has_illegal_access_error'],
            'has_verify_error': failure_cat['has_verify_error'],
            'has_linkage_error': failure_cat['has_linkage_error'],
            'has_other_error': failure_cat['has_other_error'],
            
            # Primary error
            'primary_error': failure_cat['primary_error'],
            
            # Error details (raw messages)
            'error_detail_1': failure_cat['error_details'][0] if len(failure_cat['error_details']) > 0 else '',
            'error_detail_2': failure_cat['error_details'][1] if len(failure_cat['error_details']) > 1 else '',
            'error_detail_3': failure_cat['error_details'][2] if len(failure_cat['error_details']) > 2 else ''
        }
        
        failure_analysis.append(failure_record)
    
    return failure_analysis


def save_failure_types_csv(failure_analysis, output_path):
    """Save failure type analysis to CSV."""
    if not failure_analysis:
        print("[WARN] No failure type data to save")
        return
    
    columns = [
        'instance_id', 'test_id', 'test_file', 'library', 'focal_class', 'focal_method',
        'has_compilation_error',
        'has_class_not_found',
        'has_no_class_def_found',
        'has_no_such_method',
        'has_no_such_field',
        'has_abstract_method_error',
        'has_incompatible_class_change',
        'has_illegal_access_error',
        'has_verify_error',
        'has_linkage_error',
        'has_other_error',
        'primary_error',
        'error_detail_1',
        'error_detail_2',
        'error_detail_3'
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(failure_analysis)
    
    print(f"[INFO] Saved {len(failure_analysis)} failure records to {output_path}")


def print_failure_stats(failure_analysis):
    """Print failure type statistics."""
    if not failure_analysis:
        print("\n[WARN] No failures to analyze")
        return
    
    total = len(failure_analysis)
    
    # Count each error type
    compilation = sum(1 for f in failure_analysis if f['has_compilation_error'])
    class_not_found = sum(1 for f in failure_analysis if f['has_class_not_found'])
    no_class_def = sum(1 for f in failure_analysis if f['has_no_class_def_found'])
    no_method = sum(1 for f in failure_analysis if f['has_no_such_method'])
    no_field = sum(1 for f in failure_analysis if f['has_no_such_field'])
    abstract_method = sum(1 for f in failure_analysis if f['has_abstract_method_error'])
    incompatible = sum(1 for f in failure_analysis if f['has_incompatible_class_change'])
    illegal_access = sum(1 for f in failure_analysis if f['has_illegal_access_error'])
    verify = sum(1 for f in failure_analysis if f['has_verify_error'])
    linkage = sum(1 for f in failure_analysis if f['has_linkage_error'])
    other = sum(1 for f in failure_analysis if f['has_other_error'])
    
    # Count primary errors
    primary_counts = Counter(f['primary_error'] for f in failure_analysis)
    
    print(f"""
{'='*80}
FAILURE TYPE STATISTICS
{'='*80}

Total Failures Analyzed:  {total}

ERROR TYPE OCCURRENCE (tests can have multiple error types):
Compilation errors:       {compilation:4d} ({compilation/total*100:.1f}%)
NoClassDefFoundError:     {no_class_def:4d} ({no_class_def/total*100:.1f}%)
ClassNotFoundException:   {class_not_found:4d} ({class_not_found/total*100:.1f}%)
NoSuchMethodError:        {no_method:4d} ({no_method/total*100:.1f}%)
NoSuchFieldError:         {no_field:4d} ({no_field/total*100:.1f}%)
AbstractMethodError:      {abstract_method:4d} ({abstract_method/total*100:.1f}%)
IncompatibleClassChange:  {incompatible:4d} ({incompatible/total*100:.1f}%)
IllegalAccessError:       {illegal_access:4d} ({illegal_access/total*100:.1f}%)
VerifyError:              {verify:4d} ({verify/total*100:.1f}%)
LinkageError:             {linkage:4d} ({linkage/total*100:.1f}%)
Other errors:             {other:4d} ({other/total*100:.1f}%)

PRIMARY ERROR BREAKDOWN:
""")
    
    for error_type, count in primary_counts.most_common():
        pct = count/total*100
        print(f"  {error_type:30s} {count:4d} ({pct:5.1f}%)")
    
    print(f"\n{'='*80}")


def main():
    print("="*80)
    print("FAILURE TYPE EXTRACTION")
    print("="*80)
    
    # Validate paths
    if not PRE_RESULTS.exists():
        print(f"\n[ERROR] Pre-results file not found: {PRE_RESULTS}")
        return
    
    if not BREAKING_RESULTS.exists():
        print(f"\n[ERROR] Breaking results file not found: {BREAKING_RESULTS}")
        return
    
    if not PROMPT_ROOT.exists():
        print(f"\n[ERROR] Prompt root directory not found: {PROMPT_ROOT}")
        return
    
    if not LOG_DIR.exists():
        print(f"\n[ERROR] Log directory not found: {LOG_DIR}")
        return
    
    print("\n[1/4] Loading results...")
    try:
        pre_data = json.loads(PRE_RESULTS.read_text(encoding='utf-8'))
        breaking_data = json.loads(BREAKING_RESULTS.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"\n[ERROR] Failed to load JSON files: {e}")
        return
    
    carry_forward_instances = pre_data['carry_forward_instances']
    carry_forward_tests = pre_data['carry_forward_tests']
    
    print(f"  Instances: {len(carry_forward_instances)}")
    print(f"  Tests: {sum(len(t['passed']) for t in carry_forward_tests.values())}")
    
    print("\n[2/4] Extracting metadata from PROMPT files...")
    metadata_db = load_metadata_for_tests(carry_forward_instances, carry_forward_tests, PROMPT_ROOT)
    print(f"  Loaded metadata for {len(metadata_db)} tests")
    
    if len(metadata_db) == 0:
        print("\n[ERROR] No metadata loaded! Check PROMPT_ROOT path.")
        return
    
    print("\n[3/4] Analyzing failure types...")
    failure_analysis = analyze_failure_types(metadata_db, breaking_data, LOG_DIR)
    print(f"  Categorized {len(failure_analysis)} failures")
    
    if len(failure_analysis) == 0:
        print("\n[WARN] No failures found to analyze")
        return
    
    print("\n[4/4] Saving results...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    save_failure_types_csv(failure_analysis, FAILURE_TYPES_CSV)
    
    print("\nFailure statistics:")
    print_failure_stats(failure_analysis)
    
    print("\n" + "="*80)
    print("COMPLETE!")
    print("="*80)
    print(f"\nOutput file: {FAILURE_TYPES_CSV}")


if __name__ == "__main__":
    main()