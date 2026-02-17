#!/usr/bin/env python3
"""
Enhanced stacktrace analysis with direct vs propagated call pattern detection.

Stacktrace Analysis: Direct vs Propagated OSS Library Call Detection

This script analyzes test execution logs to determine HOW tests call OSS libraries:
  - DIRECT: Test code directly calls OSS library methods
  - PROPAGATED: Test calls client's focal method, which then calls OSS library

Inputs:
  - Prompt files: To extract library name, focal class, focal method informations
  - Execution logs: Parse stack traces from failing tests on BRE image

Outputs:
  - stacktrace_analysis_RQ3.csv: Detailed per-test analysis with call_pattern column
  - instance_summary_RQ3.csv: Aggregated stats per instance
  - call_pattern_summary_RQ3.csv: Overall pattern breakdown

The script parses stack traces to determine call order:
  If stack shows: Test → Client → Library = PROPAGATED
  If stack shows: Test → Library = DIRECT
"""

import json
import re
import csv
from pathlib import Path
from collections import defaultdict

# === CONFIG ===
PRE_RESULTS = Path("/Volumes/Rachna-HD/GPTResults/Exp6BatchResults/pre/transplant_results_final_pre.json")
BREAKING_RESULTS = Path("/Volumes/Rachna-HD/GPTResults/Exp6BatchResults/bre/transplant_results_breaking_single_module.json")

PROMPT_ROOT = Path("/Volumes/Rachna-HD/FilteredDataset/Exp7Prompts")
LOG_DIR = Path("/Volumes/Rachna-HD/GPTResults/Exp6BatchResults/bre/logs")
OUTPUT_DIR = Path("/Volumes/Rachna-HD/GPTResults/Exp6BatchResults/bre/RQ3")

# Output files
OUTPUT_CSV = OUTPUT_DIR / "stacktrace_analysis_RQ3.csv"
SUMMARY_CSV = OUTPUT_DIR / "instance_summary_RQ3.csv"
OUTPUT_JSON = OUTPUT_DIR / "stacktrace_analysis_RQ3.json"
PATTERN_SUMMARY_CSV = OUTPUT_DIR / "call_pattern_summary_RQ3.csv"


def extract_metadata_from_prompt(prompt_text: str) -> dict:
    """
    Extract metadata from prompt file including test class name.
    """
    metadata = {
        'library': None,
        'focal_method': None,
        'focal_class': None,
        'test_class': None
    }
    
    for line in prompt_text.split('\n'):
        line = line.strip()
        
        # Library: grab everything after "OSS Library:"
        if 'OSS Library:' in line:
            parts = line.split('OSS Library:')
            if len(parts) > 1:
                metadata['library'] = parts[1].strip()
        
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
        
        # Test class: try to extract if mentioned
        elif 'Test class:' in line or 'test class' in line.lower():
            match = re.search(r'([\w\.]+Test)', line)
            if match:
                metadata['test_class'] = match.group(1)
    
    return metadata


def load_metadata_for_tests(carry_forward_instances, carry_forward_tests, prompt_root):
    """Load metadata from prompt files with test class inference."""
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
                
                # Infer test class from test_id if not found in prompt
                if not metadata['test_class']:
                    metadata['test_class'] = test_id
                
                # Debug first extraction
                if total_extracted == 0:
                    print(f"\n  Extracted metadata:")
                    print(f"    Library: {metadata['library']}")
                    print(f"    Focal method: {metadata['focal_method']}")
                    print(f"    Focal class: {metadata['focal_class']}")
                    print(f"    Test class: {metadata['test_class']}")
                
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


