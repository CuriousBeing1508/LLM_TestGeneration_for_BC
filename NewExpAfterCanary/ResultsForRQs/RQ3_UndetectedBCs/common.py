import os
import json
import subprocess
from pathlib import Path


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