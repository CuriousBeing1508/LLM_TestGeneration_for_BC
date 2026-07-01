import json
import csv
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

class ErrorExtractor:
    """Extracts and compares runtime errors from test execution logs"""
    
    def __init__(self):
        # Common Java exception patterns to extract from logs
        self.exception_patterns = [
            # Direct exception mentions
            r'(?:^|\s)([\w\.]+(?:Exception|Error))(?:\s|:|\(|$)',
            # Caused by pattern
            r'Caused by:\s+([\w\.]+(?:Exception|Error))',
            # In stack traces
            r'^\s*at\s+.*?\(([\w]+(?:Exception|Error))\.java',
            # Test failure messages
            r'(?:throws|threw|raised)\s+([\w\.]+(?:Exception|Error))',
        ]
    
    def extract_bump_errors(self, exception_types_str: str) -> Set[str]:
        """Extract error types from BUMP CSV exception_types column"""
        if not exception_types_str or str(exception_types_str).strip() == '' or exception_types_str == 'nan':
            return set()
        
        # Split by pipe
        errors = str(exception_types_str).split('|')
        cleaned_errors = set()
        
        for error in errors:
            error = error.strip()
            if not error:
                continue
            
            # Skip Maven-specific exceptions (build system errors, not runtime errors)
            if 'MojoFailureException' in error or 'MojoExecutionException' in error:
                continue
            
            # Extract just the exception class name (last part after dot)
            # e.g., java.lang.NoClassDefFoundError -> NoClassDefFoundError
            if '.' in error:
                parts = error.split('.')
                cleaned_errors.add(parts[-1])
            else:
                cleaned_errors.add(error)
        
        return cleaned_errors
    
    def extract_errors_from_log(self, log_path: Path) -> Set[str]:
        """Extract all exception types from a test execution log"""
        if not log_path.exists():
            return set()
        
        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                log_content = f.read()
        except Exception as e:
            print(f"Warning: Could not read {log_path}: {e}")
            return set()
        
        detected_errors = set()
        
        # Apply all exception patterns
        for pattern in self.exception_patterns:
            matches = re.findall(pattern, log_content, re.MULTILINE)
            for match in matches:
                # Clean up the match
                error_name = match.strip()
                
                # Extract just the class name (last part after dot)
                if '.' in error_name:
                    parts = error_name.split('.')
                    error_name = parts[-1]
                
                # Filter out test method names (they often contain "Exception" in the name)
                if error_name.startswith('test_') or error_name.startswith('test'):
                    continue
                
                # Filter out method names that look like tests
                # e.g., "handleCallback_withException" should be skipped
                if '_' in error_name and not error_name.endswith('Exception') and not error_name.endswith('Error'):
                    continue
                
                # Only add if it looks like an exception/error
                if error_name and ('Exception' in error_name or 'Error' in error_name):
                    # Filter out common non-error words and Maven-specific exceptions
                    excluded = ['Error', 'Exception', 'MojoFailureException', 'MojoExecutionException']
                    if error_name not in excluded:
                        detected_errors.add(error_name)
        
        return detected_errors
    
    def match_errors(self, bump_errors: Set[str], llm_errors: Set[str]) -> Dict:
        """Compare BUMP errors with LLM-detected errors"""
        
        # Exact matches
        matched = bump_errors & llm_errors
        
        # BUMP errors not detected by LLM
        missed = bump_errors - llm_errors
        
        # New errors found by LLM (not in BUMP)
        new_errors = llm_errors - bump_errors
        
        return {
            'matched': matched,
            'missed': missed,
            'new': new_errors,
            'match_count': len(matched),
            'miss_count': len(missed),
            'new_count': len(new_errors),
            'bump_total': len(bump_errors),
            'llm_total': len(llm_errors)
        }

