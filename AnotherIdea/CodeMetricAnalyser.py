import os
import re
import csv
from pathlib import Path

def clean_code(content):
    """Remove markdown code fence markers from content"""
    # Remove ```java from start and ``` from end
    content = re.sub(r'^```java\s*\n', '', content, flags=re.MULTILINE)
    content = re.sub(r'\n```\s*$', '', content, flags=re.MULTILINE)
    return content.strip()

def count_lines_of_code(content):
    """Count executable lines of code (excluding comments, empty lines, and pure brackets)"""
    lines = content.split('\n')
    loc = 0
    in_block_comment = False
    
    for line in lines:
        stripped = line.strip()
        
        # Skip empty lines
        if not stripped:
            continue
        
        # Handle block comments - check for start
        if '/*' in stripped:
            in_block_comment = True
            # If comment starts and ends on same line, handle it
            if '*/' in stripped:
                in_block_comment = False
                # Check if there's code after the comment on same line
                code_after = stripped.split('*/')[-1].strip()
                if code_after and not code_after.startswith('//'):
                    loc += 1
                continue
            # Only comment start, no code
            continue
        
        # Handle block comment end
        if in_block_comment:
            if '*/' in stripped:
                in_block_comment = False
                # Check if there's code after the comment
                code_after = stripped.split('*/')[-1].strip()
                if code_after and not code_after.startswith('//'):
                    loc += 1
            continue
        
        # Skip single-line comments
        if stripped.startswith('//'):
            continue
        
        # Skip lines that are just opening/closing brackets or semicolons
        if stripped in ['{', '}', '};', ';']:
            continue
        
        # Check for inline comments - count line if there's code before //
        if '//' in stripped:
            code_before = stripped.split('//')[0].strip()
            if code_before and code_before not in ['{', '}', '};', ';']:
                loc += 1
            continue
        
        # This is an executable line
        loc += 1
    
    return loc

def count_test_annotations(content):
    """Count @Test annotations"""
    return len(re.findall(r'@Test\b', content))

def count_imports(content):
    """Count import statements"""
    return len(re.findall(r'^\s*import\s+[\w.]+;', content, re.MULTILINE))

