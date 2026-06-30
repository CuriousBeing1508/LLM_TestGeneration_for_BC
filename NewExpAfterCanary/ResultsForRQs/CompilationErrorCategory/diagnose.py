import re
import json
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

def diagnose_unknown_errors(results: List[Dict], output_file: str = "unknown_errors_analysis.txt"):
    """Analyze unknown errors to find patterns"""
    
    unknown_errors = []
    
    for result in results:
        if result['primary_category'] == 'unknown':
            for error in result['errors']:
                if error['category'] == 'unknown':
                    unknown_errors.append({
                        'instance': result['instance'],
                        'test': result['test_name'],
                        'message': error['message']
                    })
    
    print(f"\nFound {len(unknown_errors)} unknown error messages")
    print("Analyzing patterns...\n")
    
    # Group by error pattern
    pattern_counts = defaultdict(list)
    
    for err in unknown_errors:
        msg = err['message']
        # Extract just the error type (after "error:")
        if 'error:' in msg:
            error_type = msg.split('error:')[1].strip()
            # Take first 100 chars as pattern key
            pattern_key = error_type[:100]
            pattern_counts[pattern_key].append(err)
    
    # Write detailed report
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("="*80 + "\n")
        f.write("UNKNOWN ERROR ANALYSIS\n")
        f.write("="*80 + "\n\n")
        f.write(f"Total unknown errors: {len(unknown_errors)}\n")
        f.write(f"Unique patterns: {len(pattern_counts)}\n\n")
        
        # Sort by frequency
        sorted_patterns = sorted(pattern_counts.items(), key=lambda x: len(x[1]), reverse=True)
        
        f.write("="*80 + "\n")
        f.write("TOP UNKNOWN ERROR PATTERNS (by frequency)\n")
        f.write("="*80 + "\n\n")
        
        for i, (pattern, occurrences) in enumerate(sorted_patterns[:50], 1):
            f.write(f"\n{i}. PATTERN (occurs {len(occurrences)} times):\n")
            f.write("-" * 80 + "\n")
            f.write(f"{pattern}\n")
            f.write("-" * 80 + "\n")
            f.write("Examples:\n")
            for example in occurrences[:3]:  # Show 3 examples
                f.write(f"  {example['instance']}_{example['test']}\n")
                f.write(f"  {example['message']}\n\n")
    
    print(f"✓ Unknown error analysis written to: {output_file}")
    
    # Also print top 10 to console
    print("\nTOP 10 UNKNOWN ERROR PATTERNS:\n")
    for i, (pattern, occurrences) in enumerate(sorted_patterns[:10], 1):
        print(f"{i}. ({len(occurrences)} occurrences)")
        print(f"   {pattern[:120]}...")
        print()
    
    return pattern_counts

def load_results_from_json(json_path: str) -> List[Dict]:
    """Load results from the JSON report"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    return data.get('detailed_results', [])

if __name__ == "__main__":
    # Path to your existing JSON results
    RESULTS_JSON = PRIMARY_DRIVE / "GPTResults/CompilErrorAnalysisResults/compilation_errors_GPT4o_Class.json"
    UNKNOWN_ANALYSIS = PRIMARY_DRIVE / "GPTResults/CompilErrorAnalysisResults/unknown_errors_analysis.txt"
    
    print("Loading results from JSON...")
    results = load_results_from_json(RESULTS_JSON)
    print(f"Loaded {len(results)} test results")
    
    print("\n" + "="*80)
    print("DIAGNOSING UNKNOWN ERRORS")
    print("="*80)
    
    diagnose_unknown_errors(results, UNKNOWN_ANALYSIS)