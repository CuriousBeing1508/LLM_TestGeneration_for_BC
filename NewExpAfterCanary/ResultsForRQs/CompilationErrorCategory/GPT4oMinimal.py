import re
import json
import csv
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import os

class CompilationErrorCategorizer:
    """Categorizes compilation errors from Java test generation"""
    
    def __init__(self):
        # Define error patterns with clear, research-focused names
        self.error_patterns = {
            # SYMBOL NOT FOUND ERRORS - These are potential API hallucinations
            'symbol_class': {
                'pattern': r'cannot find symbol\s+symbol:\s+class\s+(\w+)',
                'priority': 12,
                'requires_context': True,
                'main_category': 'API Hallucination'
            },
            'symbol_method': {
                'pattern': r'cannot find symbol\s+symbol:\s+method\s+(\w+)',
                'priority': 12,
                'requires_context': True,
                'main_category': 'API Hallucination'
            },
            'symbol_variable': {
                'pattern': r'cannot find symbol\s+symbol:\s+variable\s+(\w+)',
                'priority': 11,
                'requires_context': False,
                'main_category': 'API Hallucination'
            },
            'symbol_package': {
                'pattern': r'cannot find symbol\s+symbol:\s+package\s+(\w+)',
                'priority': 11,
                'requires_context': False,
                'main_category': 'Missing/Wrong Imports'
            },
            
            # PACKAGE/IMPORT ERRORS
            'package_not_exist': {
                'pattern': r'package ([\w.]+) does not exist',
                'priority': 13,
                'requires_context': True,
                'main_category': 'Missing/Wrong Imports'
            },
            'static_import_error': {
                'pattern': r'static import only from classes and interfaces',
                'priority': 13,
                'requires_context': False,
                'main_category': 'Missing/Wrong Imports'
            },
            
            # ACCESS VIOLATIONS
            'private_access': {
                'pattern': r'(.*?) has private access in (.*?)$',
                'priority': 10,
                'requires_context': False,
                'main_category': 'Access Violations'
            },
            'protected_access': {
                'pattern': r'(.*?) has protected access in (.*?)$',
                'priority': 10,
                'requires_context': False,
                'main_category': 'Access Violations'
            },
            'not_public_in_package': {
                'pattern': r'(.*?) is not public in (.*?); cannot be accessed from outside package',
                'priority': 10,
                'requires_context': False,
                'main_category': 'Access Violations'
            },
            'cannot_access_class': {
                'pattern': r'cannot access (\w+)',
                'priority': 10,
                'requires_context': False,
                'main_category': 'Access Violations'
            },
            'cannot_instantiate_abstract': {
                'pattern': r'is abstract; cannot be instantiated',
                'priority': 7,
                'requires_context': False,
                'main_category': 'Access Violations'
            },
            
            # SIGNATURE MISMATCH - Real APIs but wrong parameters
            'constructor_signature_mismatch': {
                'pattern': r'no suitable constructor found|constructor .* cannot be applied',
                'priority': 11,
                'requires_context': False,
                'main_category': 'Wrong API Signature'
            },
            'method_signature_mismatch': {
                'pattern': r'method .* cannot be applied to given types',
                'priority': 11,
                'requires_context': False,
                'main_category': 'Wrong API Signature'
            },
            'no_suitable_method': {
                'pattern': r'no suitable method found for',
                'priority': 11,
                'requires_context': False,
                'main_category': 'Wrong API Signature'
            },
            
            # OVERRIDE/IMPLEMENTATION ERRORS
            'method_does_not_override': {
                'pattern': r'method does not override or implement a method from a supertype',
                'priority': 13,
                'requires_context': False,
                'main_category': 'Override/Implementation Errors'
            },
            'not_abstract_missing_override': {
                'pattern': r'(.*?) is not abstract and does not override abstract method (.*?) in (.*?)$',
                'priority': 13,
                'requires_context': False,
                'main_category': 'Override/Implementation Errors'
            },
            
            # TYPE ERRORS
            'type_incompatible': {
                'pattern': r'incompatible types',
                'priority': 8,
                'requires_context': False,
                'main_category': 'Type Errors'
            },
            'type_argument_missing': {
                'pattern': r'requires type argument',
                'priority': 8,
                'requires_context': False,
                'main_category': 'Type Errors'
            },
            
            # TEST FRAMEWORK ERRORS
            'missing_test_framework': {
                'pattern': r'cannot find symbol\s+symbol:\s+class\s+(Test|Before|After|BeforeClass|AfterClass|BeforeEach|AfterEach)',
                'priority': 14,
                'requires_context': False,
                'main_category': 'Test Framework Errors'
            },
            'missing_assertion_method': {
                'pattern': r'cannot find symbol\s+symbol:\s+method\s+(assert\w+|expect\w+)',
                'priority': 14,
                'requires_context': False,
                'main_category': 'Test Framework Errors'
            },
            
            # MOCKITO - Instruction violation
            'mockito_related': {
                'pattern': r'mockito|Mock\w+.*is not abstract and does not override',
                'priority': 15,
                'requires_context': True,
                'main_category': 'Ignored No-Mocking Instruction'
            },
            
            # SYNTAX ERRORS
            'class_interface_enum_expected': {
                'pattern': r'class, interface, or enum expected',
                'priority': 9,
                'requires_context': False,
                'main_category': 'Syntax Errors'
            },
            'interface_expected': {
                'pattern': r'interface expected here',
                'priority': 9,
                'requires_context': False,
                'main_category': 'Syntax Errors'
            },
            'no_interface_expected': {
                'pattern': r'no interface expected here',
                'priority': 9,
                'requires_context': False,
                'main_category': 'Syntax Errors'
            },
            'syntax_missing_delimiter': {
                'pattern': r"';' expected|'\)' expected|'\}' expected|'\(' expected|'\{' expected",
                'priority': 6,
                'requires_context': False,
                'main_category': 'Syntax Errors'
            },
            'syntax_illegal': {
                'pattern': r'illegal character|illegal start of expression',
                'priority': 6,
                'requires_context': False,
                'main_category': 'Syntax Errors'
            },
            'unreachable_statement': {
                'pattern': r'unreachable statement',
                'priority': 6,
                'requires_context': False,
                'main_category': 'Syntax Errors'
            },
            'reached_end_of_file': {
                'pattern': r'reached end of file while parsing',
                'priority': 6,
                'requires_context': False,
                'main_category': 'Syntax Errors'
            },
            
            # OTHER - Merge rare categories here
            'static_context_error': {
                'pattern': r'non-static .* cannot be referenced from static context',
                'priority': 7,
                'requires_context': False,
                'main_category': 'Other'
            },
            'ambiguous_reference': {
                'pattern': r'reference to (.*?) is ambiguous',
                'priority': 9,
                'requires_context': False,
                'main_category': 'Other'
            },
            'cannot_inherit_from_final': {
                'pattern': r'cannot inherit from final (.*?)$',
                'priority': 10,
                'requires_context': False,
                'main_category': 'Other'
            },
            'cannot_assign_final': {
                'pattern': r'cannot assign a value to final variable',
                'priority': 9,
                'requires_context': False,
                'main_category': 'Other'
            },
            'name_clash': {
                'pattern': r'name clash:',
                'priority': 9,
                'requires_context': False,
                'main_category': 'Other'
            },
            'enclosing_instance_required': {
                'pattern': r'an enclosing instance that contains .* is required',
                'priority': 9,
                'requires_context': False,
                'main_category': 'Other'
            },
            'duplicate_variable': {
                'pattern': r'variable (.*?) is already defined',
                'priority': 5,
                'requires_context': False,
                'main_category': 'Other'
            },
        }
        
        # Mockito indicators
        self.mockito_indicators = [
            'mockito', 'Mock', 'mock', 'Spy', 'spy', '@Mock', '@Spy',
            'MockTypeMirror', 'MockProcessingEnvironment', 'MockTypes',
            'when(', 'verify(', 'doReturn(', 'doThrow('
        ]
    
    def get_main_category(self, subcategory: str) -> str:
        """Get main category for a subcategory"""
        if subcategory == 'unknown':
            return 'Other'
        return self.error_patterns.get(subcategory, {}).get('main_category', 'Other')
    
    def extract_test_source(self, log_content: str) -> str:
        """Extract test source code from log if available"""
        source_patterns = [
            r'(?:Test source code:|Generated test:)\s*\n(.*?)(?:\n\n|\Z)',
            r'```java\s*\n(.*?)\n```',
        ]
        
        for pattern in source_patterns:
            match = re.search(pattern, log_content, re.DOTALL)
            if match:
                return match.group(1)
        return ""
    
    def is_mockito_related(self, error_context: str, full_log: str) -> bool:
        """Check if error is related to Mockito"""
        for indicator in self.mockito_indicators:
            if indicator.lower() in error_context.lower() or indicator.lower() in full_log.lower():
                return True
        return False
    
    def is_test_framework_package(self, package_name: str) -> bool:
        """Check if package is a test framework dependency"""
        test_packages = ['junit', 'org.junit', 'testng', 'org.testng', 'hamcrest', 'org.hamcrest']
        return any(pkg in package_name.lower() for pkg in test_packages)
    
    def is_mockito_package(self, package_name: str) -> bool:
        """Check if package is mockito"""
        return 'mockito' in package_name.lower()
    
    def extract_error_blocks(self, log_content: str) -> List[str]:
        """Extract error blocks - handles multi-line errors"""
        # Match error lines and capture up to 3 following lines for context
        error_pattern = r'^(.*?:\d+: error: .*?)$(?:\n  .*?$)*'
        matches = re.findall(error_pattern, log_content, re.MULTILINE)
        
        # If we got matches with context, return them
        if matches:
            return matches
        
        # Fallback: just get single-line errors
        single_line_pattern = r'^.*?:\d+: error: .*$'
        return re.findall(single_line_pattern, log_content, re.MULTILINE)
    
    def categorize_error_block(self, error_block: str, source_code: str, full_log: str) -> Tuple[str, str]:
        """Categorize an error block (may be multi-line)"""
        
        # Get the main error line (first line)
        error_lines = error_block.strip().split('\n')
        main_error = error_lines[0]
        
        # Create context: main error + following lines
        error_context = error_block.strip()
        
        # Sort patterns by priority (highest first)
        sorted_patterns = sorted(
            self.error_patterns.items(),
            key=lambda x: x[1]['priority'],
            reverse=True
        )
        
        for category, config in sorted_patterns:
            pattern = config['pattern']
            # Search in the full error context (multi-line)
            match = re.search(pattern, error_context, re.IGNORECASE | re.DOTALL)
            
            if match:
                # Handle context-dependent categorization
                if config['requires_context']:
                    
                    if category == 'mockito_related':
                        if self.is_mockito_related(error_context, full_log):
                            return 'mockito_related', main_error.strip()
                        continue
                    
                    elif category == 'package_not_exist':
                        package_name = match.group(1)
                        if self.is_mockito_package(package_name):
                            return 'mockito_related', main_error.strip()
                        elif self.is_test_framework_package(package_name):
                            return 'missing_test_framework', main_error.strip()
                        else:
                            return 'package_not_exist', main_error.strip()
                    
                    elif category in ['symbol_class', 'symbol_method']:
                        # For research purposes: if it's a test framework symbol, categorize separately
                        symbol_name = match.group(1)
                        if symbol_name in ['Test', 'Before', 'After', 'BeforeClass', 'AfterClass', 
                                          'BeforeEach', 'AfterEach', 'Mock', 'Spy', 'InjectMocks']:
                            return 'missing_test_framework', main_error.strip()
                        # Otherwise it's likely hallucinated
                        return category, main_error.strip()
                    
                else:
                    # No context needed, direct match
                    return category, main_error.strip()
        
        # No pattern matched
        return 'unknown', main_error.strip()
    
    def parse_log_file(self, log_path: Path) -> Dict:
        """Parse a single compilation log file"""
        
        result = {
            'instance': '',
            'test_name': '',
            'log_path': str(log_path),
            'errors': [],
            'error_counts': defaultdict(int),
            'total_errors': 0,
            'primary_category': '',
            'all_error_lines': [],
            'unique_categories': set()
        }
        
        # Extract instance and test name from filename
        filename = log_path.stem
        filename = filename.replace('_compile', '')
        
        parts = filename.split('_', 1)
        if len(parts) == 2:
            result['instance'] = parts[0]
            result['test_name'] = parts[1]
        
        # Read log file
        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                log_content = f.read()
        except Exception as e:
            result['errors'].append({
                'category': 'ERROR',
                'message': f'Failed to read log: {str(e)}'
            })
            return result
        
        # Extract source code if available
        source_code = self.extract_test_source(log_content)
        
        # Extract error blocks (handles multi-line errors)
        error_blocks = self.extract_error_blocks(log_content)
        
        result['total_errors'] = len(error_blocks)
        
        # Categorize each error
        for error_block in error_blocks:
            category, message = self.categorize_error_block(error_block, source_code, log_content)
            
            result['errors'].append({
                'category': category,
                'message': message
            })
            result['error_counts'][category] += 1
            result['unique_categories'].add(category)
        
        # Store all error lines for reference
        result['all_error_lines'] = [block.split('\n')[0] for block in error_blocks]
        
        # Determine primary category (most frequent)
        if result['error_counts']:
            result['primary_category'] = max(
                result['error_counts'].items(),
                key=lambda x: x[1]
            )[0]
        
        result['unique_categories'] = list(result['unique_categories'])
        
        return result