def determine_call_pattern(test_frames, client_frames, library_frames, parsed_stack):
    """
    Determine if library is called directly from test or through client code.
    
    Call patterns:
    - DIRECT: Test -> Library (no client code in between)
    - PROPAGATED: Test -> Client -> Library
    - UNKNOWN: Cannot determine from stacktrace
    """
    if not library_frames:
        return 'no_library_call'
    
    if not test_frames:
        return 'unknown'  # No test in stack (shouldn't happen)
    
    # Get indices (stacktrace is top-to-bottom, index 0 is deepest)
    test_idx = test_frames[0][0]  # Deepest test frame
    library_idx = library_frames[-1][0]  # First library frame (closest to error)
    
    # Check if there's client code between test and library
    if client_frames:
        client_idx = client_frames[-1][0]  # First client frame
        
        # Pattern: Test (high idx) -> Client (mid idx) -> Library (low idx)
        # In stack order: Library appears first (low idx), then client, then test
        if test_idx > client_idx > library_idx:
            return 'propagated'
        
        # Client called but not in the path to library
        elif test_idx > library_idx and client_idx < library_idx:
            return 'direct'  # Test directly called library, client elsewhere
        
        # Client appears after test (shouldn't happen in normal flow)
        elif client_idx > test_idx:
            return 'unknown'
    
    # No client frames, check if test directly precedes library
    if test_idx > library_idx:
        # Check if there are any intermediate frames that are NOT test or library
        intermediate_frames = parsed_stack[library_idx+1:test_idx]
        
        # Filter out JUnit/test framework frames
        non_framework = [f for f in intermediate_frames 
                        if not any(x in f['full_class'].lower() 
                                 for x in ['junit', 'test', 'reflect', 'invoke', 'method'])]
        
        if len(non_framework) == 0:
            return 'direct'  # Direct call, only test framework in between
        else:
            # There's something in between, but it's not our client code
            # Could be other library code or utility methods
            return 'direct_with_intermediary'
    
    return 'unknown'
def analyze_all_v2_logs(metadata_db, breaking_data, log_dir):
    """Analyze all v2 logs with enhanced pattern detection using STDERR parsing."""
    results = []
    
    # Track statistics
    v2_failed_count = 0
    v2_passed_count = 0
    not_found_count = 0
    
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
            continue
        
        # Analyze log WITH test_class - USING THE NEW FUNCTION
        execution = analyze_execution_log_with_stderr(
            str(log_file),
            metadata.get('library', ''),
            metadata.get('focal_class', ''),
            metadata.get('focal_method', ''),
            metadata.get('test_class', test_id)
        )
        
        # Get v2 test results - FIXED to handle dict format
        instance_results = breaking_data.get('results', {}).get(instance_id, {})
        tests_data = instance_results.get('tests', {})
        
        # Extract file names (handles both strings and dicts)
        passed_files = extract_test_files(tests_data.get('passed', []))
        failed_files = extract_test_files(tests_data.get('failed', []))
        
        v2_passed = test_file in passed_files
        v2_failed = test_file in failed_files
        
        # Track stats
        if v2_failed:
            v2_failed_count += 1
        elif v2_passed:
            v2_passed_count += 1
        else:
            not_found_count += 1
        
        # Debug first 5 tests
        if len(results) < 5:
            print(f"\n[DEBUG] Test {len(results) + 1}: {test_id}")
            print(f"  Instance: {instance_id}")
            print(f"  Test file: {test_file}")
            print(f"  Stack depth: {execution['stacktrace_depth']}")
            print(f"  Library called: {execution['library_called']}")
            print(f"  v2_passed: {v2_passed}, v2_failed: {v2_failed}")
        
        # Combine all data
        result = {
            'instance_id': instance_id,
            'test_id': test_id,
            'test_file': test_file,
            'library': metadata.get('library', ''),
            'focal_class': metadata.get('focal_class', ''),
            'focal_method': metadata.get('focal_method', ''),
            'test_class': metadata.get('test_class', ''),
            
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
            
            # Call pattern analysis
            'call_pattern': execution['call_pattern'],
            'test_to_library_direct': execution['test_to_library_direct'],
            'test_to_client_to_library': execution['test_to_client_to_library'],
            
            'error_message': execution['error_message'],
            'stacktrace_depth': execution['stacktrace_depth'],
            'sample_stacktrace': '; '.join(execution['full_stacktrace'][:3])
        }
        
        results.append(result)
    
    # Print summary
    print(f"\n[INFO] Test Results Summary:")
    print(f"  v2 Failed (BC detected): {v2_failed_count}")
    print(f"  v2 Passed: {v2_passed_count}")
    print(f"  Not found in breaking data: {not_found_count}")
    
    return results