def analyze_file(file_path):
    """Analyze a single Java file and return metrics"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Clean the code
        content = clean_code(content)
        
        metrics = {
            'lines_of_code': count_lines_of_code(content),
            'test_count': count_test_annotations(content),
            'import_count': count_imports(content)
        }
        
        return metrics
    except Exception as e:
        print(f"Error analyzing {file_path}: {e}")
        return None

def analyze_folder(folder_path, model_name):
    """Analyze all .txt files in folder and subfolders"""
    results = []
    folder_path = Path(folder_path)
    
    # Find all .txt files recursively
    txt_files = list(folder_path.rglob('*.txt'))
    
    print(f"\nAnalyzing {model_name}: Found {len(txt_files)} files")
    
    for file_path in txt_files:
        metrics = analyze_file(file_path)
        
        if metrics:
            # Get relative path for better readability
            rel_path = file_path.relative_to(folder_path)
            
            results.append({
                'model': model_name,
                'file_path': str(rel_path),
                'class_name': file_path.stem,  # filename without extension
                'lines_of_code': metrics['lines_of_code'],
                'test_count': metrics['test_count'],
                'import_count': metrics['import_count']
            })
    
    return results

def generate_summary(results):
    """Generate summary statistics per model"""
    summary = {}
    
    for row in results:
        model = row['model']
        if model not in summary:
            summary[model] = {
                'total_files': 0,
                'total_loc': 0,
                'total_tests': 0,
                'total_imports': 0
            }
        
        summary[model]['total_files'] += 1
        summary[model]['total_loc'] += row['lines_of_code']
        summary[model]['total_tests'] += row['test_count']
        summary[model]['total_imports'] += row['import_count']
    
    return summary

def main():
    # ========== CONFIGURATION - EDIT THESE VALUES ==========
    folder1 = "/Volumes/Rachna-HD/FilteredDataset/Exp6LLMOutput/GPT4o"  # Path to first model's folder
    model1_name = "GPT4o"       # Name for first model
    
    folder2 = "/Volumes/Rachna-HD/FilteredDataset/Exp6LLMOutput/Qwen3_480b_cloud"  # Path to second model's folder
    model2_name = "Qwen3_480b_cloud"       # Name for second model
    
    output_file = "/Volumes/Rachna-HD/PerfDiff/code_metricsExp6.csv"  # Output CSV filename
    # ======================================================
    
    # Verify input folders exist
    if not Path(folder1).exists():
        print(f"ERROR: Folder1 does not exist: {folder1}")
        return
    if not Path(folder2).exists():
        print(f"ERROR: Folder2 does not exist: {folder2}")
        return
    
    # Create output directory if it doesn't exist
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Analyze both folders
    all_results = []
    all_results.extend(analyze_folder(folder1, model1_name))
    all_results.extend(analyze_folder(folder2, model2_name))
    
    # Check if any files were found
    if not all_results:
        print("\nERROR: No .txt files found in either folder!")
        print(f"Checked in: {folder1}")
        print(f"Checked in: {folder2}")
        return
    
    # Write detailed results to CSV
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['model', 'file_path', 'class_name', 'lines_of_code', 'test_count', 'import_count']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        writer.writeheader()
        writer.writerows(all_results)
    
    print(f"\n✓ Detailed results saved to {output_file}")
    
    # Generate and save summary
    summary = generate_summary(all_results)
    summary_file = output_file.replace('.csv', '_summary.csv')
    
    with open(summary_file, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['model', 'total_files', 'total_loc', 'total_tests', 'total_imports', 
                      'avg_loc_per_file', 'avg_tests_per_file', 'avg_imports_per_file']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        writer.writeheader()
        for model, stats in summary.items():
            row = {
                'model': model,
                'total_files': stats['total_files'],
                'total_loc': stats['total_loc'],
                'total_tests': stats['total_tests'],
                'total_imports': stats['total_imports'],
                'avg_loc_per_file': round(stats['total_loc'] / stats['total_files'], 2) if stats['total_files'] > 0 else 0,
                'avg_tests_per_file': round(stats['total_tests'] / stats['total_files'], 2) if stats['total_files'] > 0 else 0,
                'avg_imports_per_file': round(stats['total_imports'] / stats['total_files'], 2) if stats['total_files'] > 0 else 0
            }
            writer.writerow(row)
    
    print(f"✓ Summary saved to {summary_file}")
    
    # Print summary to console
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for model, stats in summary.items():
        print(f"\n{model}:")
        print(f"  Files: {stats['total_files']}")
        print(f"  Total LOC: {stats['total_loc']}")
        print(f"  Total @Test: {stats['total_tests']}")
        print(f"  Total Imports: {stats['total_imports']}")
        print(f"  Avg LOC/file: {stats['total_loc'] / stats['total_files']:.2f}")
        print(f"  Avg @Test/file: {stats['total_tests'] / stats['total_files']:.2f}")
        print(f"  Avg Imports/file: {stats['total_imports'] / stats['total_files']:.2f}")
    
    # Calculate and print comparison metrics
    if len(summary) == 2:
        models = list(summary.keys())
        model_a, model_b = models[0], models[1]
        stats_a, stats_b = summary[model_a], summary[model_b]
        
        print("\n" + "="*60)
        print("COMPARISON & STATISTICAL ANALYSIS")
        print("="*60)
        
        # Absolute differences
        print(f"\nAbsolute Differences ({model_a} vs {model_b}):")
        loc_diff = stats_a['total_loc'] - stats_b['total_loc']
        test_diff = stats_a['total_tests'] - stats_b['total_tests']
        import_diff = stats_a['total_imports'] - stats_b['total_imports']
        
        print(f"  Total LOC: {loc_diff:+,}")
        print(f"  Total @Test: {test_diff:+,}")
        print(f"  Total Imports: {import_diff:+,}")
        
        # Percentage differences
        print(f"\nPercentage Differences ({model_a} vs {model_b}):")
        if stats_b['total_loc'] > 0:
            print(f"  Total LOC: {(loc_diff / stats_b['total_loc']) * 100:+.2f}%")
        if stats_b['total_tests'] > 0:
            print(f"  Total @Test: {(test_diff / stats_b['total_tests']) * 100:+.2f}%")
        if stats_b['total_imports'] > 0:
            print(f"  Total Imports: {(import_diff / stats_b['total_imports']) * 100:+.2f}%")
        
        # Per-file averages comparison
        print(f"\nPer-File Averages:")
        avg_loc_a = stats_a['total_loc'] / stats_a['total_files']
        avg_loc_b = stats_b['total_loc'] / stats_b['total_files']
        avg_test_a = stats_a['total_tests'] / stats_a['total_files']
        avg_test_b = stats_b['total_tests'] / stats_b['total_files']
        avg_import_a = stats_a['total_imports'] / stats_a['total_files']
        avg_import_b = stats_b['total_imports'] / stats_b['total_files']
        
        print(f"  {model_a} - LOC: {avg_loc_a:.2f}, @Test: {avg_test_a:.2f}, Imports: {avg_import_a:.2f}")
        print(f"  {model_b} - LOC: {avg_loc_b:.2f}, @Test: {avg_test_b:.2f}, Imports: {avg_import_b:.2f}")
        
        # Key insights for execution time
        print("\n" + "="*60)
        print("KEY INSIGHTS FOR EXECUTION TIME ANALYSIS")
        print("="*60)
        
        print(f"\n1. CODE COMPLEXITY (Total LOC):")
        print(f"   {model_a}: {stats_a['total_loc']:,} lines")
        print(f"   {model_b}: {stats_b['total_loc']:,} lines")
        if loc_diff != 0:
            heavier = model_a if loc_diff > 0 else model_b
            print(f"   → {heavier} has {abs(loc_diff):,} more lines ({abs(loc_diff/max(stats_a['total_loc'], stats_b['total_loc'])*100):.1f}% more code)")
            print(f"   → More code = longer compilation time")
        
        print(f"\n2. TEST EXECUTION LOAD (Total @Test):")
        print(f"   {model_a}: {stats_a['total_tests']:,} tests")
        print(f"   {model_b}: {stats_b['total_tests']:,} tests")
        if test_diff != 0:
            more_tests = model_a if test_diff > 0 else model_b
            print(f"   → {more_tests} has {abs(test_diff):,} more tests ({abs(test_diff/max(stats_a['total_tests'], stats_b['total_tests'])*100):.1f}% more)")
            print(f"   → More tests = significantly longer execution time")
        
        print(f"\n3. DEPENDENCY LOAD (Total Imports):")
        print(f"   {model_a}: {stats_a['total_imports']:,} imports")
        print(f"   {model_b}: {stats_b['total_imports']:,} imports")
        if import_diff != 0:
            more_imports = model_a if import_diff > 0 else model_b
            print(f"   → {more_imports} has {abs(import_diff):,} more imports ({abs(import_diff/max(stats_a['total_imports'], stats_b['total_imports'])*100):.1f}% more)")
            print(f"   → More dependencies = longer class loading time")
        
        # Calculate execution time impact score
        print(f"\n4. ESTIMATED EXECUTION TIME IMPACT:")
        # Weighted scoring: tests have the biggest impact, then LOC, then imports
        score_a = (stats_a['total_tests'] * 3) + (stats_a['total_loc'] * 0.5) + (stats_a['total_imports'] * 1)
        score_b = (stats_b['total_tests'] * 3) + (stats_b['total_loc'] * 0.5) + (stats_b['total_imports'] * 1)
        
        print(f"   Impact Score (higher = slower expected execution):")
        print(f"   {model_a}: {score_a:,.0f}")
        print(f"   {model_b}: {score_b:,.0f}")
        
        if score_a != score_b:
            slower = model_a if score_a > score_b else model_b
            faster = model_b if score_a > score_b else model_a
            ratio = max(score_a, score_b) / min(score_a, score_b)
            print(f"\n   → {slower} is expected to be ~{ratio:.2f}x slower than {faster}")
            print(f"   → Primary factor: ", end="")
            if abs(test_diff) > abs(loc_diff/2) and abs(test_diff) > abs(import_diff):
                print(f"Number of tests ({abs(test_diff)} more tests)")
            elif abs(loc_diff) > abs(import_diff*5):
                print(f"Code volume ({abs(loc_diff):,} more lines)")
            else:
                print(f"Dependencies ({abs(import_diff)} more imports)")
        
        print("\n" + "="*60)

if __name__ == "__main__":
    main()