def process_all_logs(logs_dir: str, compilation_results_json: str, output_csv: str, output_json: str):
    """Process all compilation logs and generate reports"""
    
    categorizer = CompilationErrorCategorizer()
    
    # Load compilation results
    with open(compilation_results_json, 'r') as f:
        compilation_data = json.load(f)
    
    compilation_results = compilation_data.get('compilation_results', {})
    
    # Collect all failed tests
    failed_tests = []
    
    for instance, results in compilation_results.items():
        failed_list = results.get('failed', {})
        
        for test_name, details in failed_list.items():
            failed_tests.append({
                'instance': instance,
                'test_name': test_name,
                'details': details
            })
    
    print(f"Found {len(failed_tests)} failed tests to analyze")
    
    # Process each log file
    all_results = []
    logs_path = Path(logs_dir)
    
    for failed_test in failed_tests:
        instance = failed_test['instance']
        test_name = failed_test['test_name']
        
        log_filename = f"{instance}_{test_name}_compile.log"
        log_path = logs_path / log_filename
        
        if not log_path.exists():
            print(f"WARNING: Log file not found: {log_path}")
            continue
        
        print(f"Processing: {log_filename}")
        result = categorizer.parse_log_file(log_path)
        all_results.append(result)
    
    # Generate summary statistics
    summary = generate_summary(all_results, categorizer)
    
    # Write reports
    write_csv_report(all_results, output_csv)
    write_json_report(all_results, summary, output_json)
    
    print(f"\n✓ Processed {len(all_results)} log files")
    print(f"✓ CSV report written to: {output_csv}")
    print(f"✓ JSON report written to: {output_json}")
    
    return all_results, summary

