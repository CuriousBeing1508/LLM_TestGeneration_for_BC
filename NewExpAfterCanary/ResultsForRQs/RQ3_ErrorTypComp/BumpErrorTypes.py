#!/usr/bin/env python3
"""
BUMP Breaking Changes - Docker Executor and Log Parser
Executes Docker containers and extracts all error information for manual analysis.
"""

import pandas as pd
import subprocess
import json
import re
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set
import time

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

class LogParser:
    """Parse Maven/Java test logs and extract all error information."""
    
    def __init__(self, log_text: str):
        self.log = log_text
        self.errors_found = []
        
    def parse(self) -> Dict:
        """Parse log and extract all error information."""
        return {
            'all_exceptions': self.extract_exceptions(),
            'all_errors': self.extract_errors(),
            'failed_tests': self.extract_failed_tests(),
            'compilation_failures': self.extract_compilation_failures(),
            'error_messages': self.extract_error_messages(),
            'stack_traces': self.extract_stack_traces(),
            'maven_errors': self.extract_maven_errors(),
            'test_summary': self.extract_test_summary(),
        }
    
    def extract_exceptions(self) -> List[Dict]:
        """Extract all Java exceptions from the log."""
        exceptions = []
        
        # Pattern for Java exceptions
        pattern = r'([\w.]+(?:Exception|Error))(?::\s*(.+?))?(?=\n|\r|$)'
        
        for match in re.finditer(pattern, self.log):
            exception_type = match.group(1)
            exception_message = match.group(2).strip() if match.group(2) else ""
            
            # Get line number
            line_num = self.log[:match.start()].count('\n') + 1
            
            # Get context (50 chars before and after)
            start = max(0, match.start() - 50)
            end = min(len(self.log), match.end() + 100)
            context = self.log[start:end].replace('\n', ' ').strip()
            
            exceptions.append({
                'type': exception_type,
                'message': exception_message[:200],
                'line': line_num,
                'context': context[:200]
            })
        
        return exceptions
    
    def extract_errors(self) -> List[str]:
        """Extract all [ERROR] lines from Maven output."""
        errors = []
        pattern = r'\[ERROR\]\s*(.+?)(?=\n|$)'
        
        for match in re.finditer(pattern, self.log, re.MULTILINE):
            error_text = match.group(1).strip()
            if error_text and len(error_text) > 5:  # Skip trivial errors
                errors.append(error_text[:300])
        
        return list(dict.fromkeys(errors))  # Remove duplicates while preserving order
    
    def extract_failed_tests(self) -> List[Dict]:
        """Extract information about failed tests."""
        failed_tests = []
        
        # Pattern 1: JUnit format - TestName(ClassName)
        pattern1 = r'(\w+)\(([\w.]+)\)\s+Time elapsed:.*?<<<\s*(FAILURE|ERROR)!'
        for match in re.finditer(pattern1, self.log):
            failed_tests.append({
                'test_method': match.group(1),
                'test_class': match.group(2),
                'failure_type': match.group(3),
                'format': 'junit'
            })
        
        # Pattern 2: Maven format - package.Class.method
        pattern2 = r'Failed tests?:\s+([\w.]+\.[\w.]+)'
        for match in re.finditer(pattern2, self.log):
            full_name = match.group(1)
            parts = full_name.rsplit('.', 1)
            failed_tests.append({
                'test_method': parts[1] if len(parts) > 1 else full_name,
                'test_class': parts[0] if len(parts) > 1 else 'Unknown',
                'failure_type': 'FAILURE',
                'format': 'maven'
            })
        
        # Pattern 3: Errors in tests
        pattern3 = r'Errors?:\s+([\w.]+\.[\w.]+)'
        for match in re.finditer(pattern3, self.log):
            full_name = match.group(1)
            parts = full_name.rsplit('.', 1)
            failed_tests.append({
                'test_method': parts[1] if len(parts) > 1 else full_name,
                'test_class': parts[0] if len(parts) > 1 else 'Unknown',
                'failure_type': 'ERROR',
                'format': 'maven'
            })
        
        return failed_tests
    
    def extract_compilation_failures(self) -> List[Dict]:
        """Extract compilation errors."""
        compilation_errors = []
        
        # Look for compilation error blocks
        if re.search(r'COMPILATION ERROR', self.log, re.IGNORECASE):
            # Extract file and line number errors
            pattern = r'\[ERROR\]\s*([\w/\\.]+\.java):\[(\d+),(\d+)\]\s*(.+?)(?=\n|$)'
            for match in re.finditer(pattern, self.log):
                compilation_errors.append({
                    'file': match.group(1),
                    'line': match.group(2),
                    'column': match.group(3),
                    'error': match.group(4).strip()[:200]
                })
        
        return compilation_errors
    
    def extract_error_messages(self) -> List[str]:
        """Extract readable error messages."""
        messages = []
        
        # Pattern for assertion errors with expected/actual
        pattern1 = r'expected:\s*<?(.+?)>?\s*but was:\s*<?(.+?)>?'
        for match in re.finditer(pattern1, self.log, re.IGNORECASE):
            messages.append(f"Expected: {match.group(1)}, but was: {match.group(2)}")
        
        # Pattern for general error messages after exception types
        pattern2 = r'(?:Exception|Error):\s*([^\n]{20,200})'
        for match in re.finditer(pattern2, self.log):
            msg = match.group(1).strip()
            if msg not in str(messages):  # Avoid duplicates
                messages.append(msg)
        
        return messages[:10]  # Limit to first 10 distinct messages
    
    def extract_stack_traces(self) -> List[str]:
        """Extract stack traces."""
        stack_traces = []
        
        # Pattern for stack trace blocks
        pattern = r'((?:[\w.]+(?:Exception|Error)[^\n]*(?:\n\s+at\s+[\w.$<>]+\([^\)]+\))+))'
        
        matches = re.finditer(pattern, self.log, re.MULTILINE)
        for match in matches:
            trace = match.group(1)
            # Limit trace to first 5 lines
            lines = trace.split('\n')[:6]
            stack_traces.append('\n'.join(lines))
        
        return stack_traces[:3]  # Return first 3 stack traces
    
    def extract_maven_errors(self) -> Dict:
        """Extract Maven-specific error information."""
        maven_info = {
            'build_failure': bool(re.search(r'BUILD FAILURE', self.log)),
            'build_success': bool(re.search(r'BUILD SUCCESS', self.log)),
            'reactor_summary': None,
            'failure_message': None
        }
        
        # Extract failure message
        failure_pattern = r'(?:Failure|Error)\s*message:\s*(.+?)(?=\n|$)'
        match = re.search(failure_pattern, self.log, re.IGNORECASE)
        if match:
            maven_info['failure_message'] = match.group(1).strip()
        
        return maven_info
    
    def extract_test_summary(self) -> Dict:
        """Extract test execution summary."""
        summary = {
            'tests_run': 0,
            'failures': 0,
            'errors': 0,
            'skipped': 0
        }
        
        # Pattern: Tests run: 5, Failures: 1, Errors: 0, Skipped: 0
        pattern = r'Tests run:\s*(\d+).*?Failures:\s*(\d+).*?Errors:\s*(\d+).*?Skipped:\s*(\d+)'
        match = re.search(pattern, self.log)
        
        if match:
            summary['tests_run'] = int(match.group(1))
            summary['failures'] = int(match.group(2))
            summary['errors'] = int(match.group(3))
            summary['skipped'] = int(match.group(4))
        
        return summary