def analyze_error_detection(
    llm_results_json: str,
    bump_csv: str,
    llm_logs_dir: str,
    output_csv: str,
    output_json: str
):
    """
    Compare LLM-detected runtime errors with BUMP-identified errors
    ONLY for instances where LLM tests detected breaking changes (have failed tests)
    
    Args:
        llm_results_json: JSON with LLM test results (structure: results -> {instance} -> tests -> failed)
        bump_csv: CSV with BUMP results (with custom_id and exception_types columns)
        llm_logs_dir: Directory containing test execution logs
        output_csv: Output CSV path
        output_json: Output JSON path
    """
    
    extractor = ErrorExtractor()
    
    # Load BUMP data
    print("Loading BUMP data...")
    bump_data = {}
    
    with open(bump_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            instance_id = row['custom_id']
            exception_types = row.get('exception_types', '')
            bump_errors = extractor.extract_bump_errors(exception_types)
            
            bump_data[instance_id] = {
                'exception_types_raw': str(exception_types),
                'parsed_errors': bump_errors,
                'failure_category': row.get('failureCategory', ''),
                'build_status': row.get('build_status', ''),
                'test_failures': int(row.get('test_failures', 0)) if row.get('test_failures', '0').isdigit() else 0,
                'test_errors': int(row.get('test_errors', 0)) if row.get('test_errors', '0').isdigit() else 0
            }
    
    print(f"Loaded {len(bump_data)} BUMP instances")
    
    # Load LLM results
    print("Loading LLM results...")
    with open(llm_results_json, 'r') as f:
        llm_data = json.load(f)
    
    # Navigate to results
    if 'results' in llm_data:
        llm_results = llm_data['results']
    else:
        llm_results = llm_data
    
    # Process each instance
    print("\nAnalyzing instances where LLM detected breaking changes...")
    comparison_results = []
    logs_path = Path(llm_logs_dir)
    
    summary_stats = {
        'total_instances_in_json': 0,
        'instances_with_llm_bc_detection': 0,  # LLM detected BC (has failed tests)
        'instances_analyzed': 0,  # Has both LLM failures AND BUMP data
        'instances_with_matches': 0,
        'instances_with_new_errors': 0,
        'instances_with_only_new_errors': 0,
        'total_bump_errors': 0,
        'total_matched_errors': 0,
        'total_missed_errors': 0,
        'total_new_errors': 0,
        'unique_bump_errors': set(),
        'unique_llm_errors': set(),
        'unique_matched_errors': set(),
        'unique_new_errors': set()
    }
    
    for instance_id, instance_data in llm_results.items():
        summary_stats['total_instances_in_json'] += 1
        
        # Get failed tests from LLM results
        tests_data = instance_data.get('tests', {})
        failed_tests = tests_data.get('failed', [])  # It's a list, not a dict
        
        # ONLY process instances where LLM detected breaking change (has failed tests)
        if not failed_tests:
            continue
        
        summary_stats['instances_with_llm_bc_detection'] += 1
        
        # Skip if not in BUMP data
        if instance_id not in bump_data:
            print(f"Warning: {instance_id} has LLM failures but not found in BUMP CSV")
            continue
        
        # Get BUMP errors for this instance
        bump_errors = bump_data[instance_id]['parsed_errors']
        
        # Track instances analyzed (has both LLM failures and BUMP data)
        summary_stats['instances_analyzed'] += 1
        
        if bump_errors:
            summary_stats['total_bump_errors'] += len(bump_errors)
            summary_stats['unique_bump_errors'].update(bump_errors)
        
        # Extract errors from all failed test logs
        all_llm_errors = set()
        failed_test_logs = []
        
        # Iterate through the list of failed tests
        for failed_test in failed_tests:
            # Each item in the list is a dict with 'file', 'result_type', 'failure_reason'
            test_file = failed_test.get('file', '')
            result_type = failed_test.get('result_type', '')
            
            if not test_file:
                continue
            
            # Skip tests that didn't execute (transplant issues)
            if result_type == 'transplant_issue':
                print(f"  Skipping {instance_id}/{test_file} - Test did not execute")
                continue
            
            # Construct log filename using the pattern: {instance}_{test_file}_breaking_single.log
            # Example: BBC02_BBC02U0Test.java_breaking_single.log
            log_filename = f"{instance_id}_{test_file}_breaking_single.log"
            log_path = Path(llm_logs_dir) / log_filename
            
            if log_path.exists():
                # Extract errors from this log
                llm_errors = extractor.extract_errors_from_log(log_path)
                all_llm_errors.update(llm_errors)
                failed_test_logs.append(log_path.name)
            else:
                print(f"Warning: Log not found: {log_path}")
        
        if all_llm_errors:
            summary_stats['unique_llm_errors'].update(all_llm_errors)
        
        # Match errors
        match_result = extractor.match_errors(bump_errors, all_llm_errors)
        
        # Calculate match rate
        match_rate = 0.0
        if match_result['bump_total'] > 0:
            match_rate = (match_result['match_count'] / match_result['bump_total']) * 100
        
        # Update summary stats
        if match_result['match_count'] > 0:
            summary_stats['instances_with_matches'] += 1
            summary_stats['unique_matched_errors'].update(match_result['matched'])
        
        if match_result['new_count'] > 0:
            summary_stats['instances_with_new_errors'] += 1
            summary_stats['unique_new_errors'].update(match_result['new'])
            
            # Only new errors (no matches but BUMP had errors)
            if match_result['match_count'] == 0 and match_result['bump_total'] > 0:
                summary_stats['instances_with_only_new_errors'] += 1
        
        summary_stats['total_matched_errors'] += match_result['match_count']
        summary_stats['total_missed_errors'] += match_result['miss_count']
        summary_stats['total_new_errors'] += match_result['new_count']
        
        # Store result
        comparison_results.append({
            'instance': instance_id,
            'num_failed_tests': len(failed_tests),
            'failed_test_logs': failed_test_logs,
            'bump_errors': sorted(list(bump_errors)),
            'llm_errors': sorted(list(all_llm_errors)),
            'matched_errors': sorted(list(match_result['matched'])),
            'missed_errors': sorted(list(match_result['missed'])),
            'new_errors': sorted(list(match_result['new'])),
            'match_count': match_result['match_count'],
            'miss_count': match_result['miss_count'],
            'new_count': match_result['new_count'],
            'bump_total': match_result['bump_total'],
            'llm_total': match_result['llm_total'],
            'match_rate': round(match_rate, 1),
            'bump_exception_types_raw': bump_data[instance_id]['exception_types_raw'],
            'bump_failure_category': bump_data[instance_id]['failure_category']
        })
    
    # Calculate overall statistics
    if summary_stats['total_bump_errors'] > 0:
        summary_stats['overall_match_rate'] = round(
            (summary_stats['total_matched_errors'] / summary_stats['total_bump_errors']) * 100, 1
        )
    else:
        summary_stats['overall_match_rate'] = 0.0
    
    # Convert sets to sorted lists for JSON serialization
    summary_stats['unique_bump_errors'] = sorted(list(summary_stats['unique_bump_errors']))
    summary_stats['unique_llm_errors'] = sorted(list(summary_stats['unique_llm_errors']))
    summary_stats['unique_matched_errors'] = sorted(list(summary_stats['unique_matched_errors']))
    summary_stats['unique_new_errors'] = sorted(list(summary_stats['unique_new_errors']))
    
    # Write CSV report
    write_comparison_csv(comparison_results, output_csv)
    
    # Write JSON report
    write_comparison_json(comparison_results, summary_stats, output_json)
    
    # Print summary
    print_comparison_summary(summary_stats, comparison_results)
    
    return comparison_results, summary_stats

def write_comparison_csv(results: List[Dict], output_path: str):
    """Write comparison results to CSV"""
    
    import os
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        fieldnames = [
            'instance', 'num_failed_tests',
            'bump_total', 'llm_total',
            'match_count', 'miss_count', 'new_count', 'match_rate',
            'bump_errors', 'llm_errors',
            'matched_errors', 'missed_errors', 'new_errors',
            'bump_exception_types_raw', 'bump_failure_category',
            'failed_test_logs'
        ]
        
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for result in results:
            row = result.copy()
            # Convert lists to pipe-separated strings for CSV
            for key in ['bump_errors', 'llm_errors', 'matched_errors', 'missed_errors', 'new_errors', 'failed_test_logs']:
                row[key] = '|'.join(row[key]) if row[key] else ''
            writer.writerow(row)
    
    print(f"\n✓ CSV written to: {output_path}")

def write_comparison_json(results: List[Dict], summary: Dict, output_path: str):
    """Write comparison results to JSON"""
    
    import os
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    
    report = {
        'summary': summary,
        'detailed_results': results
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"✓ JSON written to: {output_path}")

def print_comparison_summary(summary: Dict, results: List[Dict]):
    """Print comparison summary"""
    
    print("\n" + "="*80)
    print("LLM VS BUMP ERROR DETECTION COMPARISON")
    print("(Only instances where LLM detected breaking changes)")
    print("="*80)
    
    print(f"\n--- Overview ---")
    print(f"Total instances in LLM results JSON: {summary['total_instances_in_json']}")
    print(f"Instances where LLM detected BC (has failed tests): {summary['instances_with_llm_bc_detection']}")
    print(f"Instances analyzed (LLM failures + BUMP data): {summary['instances_analyzed']}")
    
    print(f"\n--- Research Questions Answered ---")
    print(f"\nQ1: How many instances matched BUMP error types?")
    if summary['instances_analyzed'] > 0:
        match_pct = round(summary['instances_with_matches']/summary['instances_analyzed']*100, 1)
        print(f"    Instances with at least 1 matched error: {summary['instances_with_matches']} / {summary['instances_analyzed']} ({match_pct}%)")
    else:
        print(f"    Instances with at least 1 matched error: {summary['instances_with_matches']} / {summary['instances_analyzed']} (0.0%)")
    print(f"    Total matched errors: {summary['total_matched_errors']} / {summary['total_bump_errors']} ({summary['overall_match_rate']}%)")
    
    print(f"\nQ2: Does LLM identify other errors not reported by BUMP?")
    if summary['instances_analyzed'] > 0:
        new_pct = round(summary['instances_with_new_errors']/summary['instances_analyzed']*100, 1)
        print(f"    Instances with new errors: {summary['instances_with_new_errors']} / {summary['instances_analyzed']} ({new_pct}%)")
    else:
        print(f"    Instances with new errors: {summary['instances_with_new_errors']} / {summary['instances_analyzed']} (0.0%)")
    print(f"    Instances with ONLY new errors (0 matches): {summary['instances_with_only_new_errors']}")
    print(f"    Total new errors found: {summary['total_new_errors']}")
    
    print(f"\nQ3: How many NEW unique error types identified?")
    print(f"    Unique BUMP error types: {len(summary['unique_bump_errors'])}")
    print(f"    Unique LLM error types: {len(summary['unique_llm_errors'])}")
    print(f"    Unique matched error types: {len(summary['unique_matched_errors'])}")
    print(f"    Unique NEW error types (not in BUMP): {len(summary['unique_new_errors'])}")
    
    print(f"\n--- Unique Error Types Details ---")
    print(f"\nBUMP error types ({len(summary['unique_bump_errors'])}):")
    print(f"  {', '.join(summary['unique_bump_errors'])}")
    
    print(f"\nLLM detected error types ({len(summary['unique_llm_errors'])}):")
    print(f"  {', '.join(summary['unique_llm_errors'])}")
    
    print(f"\nMatched error types ({len(summary['unique_matched_errors'])}):")
    print(f"  {', '.join(summary['unique_matched_errors'])}")
    
    print(f"\nNEW error types found by LLM ({len(summary['unique_new_errors'])}):")
    if summary['unique_new_errors']:
        print(f"  {', '.join(summary['unique_new_errors'])}")
    else:
        print(f"  (none)")
    
    print(f"\n--- Top 10 Instances by Match Rate ---")
    sorted_results = sorted(results, key=lambda x: (x['match_rate'], x['match_count']), reverse=True)
    for i, result in enumerate(sorted_results[:10], 1):
        print(f"{i:2d}. {result['instance']:10s}: {result['match_count']}/{result['bump_total']} matched ({result['match_rate']:5.1f}%) | {result['new_count']} new | {result['num_failed_tests']} failed tests")
    
    print(f"\n--- Top 10 Instances with Most New Errors ---")
    new_error_results = sorted([r for r in results if r['new_count'] > 0], key=lambda x: x['new_count'], reverse=True)
    for i, result in enumerate(new_error_results[:10], 1):
        new_errors_str = ', '.join(result['new_errors'][:3])
        if len(result['new_errors']) > 3:
            new_errors_str += f" (+{len(result['new_errors']) - 3} more)"
        print(f"{i:2d}. {result['instance']:10s}: {result['new_count']} new | {new_errors_str}")
    
    print(f"\n--- Top 10 Instances with Most Missed Errors ---")
    missed_error_results = sorted([r for r in results if r['miss_count'] > 0], key=lambda x: x['miss_count'], reverse=True)
    for i, result in enumerate(missed_error_results[:10], 1):
        missed_errors_str = ', '.join(result['missed_errors'][:3])
        if len(result['missed_errors']) > 3:
            missed_errors_str += f" (+{len(result['missed_errors']) - 3} more)"
        print(f"{i:2d}. {result['instance']:10s}: {result['miss_count']} missed | {missed_errors_str}")


# Main execution
if __name__ == "__main__":
    # Configuration
    LLM_RESULTS_JSON = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/transplant_results_breaking_single_module.json"
    BUMP_ERROR_CSV = PRIMARY_DRIVE / "RQResults/RQ4_resultsBUMP.csv"
    LLM_LOGS_DIR = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/logs"
    OUTPUT_CSV = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/RQ4/BREErrorTypes/error_comparison.csv"
    OUTPUT_JSON = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/RQ4/BREErrorTypes/error_comparison.json"
    
    results, summary = analyze_error_detection(
        llm_results_json=LLM_RESULTS_JSON,
        bump_csv=BUMP_ERROR_CSV,
        llm_logs_dir=LLM_LOGS_DIR,
        output_csv=OUTPUT_CSV,
        output_json=OUTPUT_JSON
    )