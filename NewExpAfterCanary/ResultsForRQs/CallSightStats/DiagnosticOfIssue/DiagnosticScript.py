#!/usr/bin/env python3
"""Diagnose why 74.7% of BC detections have unknown call patterns"""

import json
from pathlib import Path
import re

OUTPUT_JSON = Path("/Volumes/Rachna-HD/GPTResults/Exp3BatchResults/bre/RQ3/stacktrace_analysis_RQ3.json")
LOG_DIR = Path("/Volumes/Rachna-HD/GPTResults/Exp3BatchResults/bre/logs")

results = json.loads(OUTPUT_JSON.read_text(encoding='utf-8'))

print("="*80)
print("DIAGNOSING UNKNOWN CALL PATTERNS")
print("="*80)

# Get all BC detections
bc_detections = [r for r in results if r['bc_detected']]
unknown_patterns = [r for r in bc_detections if r['call_pattern'] == 'unknown']

print(f"\nTotal BC detections: {len(bc_detections)}")
print(f"Unknown patterns: {len(unknown_patterns)} ({len(unknown_patterns)/len(bc_detections)*100:.1f}%)")

# Categorize the unknown patterns
categories = {
    'library_called_no_test_in_stack': [],
    'library_and_test_but_no_order': [],
    'library_and_client_but_wrong_order': [],
    'no_library_in_stack': [],
    'empty_stack': []
}

for r in unknown_patterns:
    if r['stacktrace_depth'] == 0:
        categories['empty_stack'].append(r)
    elif not r['library_called']:
        categories['no_library_in_stack'].append(r)
    else:
        # Has library, need to check details
        categories['library_called_no_test_in_stack'].append(r)

print(f"\nCategorization of UNKNOWN patterns:")
for cat, items in categories.items():
    print(f"  {cat}: {len(items)}")

# Analyze a few samples in detail
print(f"\n{'='*80}")
print("DETAILED ANALYSIS OF SAMPLE CASES")
print(f"{'='*80}")

for i, result in enumerate(unknown_patterns[:5]):
    print(f"\n{'─'*80}")
    print(f"SAMPLE #{i+1}: {result['instance_id']} / {result['test_id']}")
    print(f"{'─'*80}")
    
    print(f"\nMetadata:")
    print(f"  Library package: '{result['library']}'")
    print(f"  Focal class: '{result['focal_class']}'")
    print(f"  Focal method: '{result['focal_method']}'")
    print(f"  Test class: '{result['test_class']}'")
    
    print(f"\nDetection Results:")
    print(f"  library_called: {result['library_called']}")
    print(f"  client_code_called: {result['client_code_called']}")
    print(f"  focal_class_called: {result['focal_class_called']}")
    print(f"  focal_method_called: {result['focal_method_called']}")
    print(f"  stacktrace_depth: {result['stacktrace_depth']}")
    
    # Read actual log file to see full stack trace
    log_file = None
    for suffix in ['single', 'multi']:
        candidate = LOG_DIR / f"{result['instance_id']}_{result['test_file']}_breaking_{suffix}.log"
        if candidate.exists():
            log_file = candidate
            break
    
    if log_file:
        print(f"\n  Log file: {log_file.name}")
        log_content = log_file.read_text(encoding='utf-8', errors='ignore')
        
        # Extract full stack trace
        stack_lines = []
        in_stack = False
        for line in log_content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('at '):
                stack_lines.append(stripped)
                in_stack = True
            elif in_stack and not stripped.startswith('at '):
                break  # End of stack trace
        
        print(f"\n  Full stack trace ({len(stack_lines)} frames):")
        for j, line in enumerate(stack_lines[:15]):  # Show first 15 frames
            # Highlight important frames
            markers = []
            if result['library'] and result['library'] in line:
                markers.append('LIBRARY')
            if result['focal_class'] and result['focal_class'] in line:
                markers.append('CLIENT')
            if result['test_class'] and result['test_class'] in line:
                markers.append('TEST')
            
            marker_str = f" ← {', '.join(markers)}" if markers else ""
            print(f"    [{j:2d}] {line[:100]}{marker_str}")
        
        if len(stack_lines) > 15:
            print(f"    ... ({len(stack_lines) - 15} more frames)")
        
        # Check what we're matching against
        print(f"\n  Matching Analysis:")
        test_found = any(result['test_class'] in line for line in stack_lines)
        client_found = any(result['focal_class'] in line for line in stack_lines)
        lib_found = any(result['library'] in line for line in stack_lines)
        
        print(f"    Test class '{result['test_class']}' in stack: {test_found}")
        print(f"    Focal class '{result['focal_class']}' in stack: {client_found}")
        print(f"    Library '{result['library']}' in stack: {lib_found}")
        
        # Show why pattern detection might fail
        if lib_found and not test_found:
            print(f"\n  ⚠️  ISSUE: Library found but test class NOT in stack!")
            print(f"     Looking for test class: '{result['test_class']}'")
            print(f"     Stack contains test frames like:")
            for line in stack_lines:
                if 'Test' in line or 'junit' in line.lower():
                    print(f"       {line[:120]}")
                    break
    else:
        print(f"\n  ⚠️  Log file not found!")

# Summary of issues
print(f"\n{'='*80}")
print("SUMMARY OF POTENTIAL ISSUES")
print(f"{'='*80}")

# Check if focal_class is too specific
focal_class_issues = 0
for r in unknown_patterns[:20]:  # Check first 20
    if r['focal_class'] and '.' in r['focal_class']:
        focal_class_issues += 1

print(f"\n1. Focal class has package name: {focal_class_issues}/20 samples")
print(f"   (May need to match just class name, not FQN)")

# Check if test_class format
test_class_issues = 0
for r in unknown_patterns[:20]:
    if r['test_class'] and not '.' in r['test_class']:
        test_class_issues += 1

print(f"\n2. Test class is simple name: {test_class_issues}/20 samples")
print(f"   (May not match FQN in stack trace)")

# Check library package format
lib_package_issues = 0
for r in unknown_patterns[:20]:
    if r['library'] and not '.' in r['library']:
        lib_package_issues += 1

print(f"\n3. Library is not a package: {lib_package_issues}/20 samples")
print(f"   (May need different matching logic)")

print(f"\n{'='*80}")