def generate_summary(results: List[Dict], categorizer) -> Dict:
    """Generate summary statistics"""
    
    total_files = len(results)
    
    summary = {
        'total_test_files_analyzed': total_files,
        'total_errors': sum(r['total_errors'] for r in results),
        'category_distribution': defaultdict(int),
        'primary_category_distribution': defaultdict(int),
        'tests_by_primary_category': defaultdict(list),
        'files_with_category': defaultdict(int),
        'files_with_main_category': defaultdict(set),
        'errors_by_instance': defaultdict(lambda: defaultdict(int)),
        'test_files_by_instance': defaultdict(int)
    }
    
    # Aggregate statistics
    for result in results:
        instance = result['instance']
        
        summary['test_files_by_instance'][instance] += 1
        
        # Count errors by category
        for category, count in result['error_counts'].items():
            summary['category_distribution'][category] += count
            summary['errors_by_instance'][instance][category] += count
        
        # Track which files have which categories
        for category in result['unique_categories']:
            summary['files_with_category'][category] += 1
            main_cat = categorizer.get_main_category(category)
            summary['files_with_main_category'][main_cat].add(f"{instance}_{result['test_name']}")
        
        # Track primary category per file
        if result['primary_category']:
            summary['primary_category_distribution'][result['primary_category']] += 1
            summary['tests_by_primary_category'][result['primary_category']].append(
                f"{result['instance']}_{result['test_name']}"
            )
    
    # Create formatted summary
    formatted_summary = {
        'overview': {
            'total_test_files_analyzed': total_files,
            'total_errors_found': summary['total_errors']
        },
        'test_files_per_instance': dict(sorted(summary['test_files_by_instance'].items())),
        'errors_per_instance': {},
        'overall_error_distribution': {
            'main_categories_files_affected': [],
            'error_categories_files_affected': [],
            'error_category_distribution_all_errors': [],
            'primary_error_category_per_test_file': []
        }
    }
    
    # Format errors per instance
    for instance in sorted(summary['errors_by_instance'].keys()):
        instance_errors = summary['errors_by_instance'][instance]
        total_instance_errors = sum(instance_errors.values())
        top_3 = sorted(instance_errors.items(), key=lambda x: x[1], reverse=True)[:3]
        
        formatted_summary['errors_per_instance'][instance] = {
            'total_errors': total_instance_errors,
            'test_files': summary['test_files_by_instance'][instance],
            'top_3_categories': [
                {'category': cat, 'count': count} for cat, count in top_3
            ]
        }
    
    # Format main categories
    for main_cat, files_set in sorted(summary['files_with_main_category'].items(), 
                                       key=lambda x: len(x[1]), reverse=True):
        file_count = len(files_set)
        percentage = (file_count / total_files * 100) if total_files > 0 else 0
        formatted_summary['overall_error_distribution']['main_categories_files_affected'].append({
            'main_category': main_cat,
            'files_affected': file_count,
            'percentage_of_files': round(percentage, 1)
        })
    
    # Format subcategories
    sorted_files_affected = sorted(
        summary['files_with_category'].items(),
        key=lambda x: x[1],
        reverse=True
    )
    for category, file_count in sorted_files_affected:
        percentage = (file_count / total_files * 100) if total_files > 0 else 0
        total_errors = summary['category_distribution'].get(category, 0)
        formatted_summary['overall_error_distribution']['error_categories_files_affected'].append({
            'category': category,
            'files_affected': file_count,
            'percentage_of_files': round(percentage, 1),
            'total_errors': total_errors
        })
    
    # Format all errors distribution
    sorted_categories = sorted(
        summary['category_distribution'].items(),
        key=lambda x: x[1],
        reverse=True
    )
    for category, count in sorted_categories:
        percentage = (count / summary['total_errors'] * 100) if summary['total_errors'] > 0 else 0
        formatted_summary['overall_error_distribution']['error_category_distribution_all_errors'].append({
            'category': category,
            'count': count,
            'percentage': round(percentage, 1)
        })
    
    # Format primary categories
    sorted_primary = sorted(
        summary['primary_category_distribution'].items(),
        key=lambda x: x[1],
        reverse=True
    )
    for category, count in sorted_primary:
        percentage = (count / total_files * 100) if total_files > 0 else 0
        formatted_summary['overall_error_distribution']['primary_error_category_per_test_file'].append({
            'category': category,
            'test_files': count,
            'percentage': round(percentage, 1)
        })
    
    # Convert for JSON serialization
    summary['category_distribution'] = dict(summary['category_distribution'])
    summary['primary_category_distribution'] = dict(summary['primary_category_distribution'])
    summary['tests_by_primary_category'] = dict(summary['tests_by_primary_category'])
    summary['files_with_category'] = dict(summary['files_with_category'])
    summary['files_with_main_category'] = {k: len(v) for k, v in summary['files_with_main_category'].items()}
    summary['errors_by_instance'] = {k: dict(v) for k, v in summary['errors_by_instance'].items()}
    summary['test_files_by_instance'] = dict(summary['test_files_by_instance'])
    summary['formatted'] = formatted_summary
    
    return summary

