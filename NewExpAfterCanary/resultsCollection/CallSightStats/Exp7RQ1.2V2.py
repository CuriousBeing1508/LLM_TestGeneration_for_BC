#!/usr/bin/env python3
"""
Stacktrace analysis with CSV output - FIXED VERSION
"""

import json
import re
import csv
from pathlib import Path
from collections import defaultdict

# === CONFIG ===
PRE_RESULTS = Path("/Volumes/Rachna-HD/GPTResults/Exp7BatchResults/pre/transplant_results_pre_filteredExp7.json")
BREAKING_RESULTS = Path("/Volumes/Rachna-HD/GPTResults/Exp7BatchResults/breaking/transplant_results_final_breaking_hybrid.json")

# UPDATED: Read from LLMPrompt
PROMPT_ROOT = Path("/Volumes/Rachna-HD/FilteredDataset/Exp7Prompts")  
PROMPT_OUTPUT_ROOT = Path("/Volumes/Rachna-HD/FilteredDataset/Exp7LLMOutput/GPT4o") 

LOG_DIR = Path("/Volumes/Rachna-HD/GPTResults/Exp7BatchResults/bre/logs")
OUTPUT_DIR = Path("/Volumes/Rachna-HD/GPTResults/Exp7BatchResults/breaking")

# Output files
OUTPUT_CSV = OUTPUT_DIR / "stacktrace_analysisRQ1.2.csv"
SUMMARY_CSV = OUTPUT_DIR / "instance_summaryRQ1.2.csv"
OUTPUT_JSON = OUTPUT_DIR / "stacktrace_analysisRQ1.2.json"


def extract_metadata_from_prompt(prompt_text: str) -> dict:
    """
    Extract metadata from prompt file.
    
    Now reading from actual prompt files, not generated code.
    """
    metadata = {'library': None, 'focal_method': None, 'focal_class': None}
    
    # Now the file should have the actual prompt format
    for line in prompt_text.split('\n'):
        line = line.strip()
        
        # Library: grab everything after "OSS Library:"
        if 'OSS Library:' in line:
            parts = line.split('OSS Library:')
            if len(parts) > 1:
                lib = parts[1].strip()
                # Extract package prefix (e.g., "com.fasterxml.jackson" from "com.fasterxml.jackson.databind")
                # This helps catch related packages like jackson-annotations
                lib_parts = lib.split('.')
                if len(lib_parts) >= 3:
                    # Use first 3 parts as base package (e.g., com.fasterxml.jackson)
                    metadata['library'] = '.'.join(lib_parts[:3])
                else:
                    metadata['library'] = lib
        
        # Focal method: grab method name before (
        elif 'Focal method signature:' in line:
            match = re.search(r'(\w+)\s*\(', line)
            if match:
                metadata['focal_method'] = match.group(1)
        
        # Focal class: grab everything after "Focal class FQN:"
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
        
        # Read from PROMPT directory
        prompt_dir = prompt_root / instance_id
        if not prompt_dir.exists():
            print(f"[WARN] No prompt dir for {instance_id}: {prompt_dir}")
            continue
        
        for test_file in passed_tests:
            total_processed += 1
            test_id = test_file.replace('.java', '')
            
            # Try different prompt file patterns
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
                
                # Debug first file
                if total_extracted == 0:
                    print(f"\n[DEBUG] First prompt file: {prompt_file.name}")
                    print(f"  File size: {len(prompt_text)} chars")
                    print(f"  First 300 chars:")
                    print(prompt_text[:300])
                
                metadata = extract_metadata_from_prompt(prompt_text)
                
                # Debug first extraction
                if total_extracted == 0:
                    print(f"\n  Extracted metadata:")
                    print(f"    Library: {metadata['library']}")
                    print(f"    Focal method: {metadata['focal_method']}")
                    print(f"    Focal class: {metadata['focal_class']}")
                
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


