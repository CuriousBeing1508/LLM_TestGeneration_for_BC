#!/usr/bin/env python3
"""
Master script to run all phases of PRE stage testing automatically.
Executes Phase 1 (Compilation), Phase 2 (Execution), and Phase 3 (Merge) in sequence.
"""

import subprocess
import sys
import json
from pathlib import Path
from datetime import datetime
import time

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE

# === CONFIG ===
COMPILE_SCRIPT = "Phase1Compilation.py"
EXECUTE_SCRIPT = "Phase2Execution.py"
MERGE_SCRIPT = "Phase3MergeResults.py"

COMPILE_OUTPUT = PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/compile_results_pre.json"
EXECUTE_OUTPUT = PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/execute_results_pre.json"
FINAL_OUTPUT = PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/transplant_results_final_pre.json"

MASTER_LOG = PRIMARY_DRIVE / "GPTResults/Exp7BatchResultsOp2/pre/master_execution_log.txt"


def log_message(msg, also_print=True):
    """Log message to file and optionally print to console."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {msg}"
    
    if also_print:
        print(log_line)
    
    MASTER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(MASTER_LOG, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")


def run_phase(phase_num, script_name, description):
    """
    Run a phase script and return success status.
    Returns: (success: bool, duration: float)
    """
    log_message(f"\n{'='*80}")
    log_message(f"PHASE {phase_num}: {description}")
    log_message(f"Script: {script_name}")
    log_message(f"{'='*80}\n")
    
    start_time = time.time()
    
    try:
        # Run the script as a subprocess
        result = subprocess.run(
            [sys.executable, script_name],
            capture_output=True,
            text=True,
            check=False  # Don't raise exception on non-zero exit
        )
        
        duration = time.time() - start_time
        
        # Log the output
        log_message(f"\n--- STDOUT from {script_name} ---", also_print=False)
        log_message(result.stdout, also_print=False)
        
        if result.stderr:
            log_message(f"\n--- STDERR from {script_name} ---", also_print=False)
            log_message(result.stderr, also_print=False)
        
        # Check if successful
        if result.returncode == 0:
            log_message(f" Phase {phase_num} completed successfully in {duration/60:.2f} minutes")
            # Also print the output to console
            print(result.stdout)
            return True, duration
        else:
            log_message(f" Phase {phase_num} FAILED with return code {result.returncode}")
            log_message(f"Duration before failure: {duration/60:.2f} minutes")
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            return False, duration
            
    except FileNotFoundError:
        log_message(f" ERROR: Script not found: {script_name}")
        return False, 0.0
    except Exception as e:
        duration = time.time() - start_time
        log_message(f" EXCEPTION in Phase {phase_num}: {e}")
        log_message(f"Duration before exception: {duration/60:.2f} minutes")
        return False, duration


def load_json_safe(path):
    """Safely load JSON file, return None if doesn't exist or invalid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log_message(f"Warning: Could not load {path}: {e}")
        return None


def print_summary():
    """Print comprehensive summary of all phases."""
    log_message(f"\n{'='*80}")
    log_message(f"FINAL SUMMARY - ALL PHASES COMPLETE")
    log_message(f"{'='*80}\n")
    
    # Load final results
    final_data = load_json_safe(FINAL_OUTPUT)
    
    if not final_data:
        log_message("ERROR: Could not load final results")
        return
    
    summary = final_data.get("global_summary", {})
    
    # Print comprehensive results
    log_message("CLASS TEST FILES:")
    log_message(f"  Files Generated:           {summary.get('total_files_generated', 0)}")
    log_message(f"  Files Compiled:            {summary.get('total_files_compiled', 0)} "
                f"({summary.get('compilation_success_rate', 'N/A')})")
    log_message(f"  Files Failed Compilation:  {summary.get('total_files_failed_compilation', 0)}")
    log_message(f"")
    log_message(f"  Files Executed:            {summary.get('total_files_executed', 0)}")
    log_message(f"  Files PASSED on PRE:       {summary.get('total_files_passed_on_pre', 0)} "
                f"({summary.get('execution_success_rate', 'N/A')} of compiled)")
    log_message(f"  Files FAILED on PRE:       {summary.get('total_files_failed_on_pre', 0)}")
    log_message(f"")
    log_message(f"@TEST METHODS:")
    log_message(f"  @Test in generated files:  {summary.get('total_tests_in_generated_files', 0)}")
    log_message(f"  @Test in compiled files:   {summary.get('total_tests_in_compiled_files', 0)}")
    log_message(f"")
    log_message(f"OVERALL METRICS:")
    log_message(f"  Compilation Success Rate:  {summary.get('compilation_success_rate', 'N/A')}")
    log_message(f"  Execution Success Rate:    {summary.get('execution_success_rate', 'N/A')}")
    log_message(f"  Overall Success Rate:      {summary.get('overall_success_rate', 'N/A')}")
    log_message(f"")
    log_message(f"INSTANCES:")
    log_message(f"  With passing tests:        {summary.get('instances_with_passing_tests', 0)}")
    log_message(f"")
    log_message(f"OUTPUT FILES:")
    log_message(f"  Compilation Results:       {COMPILE_OUTPUT}")
    log_message(f"  Execution Results:         {EXECUTE_OUTPUT}")
    log_message(f"  Final Merged Results:      {FINAL_OUTPUT}")
    log_message(f"  Master Log:                {MASTER_LOG}")
    log_message(f"")
    log_message(f"{'='*80}\n")