# def analyze_execution_log_with_stderr(log_path: str, library_package: str, focal_class: str, 
#                           focal_method: str, test_class: str) -> dict:
#     """
#     Enhanced stacktrace analysis to detect direct vs propagated calls.
#     Parse BOTH stdout and stderr sections - prioritize stderr but fallback to stdout.
#     """
#     if not Path(log_path).exists():
#         return {
#             'library_called': None,
#             'library_methods': [],
#             'client_code_called': None,
#             'client_methods': [],
#             'focal_class_called': None,
#             'focal_method_called': None,
#             'call_pattern': None,
#             'test_to_library_direct': None,
#             'test_to_client_to_library': None,
#             'error_message': None,
#             'stacktrace_depth': 0,
#             'full_stacktrace': []
#         }
    
#     log_text = Path(log_path).read_text(encoding='utf-8', errors='ignore')
    
#     # Split log into sections
#     sections = {'stdout': '', 'stderr': ''}
#     current_section = None
    
#     for line in log_text.split('\n'):
#         if line.strip() == '=== STDOUT ===':
#             current_section = 'stdout'
#         elif line.strip() == '=== STDERR ===':
#             current_section = 'stderr'
#         elif line.strip().startswith('=== '):
#             current_section = None
#         elif current_section:
#             sections[current_section] += line + '\n'
    
#     # Extract stacktrace from BOTH stdout and stderr
#     stderr_stack = []
#     stdout_stack = []
    
#     # Get frames from stderr (usually has full stack for errors)
#     for line in sections['stderr'].split('\n'):
#         stripped = line.strip()
#         if stripped.startswith('at '):
#             stderr_stack.append(stripped)
    
#     # Get frames from stdout (usually has stack for test failures)
#     for line in sections['stdout'].split('\n'):
#         stripped = line.strip()
#         if stripped.startswith('at '):
#             stdout_stack.append(stripped)
    
#     # Choose the longer/more complete stack trace
#     # Stderr usually has more details for initialization errors
#     # Stdout usually has details for test assertion failures
#     if len(stderr_stack) > len(stdout_stack):
#         stack_lines = stderr_stack
#         stack_source = 'stderr'
#     elif len(stdout_stack) > 0:
#         stack_lines = stdout_stack
#         stack_source = 'stdout'
#     else:
#         # Try to extract from anywhere in the log as fallback
#         stack_lines = []
#         for line in log_text.split('\n'):
#             stripped = line.strip()
#             if stripped.startswith('at '):
#                 stack_lines.append(stripped)
#         stack_source = 'combined'
    
#     # Extract error message from both sections
#     error_message = None
#     combined_text = sections['stdout'] + '\n' + sections['stderr']
    
#     for line in combined_text.split('\n'):
#         stripped = line.strip()
#         if ('Error' in stripped or 'Exception' in stripped or 'Failure' in stripped) and not stripped.startswith('at '):
#             if not error_message:
#                 error_message = stripped[:200]
#                 break
    
    
#     # Parse stacktrace into structured format
#     parsed_stack = []
#     for line in stack_lines:
#         match = re.search(r'at\s+([\w\.\$]+)\.([\w<>]+)\(([^)]*)\)', line)
#         if match:
#             parsed_stack.append({
#                 'full_class': match.group(1),
#                 'method': match.group(2),
#                 'location': match.group(3),
#                 'raw': line
#             })
    
#     # Initialize result
#     result = {
#         'library_called': False,
#         'library_methods': [],
#         'client_code_called': False,
#         'client_methods': [],
#         'focal_class_called': False,
#         'focal_method_called': False,
#         'call_pattern': 'unknown',
#         'test_to_library_direct': False,
#         'test_to_client_to_library': False,
#         'error_message': error_message,
#         'stacktrace_depth': len(stack_lines),
#         'full_stacktrace': stack_lines[:10],
#         'stack_source': stack_source  # Track where stack came from
#     }
    
#     # Analyze each frame
#     test_frames = []
#     client_frames = []
#     library_frames = []
    
#     for i, frame in enumerate(parsed_stack):
#         full_class = frame['full_class']
#         method = frame['method']
        
#         # Identify frame type - flexible matching
#         is_test = test_class and (
#             test_class in full_class or 
#             full_class.endswith(f".{test_class}") or
#             full_class.endswith(test_class)
#         )
        
#         is_client = focal_class and (
#             focal_class in full_class or
#             full_class == focal_class or
#             full_class.endswith(f".{focal_class.split('.')[-1]}")
#         )
        