def write_csv_report(all_results: List[Dict], output_path: str):
    """Write detailed CSV report"""
    
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    
    # Get all unique categories from results
    all_categories = set()
    for result in all_results:
        all_categories.update(result['error_counts'].keys())
    
    # Base fields
    base_fields = ['instance', 'test_file', 'total_errors', 'primary_category']
    category_fields = sorted(list(all_categories))
    fieldnames = base_fields + category_fields + ['first_error_message']
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for result in all_results:
            row = {
                'instance': result['instance'],
                'test_file': result['test_name'],
                'total_errors': result['total_errors'],
                'primary_category': result['primary_category'],
                'first_error_message': result['errors'][0]['message'] if result['errors'] else ''
            }
            
            # Add counts for each category
            for category in category_fields:
                row[category] = result['error_counts'].get(category, 0)
            
            writer.writerow(row)

def write_json_report(results: List[Dict], summary: Dict, output_path: str):
    """Write detailed JSON report"""
    
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    
    formatted_summary = summary.get('formatted', {})
    
    report = {
        'summary': formatted_summary,
        'raw_data': {
            'category_distribution': summary['category_distribution'],
            'primary_category_distribution': summary['primary_category_distribution'],
            'files_with_category': summary['files_with_category'],
            'files_with_main_category': summary['files_with_main_category'],
            'errors_by_instance': summary['errors_by_instance'],
            'test_files_by_instance': summary['test_files_by_instance'],
            'tests_by_primary_category': summary['tests_by_primary_category']
        },
        'detailed_results': results
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

def print_summary(summary: Dict):
    """Print summary statistics to console"""
    
    formatted = summary.get('formatted', {})
    if not formatted:
        print("ERROR: Formatted summary not found")
        return
    
    print("\n" + "="*80)
    print("COMPILATION ERROR ANALYSIS SUMMARY")
    print("="*80)
    
    overview = formatted.get('overview', {})
    total_files = overview.get('total_test_files_analyzed', 0)
    
    print(f"\nTotal test files analyzed: {total_files}")
    print(f"Total errors found: {overview.get('total_errors_found', 0)}")
    
    print("\n--- Test Files per Instance ---")
    for instance, count in formatted.get('test_files_per_instance', {}).items():
        print(f"{instance}: {count} test files")
    
    print("\n--- Errors per Instance ---")
    for instance, data in formatted.get('errors_per_instance', {}).items():
        print(f"\n{instance}: {data['total_errors']} errors across {data['test_files']} test files")
        for item in data['top_3_categories']:
            print(f"  - {item['category']}: {item['count']}")
    
    print("\n" + "="*80)
    print("OVERALL ERROR DISTRIBUTION")
    print("="*80)
    
    print("\n--- Main Error Categories: Files Affected ---")
    dist = formatted.get('overall_error_distribution', {})
    for item in dist.get('main_categories_files_affected', []):
        print(f"{item['main_category']:40s}: {item['files_affected']:4d} files ({item['percentage_of_files']:5.1f}%)")
    
    print("\n--- Error Subcategories: Files Affected (Top 20) ---")
    for i, item in enumerate(dist.get('error_categories_files_affected', [])[:20], 1):
        print(f"{i:2d}. {item['category']:35s}: {item['files_affected']:4d} files ({item['percentage_of_files']:5.1f}%) - {item['total_errors']:4d} total errors")
    
    print("\n--- Error Category Distribution (All Errors - Top 20) ---")
    for i, item in enumerate(dist.get('error_category_distribution_all_errors', [])[:20], 1):
        print(f"{i:2d}. {item['category']:35s}: {item['count']:4d} ({item['percentage']:5.1f}%)")
    
    print("\n--- Primary Error Category (Per Test File - Top 20) ---")
    for i, item in enumerate(dist.get('primary_error_category_per_test_file', [])[:20], 1):
        print(f"{i:2d}. {item['category']:35s}: {item['test_files']:4d} test files ({item['percentage']:5.1f}%)")

# Main execution
if __name__ == "__main__":
    LOGS_DIR = "/Volumes/Rachna-HD/GPTResults/Exp3BatchResults/pre/logs"
    COMPILATION_JSON = "/Volumes/Rachna-HD/GPTResults/Exp3BatchResults/pre/compile_results_pre.json"
    OUTPUT_CSV = "/Volumes/Rachna-HD/GPTResults/CompilErrorAnalysisResults/compilation_errors_GPT4o_Minimal.csv"
    OUTPUT_JSON = "/Volumes/Rachna-HD/GPTResults/CompilErrorAnalysisResults/compilation_errors_GPT4o_Minimal.json"
    
    results, summary = process_all_logs(
        logs_dir=LOGS_DIR,
        compilation_results_json=COMPILATION_JSON,
        output_csv=OUTPUT_CSV,
        output_json=OUTPUT_JSON
    )
    
    print_summary(summary)