class BUMPExecutor:
    """Execute BUMP Docker containers and parse logs."""
    
    def __init__(self, input_csv: str, output_csv: str, timeout: int = 600, 
                 log_dir: str = 'logs', parsed_dir: str = 'parsed_errors'):
        self.input_csv = input_csv
        self.output_csv = output_csv
        self.timeout = timeout
        self.log_dir = Path(log_dir)
        self.parsed_dir = Path(parsed_dir)
        self.results = []
        
        # Create directories (including parent directories)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.parsed_dir.mkdir(parents=True, exist_ok=True)
        
    def run(self):
        """Main execution."""
        print("="*80)
        print("BUMP Docker Executor and Log Parser")
        print("="*80)
        
        df = pd.read_csv(self.input_csv)
        print(f"✓ Loaded {len(df)} records\n")
        
        # Filter executable records
        executable = df[df['docker_image_breaking'].notna()]
        print(f"✓ Found {len(executable)} records with Docker images\n")
        
        # Process each
        for idx, row in executable.iterrows():
            print(f"[{idx + 1}/{len(executable)}] Processing {row['custom_id']}...")
            result = self.process_record(row)
            if result:
                self.results.append(result)
                self.save_progress()
        
        self.finalize()
    
    def process_record(self, row: pd.Series) -> Dict:
        """Process a single record."""
        custom_id = row['custom_id']
        docker_image = row['docker_image_breaking']
        
        print(f"  Docker: {docker_image}")
        
        # Execute
        exec_result = self.execute_docker(docker_image)
        
        # Save raw log
        log_file = self.log_dir / f"{custom_id}.log"
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(exec_result['full_log'])
        print(f"  ✓ Log saved: {log_file}")
        
        # Parse log
        parser = LogParser(exec_result['full_log'])
        parsed = parser.parse()
        
        # Save parsed errors
        error_file = self.parsed_dir / f"{custom_id}.json"
        with open(error_file, 'w', encoding='utf-8') as f:
            json.dump(parsed, f, indent=2, default=str)
        print(f"  ✓ Parsed errors: {error_file}")
        
        # Build result
        result = self.build_result(row, exec_result, parsed, log_file, error_file)
        
        # Print summary
        self.print_result_summary(result)
        print()
        
        return result
    
    def execute_docker(self, image: str) -> Dict:
        """Execute Docker container."""
        start = time.time()
        
        try:
            result = subprocess.run(
                ['docker', 'run', '--rm', image],
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            return {
                'success': result.returncode == 0,
                'return_code': result.returncode,
                'full_log': f"{result.stdout}\n{result.stderr}",
                'execution_time': time.time() - start,
            }
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'return_code': -1,
                'full_log': f"TIMEOUT after {self.timeout}s",
                'execution_time': self.timeout,
            }
        except Exception as e:
            return {
                'success': False,
                'return_code': -1,
                'full_log': f"ERROR: {str(e)}",
                'execution_time': time.time() - start,
            }
    
    def build_result(self, row: pd.Series, exec_result: Dict, parsed: Dict, 
                     log_file: Path, error_file: Path) -> Dict:
        """Build result dictionary."""
        
        # Extract unique exception types
        exception_types = list(set([e['type'] for e in parsed['all_exceptions']]))
        
        # Get first error message
        first_error = ""
        if parsed['error_messages']:
            first_error = parsed['error_messages'][0]
        elif parsed['all_errors']:
            first_error = parsed['all_errors'][0]
        
        # Count different error categories
        num_exceptions = len(parsed['all_exceptions'])
        num_failed_tests = len(parsed['failed_tests'])
        num_compilation_errors = len(parsed['compilation_failures'])
        
        return {
            # Original fields
            'custom_id': row['custom_id'],
            'clientGithubURL': row['clientGithubURL'],
            'clientProject': row['clientProject'],
            'clientProjectOrganisation': row['clientProjectOrganisation'],
            'breakingCommit': row['breakingCommit'],
            'dependencyGroupID': row['dependencyGroupID'],
            'dependencyArtifactID': row['dependencyArtifactID'],
            'previousVersion': row['previousVersion'],
            'newVersion': row['newVersion'],
            'failureCategory': row['failureCategory'],
            'docker_image_breaking': row['docker_image_breaking'],
            
            # Execution info
            'execution_timestamp': datetime.now().isoformat(),
            'execution_success': exec_result['success'],
            'return_code': exec_result['return_code'],
            'execution_time_seconds': round(exec_result['execution_time'], 2),
            
            # Test summary
            'tests_run': parsed['test_summary']['tests_run'],
            'test_failures': parsed['test_summary']['failures'],
            'test_errors': parsed['test_summary']['errors'],
            'test_skipped': parsed['test_summary']['skipped'],
            
            # Error counts
            'num_exceptions': num_exceptions,
            'num_failed_tests': num_failed_tests,
            'num_compilation_errors': num_compilation_errors,
            'num_error_messages': len(parsed['error_messages']),
            
            # Error details
            'exception_types': '|'.join(exception_types) if exception_types else 'NONE',
            'first_error_message': first_error[:500],
            'all_maven_errors': '|'.join(parsed['all_errors'][:5]),
            'build_status': 'FAILURE' if parsed['maven_errors']['build_failure'] else 'SUCCESS',
            
            # For manual categorization
            'error_category': '',  # EMPTY - for manual analysis
            'notes': '',  # EMPTY - for manual notes
            
            # File references
            'log_file': str(log_file),
            'parsed_errors_file': str(error_file),
        }
    
    def print_result_summary(self, result: Dict):
        """Print summary of result."""
        print(f"  Status: {'✓ SUCCESS' if result['execution_success'] else '✗ FAILURE'}")
        print(f"  Return code: {result['return_code']}")
        print(f"  Execution time: {result['execution_time_seconds']}s")
        print(f"  Tests: {result['tests_run']} run, {result['test_failures']} failures, {result['test_errors']} errors")
        print(f"  Exceptions found: {result['num_exceptions']}")
        print(f"  Failed tests: {result['num_failed_tests']}")
        if result['exception_types'] != 'NONE':
            print(f"  Exception types: {result['exception_types']}")
    
    def save_progress(self):
        """Save progress to CSV."""
        if self.results:
            df = pd.DataFrame(self.results)
            df.to_csv(self.output_csv, index=False)
    
    def finalize(self):
        """Finalize and print summary."""
        if not self.results:
            print("\n⚠ No results to save")
            return
        
        df = pd.DataFrame(self.results)
        df = df.sort_values('custom_id')
        df.to_csv(self.output_csv, index=False)
        
        print("\n" + "="*80)
        print("EXECUTION COMPLETE")
        print("="*80)
        print(f"✓ Total processed: {len(df)}")
        print(f"✓ Results CSV: {self.output_csv}")
        print(f"✓ Raw logs: {self.log_dir}/")
        print(f"✓ Parsed errors: {self.parsed_dir}/")
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        print(f"Successful executions: {df['execution_success'].sum()}")
        print(f"Failed executions: {(~df['execution_success']).sum()}")
        print(f"Total test failures: {df['test_failures'].sum()}")
        print(f"Total test errors: {df['test_errors'].sum()}")
        print(f"Total exceptions found: {df['num_exceptions'].sum()}")
        print("\nTop exception types:")
        
        # Count all exception types
        all_exceptions = []
        for types in df['exception_types']:
            if types != 'NONE':
                all_exceptions.extend(types.split('|'))
        
        from collections import Counter
        exception_counts = Counter(all_exceptions)
        for exc_type, count in exception_counts.most_common(10):
            print(f"  {exc_type}: {count}")
        
        print("\n" + "="*80)
        print("NEXT STEPS:")
        print("="*80)
        print("1. Open the output CSV and manually categorize errors in 'error_category' column")
        print("2. Add notes in 'notes' column for interesting cases")
        print(f"3. Review {self.parsed_dir}/*.json for detailed error information")
        print(f"4. Check {self.log_dir}/*.log for full execution logs")
        print("="*80 + "\n")