#         is_library = library_package and library_package.lower() in full_class.lower()
        
#         if is_test:
#             test_frames.append((i, frame))
        
#         if is_client:
#             client_frames.append((i, frame))
#             result['client_code_called'] = True
#             result['focal_class_called'] = True
#             full_method = f"{full_class}.{method}()"
#             if full_method not in result['client_methods']:
#                 result['client_methods'].append(full_method)
        
#         if is_library:
#             library_frames.append((i, frame))
#             result['library_called'] = True
#             full_method = f"{full_class}.{method}()"
#             if full_method not in result['library_methods']:
#                 result['library_methods'].append(full_method)
        
#         # Check for focal method
#         if focal_method and focal_method.lower() in method.lower():
#             result['focal_method_called'] = True
    
#     # Determine call pattern by analyzing stack order
#     if result['library_called']:
#         result['call_pattern'] = determine_call_pattern(
#             test_frames, client_frames, library_frames, parsed_stack
#         )
        
#         if result['call_pattern'] == 'direct' or result['call_pattern'] == 'direct_with_intermediary':
#             result['test_to_library_direct'] = True
#         elif result['call_pattern'] == 'propagated':
#             result['test_to_client_to_library'] = True
#     else:
#         result['call_pattern'] = 'no_library_call'

#     # NEW: If no library in stack but library mentioned in error, mark as library-related
#     if not result['library_called'] and error_has_library:
#         result['library_called'] = True
#         result['library_methods'].append(f"{library_package} (in error message)")
#         result['call_pattern'] = 'error_before_execution'
#         result['test_to_library_direct'] = True  # Test tried to call library directly
    
#     return result


