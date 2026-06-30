import os
import json
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config import PRIMARY_DRIVE, SECONDARY_DRIVE

# #########################################################
# GPT Execution
# For pre execution
# LOG_DIR_BATCH = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/pre/logs"
# LOG_DIR_BATCH.mkdir(parents=True, exist_ok=True)

# # For Batch execution breaking
# LOG_DIR_BATCH_BRE = PRIMARY_DRIVE / "GPTResults/Exp3BatchResults/bre/logs"
# LOG_DIR_BATCH_BRE.mkdir(parents=True, exist_ok=True)

# ##########################################################
# Qwen execution

# For pre execution
LOG_DIR_BATCH = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/pre/logs"
LOG_DIR_BATCH.mkdir(parents=True, exist_ok=True)

# For Batch execution breaking
LOG_DIR_BATCH_BRE = SECONDARY_DRIVE / "Qwen480Results/Exp3BatchResults/bre/logs"
LOG_DIR_BATCH_BRE.mkdir(parents=True, exist_ok=True)

class DockerRunError(Exception):
    def __init__(self, message, log_path):
        super().__init__(message)
        self.log_path = log_path


class MavenTestError(Exception):
    def __init__(self, message, log_path):
        super().__init__(message)
        self.log_path = log_path


def get_last_lines(log_content, num_lines=50):
    lines = log_content.strip().splitlines()
    return '\n'.join(lines[-num_lines:]).strip()


def classify_compilation_error(log_content):
    """
    Classify compilation errors from log content.
    
    Returns:
        dict with keys: "category", "subtype" (optional), "reason"
    """
    log_lower = log_content.lower()

    if "lambda expressions are not supported in" in log_lower:
        return {
            "category": "syntax error",
            "subtype": "java version incompatibility",
            "reason": "LLM-generated code uses Java 8+ features (e.g., lambdas), but the target project compiles with -source 1.7"
        }
    elif "illegal character: '`'" in log_lower:
        return {
            "category": "syntax error",
            "subtype": "invalid character",
            "reason": "Backticks (`) are not valid in Java. Possibly introduced by LLM formatting."
        }
    elif "cannot find symbol" in log_lower or "symbol:   class" in log_lower:
        return {
            "category": "dependency error",
            "reason": "Missing or unrecognized classes; possibly a dependency issue."
        }
    elif " ';' expected" in log_lower or "illegal start of type" in log_lower:
        return {
            "category": "syntax error",
            "reason": "Likely syntax issue in the Java code."
        }
    else:
        return {
            "category": "unknown",
            "reason": get_last_lines(log_content, 10)
        }


def clean_llm_code(lines):
    """
    Clean LLM-generated code by removing markdown artifacts and fixing quotes.
    
    Args:
        lines: List of code lines
        
    Returns:
        List of cleaned code lines
    """
    cleaned = []
    for line in lines:
        # Skip markdown code fence markers
        if line.strip().startswith("```"):
            continue
        # Remove backticks
        line = line.replace("`", "")
        # Fix smart quotes
        line = line.replace("'", "'").replace("'", "'")
        line = line.replace(""", "\"").replace(""", "\"")
        cleaned.append(line)
    return cleaned


def parse_package_summary(path):
    """
    Parse package structure summary file.
    
    The file format is:
        ==== BBC10 | pre
        Test root: /workspace/src/test/java
        package: se.kth.assertgroup.core
    
    Args:
        path: Path to package_structure_summary.txt
        
    Returns:
        dict: {(custom_id, stage): (test_root, package)}
        Example: {("BBC10", "pre"): ("/workspace/src/test/java", "se.kth.assertgroup.core")}
    """
    info = {}
    current_id = current_type = None
    test_root = package = None

    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("===="):
                parts = line.split(" | ")
                if len(parts) >= 2:
                    current_id = parts[0].replace("====", "").strip()
                    current_type = parts[1].strip()
                    test_root = package = None
            elif line.startswith("Test root:"):
                test_root = line.split("Test root:")[1].strip()
            elif line.startswith("package:"):
                package = line.split("package:")[1].strip()

            if current_id and current_type and test_root and package:
                info[(current_id, current_type)] = (test_root, package)
                current_id = current_type = test_root = package = None
    return info