def main():
    """Main execution function."""
    overall_start = time.time()
    
    # Initialize log file
    MASTER_LOG.parent.mkdir(parents=True, exist_ok=True)
    if MASTER_LOG.exists():
        MASTER_LOG.unlink()  # Clear previous log
    
    log_message("="*80)
    log_message("AUTOMATED PRE STAGE TESTING - ALL PHASES")
    log_message(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_message("="*80)
    
    # Track phase durations
    phase_durations = {}
    
    # PHASE 1: Compilation
    success, duration = run_phase(1, COMPILE_SCRIPT, "COMPILATION")
    phase_durations["Phase 1 (Compilation)"] = duration
    
    if not success:
        log_message("\n✗ ABORTED: Phase 1 (Compilation) failed. Cannot proceed to Phase 2.")
        log_message(f"Please check the logs and fix compilation issues.")
        log_message(f"Master log: {MASTER_LOG}")
        sys.exit(1)
    
    # Check if compilation produced results
    if not COMPILE_OUTPUT.exists():
        log_message(f"\n ABORTED: Compilation output not found: {COMPILE_OUTPUT}")
        sys.exit(1)
    
    compile_data = load_json_safe(COMPILE_OUTPUT)
    if not compile_data:
        log_message("\n ABORTED: Could not load compilation results")
        sys.exit(1)
    
    compile_summary = compile_data.get("summary", {})
    compiled_count = compile_summary.get("total_files_compiled", 0)
    
    log_message(f"\n[INFO] Compilation complete: {compiled_count} files compiled successfully")
    
    if compiled_count == 0:
        log_message("\n⚠ WARNING: No files compiled successfully. Skipping execution phase.")
        log_message("You may want to review compilation errors before proceeding.")
        
        # Still run merge to generate final report
        success, duration = run_phase(3, MERGE_SCRIPT, "MERGE RESULTS")
        phase_durations["Phase 3 (Merge)"] = duration
        
        if success:
            print_summary()
        
        overall_duration = time.time() - overall_start
        log_message(f"\n{'='*80}")
        log_message(f"EXECUTION COMPLETE (with warnings)")
        log_message(f"Total Duration: {overall_duration/60:.2f} minutes ({overall_duration/3600:.2f} hours)")
        log_message(f"{'='*80}\n")
        sys.exit(0)
    
    # PHASE 2: Execution
    success, duration = run_phase(2, EXECUTE_SCRIPT, "EXECUTION")
    phase_durations["Phase 2 (Execution)"] = duration
    
    if not success:
        log_message("\n ABORTED: Phase 2 (Execution) failed. Cannot proceed to merge.")
        log_message(f"Please check the logs and fix execution issues.")
        log_message(f"Master log: {MASTER_LOG}")
        sys.exit(1)
    
    # Check if execution produced results
    if not EXECUTE_OUTPUT.exists():
        log_message(f"\n ABORTED: Execution output not found: {EXECUTE_OUTPUT}")
        sys.exit(1)
    
    # PHASE 3: Merge Results
    success, duration = run_phase(3, MERGE_SCRIPT, "MERGE RESULTS")
    phase_durations["Phase 3 (Merge)"] = duration
    
    if not success:
        log_message("\n WARNING: Phase 3 (Merge) failed, but compilation and execution completed.")
        log_message(f"You can manually inspect: {COMPILE_OUTPUT} and {EXECUTE_OUTPUT}")
        sys.exit(1)
    
    # Print final summary
    print_summary()
    
    # Print phase breakdown
    overall_duration = time.time() - overall_start
    log_message("\nPHASE DURATION BREAKDOWN:")
    for phase, dur in phase_durations.items():
        log_message(f"  {phase}: {dur/60:.2f} minutes ({dur/3600:.2f} hours)")
    log_message(f"  Total: {overall_duration/60:.2f} minutes ({overall_duration/3600:.2f} hours)")
    
    log_message(f"\n{'='*80}")
    log_message(f" ALL PHASES COMPLETED SUCCESSFULLY")
    log_message(f"Total Duration: {overall_duration/60:.2f} minutes ({overall_duration/3600:.2f} hours)")
    log_message(f"Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_message(f"{'='*80}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_message("\n\n INTERRUPTED: Execution stopped by user (Ctrl+C)")
        log_message("Partial results may be available in output files.")
        sys.exit(130)
    except Exception as e:
        log_message(f"\n\n✗ FATAL ERROR: {e}")
        import traceback
        log_message(traceback.format_exc())
        sys.exit(1)