def analyze_execution_log_with_stderr(log_path: str, library_package: str, focal_class: str, 
                          focal_method: str, test_class: str) -> dict:
    """
    Enhanced stacktrace analysis to detect direct vs propagated calls.
    Parse BOTH stdout and stderr sections - prioritize stderr but fallback to stdout.
    """
    if not Path(log_path).exists():
        return {
            'library_called': None,
            'library_methods': [],
            'client_code_called': None,
            'client_methods': [],
            'focal_class_called': None,
            'focal_method_called': None,
            'call_pattern': None,
            'test_to_library_direct': None,
            'test_to_client_to_library': None,
            'error_message': None,
            'stacktrace_depth': 0,
            'full_stacktrace': []
        }
    
    log_text = Path(log_path).read_text(encoding='utf-8', errors='ignore')
    
    # Split log into sections
    sections = {'stdout': '', 'stderr': ''}
    current_section = None
    
    for line in log_text.split('\n'):
        if line.strip() == '=== STDOUT ===':
            current_section = 'stdout'
        elif line.strip() == '=== STDERR ===':
            current_section = 'stderr'
        elif line.strip().startswith('=== '):
            current_section = None
        elif current_section:
            sections[current_section] += line + '\n'
    
    # Extract stacktrace from BOTH stdout and stderr
    stderr_stack = []
    stdout_stack = []
    
    # Get frames from stderr (usually has full stack for errors)
    for line in sections['stderr'].split('\n'):
        stripped = line.strip()
        if stripped.startswith('at '):
            stderr_stack.append(stripped)
    
    # Get frames from stdout (usually has stack for test failures)
    for line in sections['stdout'].split('\n'):
        stripped = line.strip()
        if stripped.startswith('at '):
            stdout_stack.append(stripped)
    
    # Choose the longer/more complete stack trace
    if len(stderr_stack) > len(stdout_stack):
        stack_lines = stderr_stack
        stack_source = 'stderr'
    elif len(stdout_stack) > 0:
        stack_lines = stdout_stack
        stack_source = 'stdout'
    else:
        stack_lines = []
        for line in log_text.split('\n'):
            stripped = line.strip()
            if stripped.startswith('at '):
                stack_lines.append(stripped)
        stack_source = 'combined'
    
    # Extract error message from both sections
    error_message = None
    error_has_library = False  # INITIALIZE HERE - before the loop
    combined_text = sections['stdout'] + '\n' + sections['stderr']
    
    for line in combined_text.split('\n'):
        stripped = line.strip()
        if ('Error' in stripped or 'Exception' in stripped or 'Failure' in stripped) and not stripped.startswith('at '):
            if not error_message:
                error_message = stripped[:200]
            
            # Check if library is mentioned in error message
            if library_package and library_package.lower() in stripped.lower():
                error_has_library = True
    
    # Parse stacktrace into structured format
    parsed_stack = []
    for line in stack_lines:
        match = re.search(r'at\s+([\w\.\$]+)\.([\w<>]+)\(([^)]*)\)', line)
        if match:
            parsed_stack.append({
                'full_class': match.group(1),
                'method': match.group(2),
                'location': match.group(3),
                'raw': line
            })
    
    # Initialize result
    result = {
        'library_called': False,
        'library_methods': [],
        'client_code_called': False,
        'client_methods': [],
        'focal_class_called': False,
        'focal_method_called': False,
        'call_pattern': 'unknown',
        'test_to_library_direct': False,
        'test_to_client_to_library': False,
        'error_message': error_message,
        'stacktrace_depth': len(stack_lines),
        'full_stacktrace': stack_lines[:10],
        'stack_source': stack_source
    }
    
    # Analyze each frame
    test_frames = []
    client_frames = []
    library_frames = []
    
    for i, frame in enumerate(parsed_stack):
        full_class = frame['full_class']
        method = frame['method']
        
        # Identify frame type - flexible matching
        is_test = test_class and (
            test_class in full_class or 
            full_class.endswith(f".{test_class}") or
            full_class.endswith(test_class)
        )
        
        is_client = focal_class and (
            focal_class in full_class or
            full_class == focal_class or
            full_class.endswith(f".{focal_class.split('.')[-1]}")
        )
        
        is_library = library_package and library_package.lower() in full_class.lower()
        
        if is_test:
            test_frames.append((i, frame))
        
        if is_client:
            client_frames.append((i, frame))
            result['client_code_called'] = True
            result['focal_class_called'] = True
            full_method = f"{full_class}.{method}()"
            if full_method not in result['client_methods']:
                result['client_methods'].append(full_method)
        
        if is_library:
            library_frames.append((i, frame))
            result['library_called'] = True
            full_method = f"{full_class}.{method}()"
            if full_method not in result['library_methods']:
                result['library_methods'].append(full_method)
        
        # Check for focal method
        if focal_method and focal_method.lower() in method.lower():
            result['focal_method_called'] = True
    
    # Determine call pattern by analyzing stack order
    if result['library_called']:
        result['call_pattern'] = determine_call_pattern(
            test_frames, client_frames, library_frames, parsed_stack
        )
        
        if result['call_pattern'] == 'direct' or result['call_pattern'] == 'direct_with_intermediary':
            result['test_to_library_direct'] = True
        elif result['call_pattern'] == 'propagated':
            result['test_to_client_to_library'] = True
    else:
        result['call_pattern'] = 'no_library_call'
    
    # Handle cases where library is in error message but not in stack
    # This happens with NoSuchMethodError, NoClassDefFoundError, etc.
    if not result['library_called'] and error_has_library:
        result['library_called'] = True
        result['library_methods'].append(f"{library_package} (mentioned in error)")
        result['call_pattern'] = 'error_before_execution'
        result['test_to_library_direct'] = True
    
    return result


def extract_test_files(test_list):
    """
    Extract test file names from a list that might contain strings or dicts.
    
    Args:
        test_list: List of either strings or dicts with 'file' key
    
    Returns:
        List of test file names (strings)
    """
    files = []
    for item in test_list:
        if isinstance(item, dict):
            files.append(item.get('file', ''))
        elif isinstance(item, str):
            files.append(item)
    return files