def main():
    # ============================================================================
    # CONFIGURATION - Set your input/output paths here
    # ============================================================================
    DEFAULT_INPUT_CSV = PRIMARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"  
    DEFAULT_OUTPUT_CSV = PRIMARY_DRIVE / "RQResults/RQ4_results.csv"           
    DEFAULT_LOG_DIR = PRIMARY_DRIVE / "RQResults/RQ4/BumpExecutionlogs" 
    DEFAULT_PARSED_DIR = PRIMARY_DRIVE / "RQResults/RQ4/parsed_errors"
    DEFAULT_TIMEOUT = 600   
    # ============================================================================
    
    parser = argparse.ArgumentParser(
        description='Execute BUMP Docker containers and parse error logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bump_executor.py --input breaking_updates.csv --output results.csv
  python bump_executor.py -i data.csv -o output.csv --timeout 900
  python bump_executor.py --log-dir my_logs --parsed-dir my_errors
  python bump_executor.py  # Uses default paths set in script
        """
    )
    parser.add_argument('--input', '-i', default=DEFAULT_INPUT_CSV, 
                       help=f'Input CSV file (default: {DEFAULT_INPUT_CSV})')
    parser.add_argument('--output', '-o', default=DEFAULT_OUTPUT_CSV, 
                       help=f'Output CSV file (default: {DEFAULT_OUTPUT_CSV})')
    parser.add_argument('--log-dir', default=DEFAULT_LOG_DIR,
                       help=f'Directory for raw log files (default: {DEFAULT_LOG_DIR})')
    parser.add_argument('--parsed-dir', default=DEFAULT_PARSED_DIR,
                       help=f'Directory for parsed error JSON files (default: {DEFAULT_PARSED_DIR})')
    parser.add_argument('--timeout', '-t', type=int, default=DEFAULT_TIMEOUT, 
                       help=f'Docker timeout in seconds (default: {DEFAULT_TIMEOUT})')
    
    args = parser.parse_args()
    
    # Validate input file
    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' not found")
        return 1
    
    executor = BUMPExecutor(
        args.input, 
        args.output, 
        args.timeout,
        args.log_dir,
        args.parsed_dir
    )
    executor.run()
    
    return 0


if __name__ == '__main__':
    exit(main())