def analyze_execution_log(log_path: str, library_package: str, focal_class: str, focal_method: str) -> dict:
    """
    Improved stacktrace analysis.
    
    Changes:
    - Better library package matching (catches related packages)
    - Context-aware focal method detection (must be in focal class)
    - Distinguishes client code from test framework code
    - Detects failure type (class loading vs runtime vs API incompatibility)
    """
    if not Path(log_path).exists():
        return {
            'library_called': None,
            'library_methods': [],
            'client_code_called': None,
            'client_methods': [],
            'focal_class_called': None,
            'focal_method_called': None,
            'error_message': None,
            'stacktrace_depth': 0,
            'failure_type': None
        }
    
    try:
        log_text = Path(log_path).read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        print(f"[ERROR] Failed to read {log_path}: {e}")
        return {
            'library_called': None,
            'library_methods': [],
            'client_code_called': None,
            'client_methods': [],
            'focal_class_called': None,
            'focal_method_called': None,
            'error_message': f"Failed to read log: {e}",
            'stacktrace_depth': 0,
            'failure_type': 'read_error'
        }
    
    # Extract stacktrace
    stack_lines = []
    for line in log_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('at '):
            stack_lines.append(stripped)
    
    # Extract error message
    error_message = None
    for line in log_text.split('\n'):
        stripped = line.strip()
        if ('Error' in stripped or 'Exception' in stripped) and not stripped.startswith('at '):
            error_message = stripped[:200]
            break
    
    # Initialize result
    result = {
        'library_called': False,
        'library_methods': [],
        'client_code_called': False,
        'client_methods': [],
        'focal_class_called': False,
        'focal_method_called': False,
        'error_message': error_message,
        'stacktrace_depth': len(stack_lines),
        'failure_type': None
    }
    
    # Detect failure type
    if error_message:
        if any(err in error_message for err in ['ClassNotFoundException', 'NoClassDefFoundError']):
            result['failure_type'] = 'class_loading'
        elif any(err in error_message for err in ['NoSuchMethodError', 'NoSuchFieldError']):
            result['failure_type'] = 'api_incompatibility'
        elif any(err in error_message for err in ['AbstractMethodError', 'IncompatibleClassChangeError']):
            result['failure_type'] = 'binary_incompatibility'
        else:
            result['failure_type'] = 'runtime_exception'
    
    # Analyze stacktrace
    for line in stack_lines:
        # Extract class and method
        match = re.search(r'at\s+([\w\.$]+)\.([\w<>]+)\(', line)
        if not match:
            continue
        
        full_class = match.group(1)
        method_name = match.group(2)
        
        # Check for library code
        # Using startswith to catch related packages (e.g., com.fasterxml.jackson.* catches both databind and annotation)
        if library_package and full_class.startswith(library_package):
            result['library_called'] = True
            method = f"{full_class}.{method_name}()"
            if method not in result['library_methods']:
                result['library_methods'].append(method)
        
        # Check for client code (not library, not test framework, not JDK)
        is_library = library_package and full_class.startswith(library_package)
        is_test_framework = any(fw in full_class for fw in [
            'org.testng', 
            'junit', 
            'org.junit',
            'surefire', 
            'java.lang.reflect',
            'jdk.internal.reflect',
            'sun.reflect'
        ])
        is_jdk = full_class.startswith('java.') or full_class.startswith('javax.')
        
        if not is_library and not is_test_framework and not is_jdk:
            result['client_code_called'] = True
            full_method = f"{full_class}.{method_name}()"
            if full_method not in result['client_methods']:
                result['client_methods'].append(full_method)
            
            # Check if this is specifically the focal class
            if focal_class and focal_class in full_class:
                result['focal_class_called'] = True
                
                # Check if this is the focal method
                # Now using exact match (case-insensitive) instead of substring
                if focal_method and focal_method.lower() == method_name.lower():
                    result['focal_method_called'] = True
    
    return result


def analyze_all_v2_logs(metadata_db, breaking_data, log_dir):
    """Analyze all v2 logs and return results."""
    results = []
    
    for test_id, metadata in metadata_db.items():
        instance_id = metadata['instance_id']
        test_file = metadata['test_file']
        
        # Find log file
        log_file = None
        for suffix in ['single', 'multi']:
            candidate = log_dir / f"{instance_id}_{test_file}_breaking_{suffix}.log"
            if candidate.exists():
                log_file = candidate
                break
        
        if not log_file:
            # Try without suffix
            candidate = log_dir / f"{instance_id}_{test_file}_breaking.log"
            if candidate.exists():
                log_file = candidate
        
        if not log_file:
            print(f"[WARN] No log file for {instance_id}/{test_file}")
            continue
        
        # Analyze log
        execution = analyze_execution_log(
            str(log_file),
            metadata.get('library', ''),
            metadata.get('focal_class', ''),
            metadata.get('focal_method', '')
        )
        
        # Get v2 test results
        instance_results = breaking_data.get('results', {}).get(instance_id, {})
        v2_passed = test_file in instance_results.get('tests', {}).get('passed', [])
        v2_failed = test_file in instance_results.get('tests', {}).get('failed', [])
        
        # Combine all data
        result = {
            'instance_id': instance_id,
            'test_id': test_id,
            'test_file': test_file,
            'library': metadata.get('library', ''),
            'focal_class': metadata.get('focal_class', ''),
            'focal_method': metadata.get('focal_method', ''),
            
            # Test results
            'v1_passed': True,
            'v2_passed': v2_passed,
            'v2_failed': v2_failed,
            'bc_detected': v2_failed,
            
            # Execution analysis
            'library_called': execution['library_called'],
            'library_methods_count': len(execution['library_methods']),
            'library_methods': '; '.join(execution['library_methods'][:3]),
            'client_code_called': execution['client_code_called'],
            'client_methods_count': len(execution['client_methods']),
            'client_methods': '; '.join(execution['client_methods'][:3]),
            'focal_class_called': execution['focal_class_called'],
            'focal_method_called': execution['focal_method_called'],
            'failure_type': execution['failure_type'],
            'error_message': execution['error_message'],
            'stacktrace_depth': execution['stacktrace_depth']
        }
        
        results.append(result)
    
    return results