def save_to_csv(results, output_path):
    """Save results to CSV with call pattern columns."""
    if not results:
        print("[WARN] No results to save")
        return
    
    columns = [
        'instance_id', 'test_id', 'test_file', 'library', 'focal_class', 'focal_method', 'test_class',
        'v1_passed', 'v2_passed', 'v2_failed', 'bc_detected',
        'library_called', 'library_methods_count', 'library_methods',
        'client_code_called', 'client_methods_count', 'client_methods',
        'focal_class_called', 'focal_method_called',
        'call_pattern', 'test_to_library_direct', 'test_to_client_to_library',
        'error_message', 'stacktrace_depth', 'sample_stacktrace'
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
        
        if bc_detected == 0:
            continue  # Skip instances with no BC detections
        
        lib_called = sum(1 for t in tests if t['bc_detected'] and t['library_called'])
        client_called = sum(1 for t in tests if t['bc_detected'] and t['client_code_called'])
        focal_method_called = sum(1 for t in tests if t['bc_detected'] and t['focal_method_called'])
        both_called = sum(1 for t in tests if t['bc_detected'] and t['library_called'] and t['client_code_called'])
        
        # Call pattern counts
        direct = sum(1 for t in tests if t['bc_detected'] and t['call_pattern'] == 'direct')
        propagated = sum(1 for t in tests if t['bc_detected'] and t['call_pattern'] == 'propagated')
        direct_intermediary = sum(1 for t in tests if t['bc_detected'] and t['call_pattern'] == 'direct_with_intermediary')
        
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
            'focal_method_called_count': focal_method_called,
            'focal_method_called_pct': f"{focal_method_called/bc_detected*100:.1f}%" if bc_detected else '0%',
            'both_called_count': both_called,
            'both_called_pct': f"{both_called/bc_detected*100:.1f}%" if bc_detected else '0%',
            'direct_pattern_count': direct,
            'propagated_pattern_count': propagated,
            'direct_intermediary_count': direct_intermediary
        })
    
    columns = [
        'instance_id', 'library', 'total_tests', 'bc_detected_count', 'bc_detected_pct',
        'library_called_count', 'library_called_pct', 'client_code_called_count', 'client_code_called_pct',
        'focal_method_called_count', 'focal_method_called_pct', 'both_called_count', 'both_called_pct',
        'direct_pattern_count', 'propagated_pattern_count', 'direct_intermediary_count'
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(summary_rows)
    
    print(f"[INFO] Saved {len(summary_rows)} instance summaries to {output_path}")


def generate_pattern_summary(results, output_path):
    """Generate call pattern breakdown summary."""
    bc_detected = [r for r in results if r['bc_detected']]
    
    if not bc_detected:
        print("[WARN] No BC detections for pattern summary")
        return
    
    # Count patterns
    pattern_counts = defaultdict(int)
    for r in bc_detected:
        pattern_counts[r['call_pattern']] += 1
    
    # Create summary rows
    summary_rows = []
    total = len(bc_detected)
    
    for pattern, count in sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True):
        summary_rows.append({
            'call_pattern': pattern,
            'count': count,
            'percentage': f"{count/total*100:.1f}%",
            'description': get_pattern_description(pattern)
        })
    
    # Add total row
    summary_rows.append({
        'call_pattern': 'TOTAL',
        'count': total,
        'percentage': '100.0%',
        'description': 'All BC detections'
    })
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['call_pattern', 'count', 'percentage', 'description'])
        writer.writeheader()
        writer.writerows(summary_rows)
    
    print(f"[INFO] Saved pattern summary to {output_path}")