def save_to_csv(results, output_path):
    """Save results to CSV."""
    if not results:
        print("[WARN] No results to save")
        return
    
    columns = [
        'instance_id', 'test_id', 'test_file', 'library', 'focal_class', 'focal_method',
        'v1_passed', 'v2_passed', 'v2_failed', 'bc_detected',
        'library_called', 'library_methods_count', 'library_methods',
        'client_code_called', 'client_methods_count', 'client_methods',
        'focal_class_called', 'focal_method_called', 
        'failure_type', 'error_message', 'stacktrace_depth'
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"[INFO] Saved {len(results)} rows to {output_path}")


def generate_instance_summary(results, output_path):
    """Generate per-instance summary CSV."""
    by_instance = defaultdict(list)
    for result in results:
        by_instance[result['instance_id']].append(result)
    
    summary_rows = []
    
    for instance_id, tests in sorted(by_instance.items()):
        total_tests = len(tests)
        bc_detected = sum(1 for t in tests if t['bc_detected'])
        
        if bc_detected > 0:
            lib_called = sum(1 for t in tests if t['bc_detected'] and t['library_called'])
            client_called = sum(1 for t in tests if t['bc_detected'] and t['client_code_called'])
            focal_class_called = sum(1 for t in tests if t['bc_detected'] and t['focal_class_called'])
            focal_method_called = sum(1 for t in tests if t['bc_detected'] and t['focal_method_called'])
            both_called = sum(1 for t in tests if t['bc_detected'] and t['library_called'] and t['client_code_called'])
            
            # Count failure types
            class_loading = sum(1 for t in tests if t['bc_detected'] and t['failure_type'] == 'class_loading')
            api_incompat = sum(1 for t in tests if t['bc_detected'] and t['failure_type'] == 'api_incompatibility')
            runtime_exc = sum(1 for t in tests if t['bc_detected'] and t['failure_type'] == 'runtime_exception')
        else:
            lib_called = client_called = focal_class_called = focal_method_called = both_called = 0
            class_loading = api_incompat = runtime_exc = 0
        
        library = tests[0]['library'] if tests else ''
        
        summary_rows.append({
            'instance_id': instance_id,
            'library': library,
            'total_tests': total_tests,
            'bc_detected_count': bc_detected,
            'bc_detected_pct': f"{bc_detected/total_tests*100:.1f}%" if total_tests else '0%',
            'library_called_count': lib_called,
            'library_called_pct': f"{lib_called/bc_detected*100:.1f}%" if bc_detected else '0%',
            'client_code_called_count': client_called,
            'client_code_called_pct': f"{client_called/bc_detected*100:.1f}%" if bc_detected else '0%',
            'focal_class_called_count': focal_class_called,
            'focal_class_called_pct': f"{focal_class_called/bc_detected*100:.1f}%" if bc_detected else '0%',
            'focal_method_called_count': focal_method_called,
            'focal_method_called_pct': f"{focal_method_called/bc_detected*100:.1f}%" if bc_detected else '0%',
            'both_called_count': both_called,
            'both_called_pct': f"{both_called/bc_detected*100:.1f}%" if bc_detected else '0%',
            'class_loading_failures': class_loading,
            'api_incompatibility_failures': api_incompat,
            'runtime_exception_failures': runtime_exc
        })
    
    columns = [
        'instance_id', 'library', 'total_tests', 'bc_detected_count', 'bc_detected_pct',
        'library_called_count', 'library_called_pct', 
        'client_code_called_count', 'client_code_called_pct',
        'focal_class_called_count', 'focal_class_called_pct',
        'focal_method_called_count', 'focal_method_called_pct', 
        'both_called_count', 'both_called_pct',
        'class_loading_failures', 'api_incompatibility_failures', 'runtime_exception_failures'
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(summary_rows)
    
    print(f"[INFO] Saved {len(summary_rows)} instance summaries to {output_path}")


def print_summary_stats(results):
    """Print summary statistics to console."""
    total = len(results)
    bc_detected = [r for r in results if r['bc_detected']]
    total_bc = len(bc_detected)
    
    if total_bc == 0:
        print("\n[WARN] No BC detections found")
        return
    
    lib_called = sum(1 for r in bc_detected if r['library_called'])
    client_called = sum(1 for r in bc_detected if r['client_code_called'])
    focal_class_called = sum(1 for r in bc_detected if r['focal_class_called'])
    focal_method_called = sum(1 for r in bc_detected if r['focal_method_called'])
    both = sum(1 for r in bc_detected if r['library_called'] and r['client_code_called'])
    
    # Failure type breakdown
    class_loading = sum(1 for r in bc_detected if r['failure_type'] == 'class_loading')
    api_incompat = sum(1 for r in bc_detected if r['failure_type'] == 'api_incompatibility')
    binary_incompat = sum(1 for r in bc_detected if r['failure_type'] == 'binary_incompatibility')
    runtime_exc = sum(1 for r in bc_detected if r['failure_type'] == 'runtime_exception')
    
    print(f"""
{'='*80}
SUMMARY STATISTICS
{'='*80}

Total Tests Analyzed:     {total}
BC Detections (v2 fail):  {total_bc} ({total_bc/total*100:.1f}%)

AMONG BC DETECTIONS:
Library called:           {lib_called:4d} / {total_bc} ({lib_called/total_bc*100:.1f}%)
Client code called:       {client_called:4d} / {total_bc} ({client_called/total_bc*100:.1f}%)
Focal class called:       {focal_class_called:4d} / {total_bc} ({focal_class_called/total_bc*100:.1f}%)
Focal method called:      {focal_method_called:4d} / {total_bc} ({focal_method_called/total_bc*100:.1f}%)
Both lib + client:        {both:4d} / {total_bc} ({both/total_bc*100:.1f}%)

FAILURE TYPE BREAKDOWN:
Class loading failures:   {class_loading:4d} / {total_bc} ({class_loading/total_bc*100:.1f}%)
API incompatibilities:    {api_incompat:4d} / {total_bc} ({api_incompat/total_bc*100:.1f}%)
Binary incompatibilities: {binary_incompat:4d} / {total_bc} ({binary_incompat/total_bc*100:.1f}%)
Runtime exceptions:       {runtime_exc:4d} / {total_bc} ({runtime_exc/total_bc*100:.1f}%)

{'='*80}
""")


def main():
    print("="*80)
    print("STACKTRACE ANALYSIS - CSV OUTPUT (FIXED VERSION)")
    print("="*80)
    
    # Validate paths exist
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
    
    print("\n[1/5] Loading results...")
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
    
    print("\n[2/5] Extracting metadata from PROMPT files...")
    print(f"  Prompt root: {PROMPT_ROOT}")
    
    metadata_db = load_metadata_for_tests(carry_forward_instances, carry_forward_tests, PROMPT_ROOT)
    print(f"  Loaded metadata for {len(metadata_db)} tests")
    
    if len(metadata_db) == 0:
        print("\n[ERROR] No metadata loaded! Check PROMPT_ROOT path.")
        return
    
    print("\n[3/5] Analyzing v2 execution logs...")
    results = analyze_all_v2_logs(metadata_db, breaking_data, LOG_DIR)
    print(f"  Analyzed {len(results)} test executions")
    
    if len(results) == 0:
        print("\n[ERROR] No results generated! Check LOG_DIR path.")
        return
    
    print("\n[4/5] Saving results...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    save_to_csv(results, OUTPUT_CSV)
    OUTPUT_JSON.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f"[INFO] Saved JSON to {OUTPUT_JSON}")
    
    generate_instance_summary(results, SUMMARY_CSV)
    
    print("\n[5/5] Summary statistics...")
    print_summary_stats(results)
    
    print("\n" + "="*80)
    print("COMPLETE!")
    print("="*80)
    print(f"\nOutput files:")
    print(f"  - Detailed: {OUTPUT_CSV}")
    print(f"  - Summary:  {SUMMARY_CSV}")
    print(f"  - JSON:     {OUTPUT_JSON}")


if __name__ == "__main__":
    main()