def get_pattern_description(pattern):
    """Get human-readable description of call pattern."""
    descriptions = {
        'direct': 'Test directly calls OSS library',
        'propagated': 'Test calls client code which calls OSS library',
        'direct_with_intermediary': 'Test calls library through intermediary (not client code)',
        'error_before_execution': 'Library method not found or class loading error (direct call attempted)',  # NEW
        'no_library_call': 'No OSS library call detected in stack',
        'unknown': 'Cannot determine call pattern'
    }
    return descriptions.get(pattern, 'Unknown pattern')


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
    focal_method_called = sum(1 for r in bc_detected if r['focal_method_called'])
    both = sum(1 for r in bc_detected if r['library_called'] and r['client_code_called'])
    
    # Call pattern breakdown
    direct = sum(1 for r in bc_detected if r['call_pattern'] == 'direct')
    propagated = sum(1 for r in bc_detected if r['call_pattern'] == 'propagated')
    direct_intermediary = sum(1 for r in bc_detected if r['call_pattern'] == 'direct_with_intermediary')
    unknown = sum(1 for r in bc_detected if r['call_pattern'] == 'unknown')
    no_lib = sum(1 for r in bc_detected if r['call_pattern'] == 'no_library_call')
    
    print(f"""
{'='*80}
SUMMARY STATISTICS
{'='*80}

Total Tests Analyzed:     {total}
BC Detections (v2 fail):  {total_bc} ({total_bc/total*100:.1f}%)

AMONG BC DETECTIONS:
Library called:           {lib_called:4d} / {total_bc} ({lib_called/total_bc*100:.1f}%)
Client code called:       {client_called:4d} / {total_bc} ({client_called/total_bc*100:.1f}%)
Focal method called:      {focal_method_called:4d} / {total_bc} ({focal_method_called/total_bc*100:.1f}%)
Both lib + client:        {both:4d} / {total_bc} ({both/total_bc*100:.1f}%)

CALL PATTERN ANALYSIS:
Direct (Test->Library):           {direct:4d} / {total_bc} ({direct/total_bc*100:.1f}%)
Propagated (Test->Client->Lib):   {propagated:4d} / {total_bc} ({propagated/total_bc*100:.1f}%)
Direct with intermediary:         {direct_intermediary:4d} / {total_bc} ({direct_intermediary/total_bc*100:.1f}%)
No library call detected:         {no_lib:4d} / {total_bc} ({no_lib/total_bc*100:.1f}%)
Unknown pattern:                  {unknown:4d} / {total_bc} ({unknown/total_bc*100:.1f}%)

KEY INSIGHT:
Tests using PROPAGATED pattern:  {propagated:4d} / {total_bc} ({propagated/total_bc*100:.1f}%)
  (These tests call through client's focal method)

Tests using DIRECT pattern:      {direct + direct_intermediary:4d} / {total_bc} ({(direct + direct_intermediary)/total_bc*100:.1f}%)
  (These tests directly call OSS library)

{'='*80}
""")


def main():
    print("="*80)
    print("STACKTRACE ANALYSIS - DIRECT vs PROPAGATED CALL PATTERN DETECTION")
    print("="*80)
    
    print("\n[1/6] Loading results...")
    pre_data = json.loads(PRE_RESULTS.read_text(encoding='utf-8'))
    breaking_data = json.loads(BREAKING_RESULTS.read_text(encoding='utf-8'))
    
    carry_forward_instances = pre_data['carry_forward_instances']
    carry_forward_tests = pre_data['carry_forward_tests']
    
    print(f"  Instances: {len(carry_forward_instances)}")
    print(f"  Tests: {sum(len(t['passed']) for t in carry_forward_tests.values())}")
    
    print("\n[2/6] Extracting metadata from PROMPT files...")
    print(f"  Prompt root: {PROMPT_ROOT}")
    
    metadata_db = load_metadata_for_tests(carry_forward_instances, carry_forward_tests, PROMPT_ROOT)
    print(f"  Loaded metadata for {len(metadata_db)} tests")
    
    if len(metadata_db) == 0:
        print("\n[ERROR] No metadata loaded! Check PROMPT_ROOT path.")
        return
    
    print("\n[3/6] Analyzing v2 execution logs...")
    results = analyze_all_v2_logs(metadata_db, breaking_data, LOG_DIR)
    print(f"  Analyzed {len(results)} test executions")
    
    print("\n[4/6] Saving results...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    save_to_csv(results, OUTPUT_CSV)
    OUTPUT_JSON.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f"[INFO] Saved JSON to {OUTPUT_JSON}")
    
    print("\n[5/6] Generating summaries...")
    generate_instance_summary(results, SUMMARY_CSV)
    generate_pattern_summary(results, PATTERN_SUMMARY_CSV)
    
    print("\n[6/6] Summary statistics...")
    print_summary_stats(results)
    
    print("\n" + "="*80)
    print("COMPLETE!")
    print("="*80)
    print(f"\nOutput files:")
    print(f"  - Detailed CSV:     {OUTPUT_CSV}")
    print(f"  - Instance Summary: {SUMMARY_CSV}")
    print(f"  - Pattern Summary:  {PATTERN_SUMMARY_CSV}")
    print(f"  - JSON:             {OUTPUT_JSON}")
    print("\nKey columns in detailed CSV:")
    print("  - call_pattern: 'direct', 'propagated', 'direct_with_intermediary', etc.")
    print("  - test_to_library_direct: True if test directly calls library")
    print("  - test_to_client_to_library: True if test calls through client code")


if __name__ == "__main__":
    main()