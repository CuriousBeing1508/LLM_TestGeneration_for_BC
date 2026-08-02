"""Shared utility helpers used by all five pipeline scripts.

Pure functions only - no filesystem side effects at import time, and no
knowledge of which model/context is being run (that lives in config.py).
"""
import re


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
    """Best-effort classification of a failing docker/maven log.

    Heuristic and intentionally not exhaustive - unmatched cases fall back
    to "unknown" with the last few log lines attached for manual triage.
    """
    log_lower = log_content.lower()

    if "lambda expressions are not supported in" in log_lower:
        return {
            "type": "syntax error",
            "subtype": "java version incompatibility",
            "content": "LLM-generated code uses Java 8+ features (e.g., lambdas), but the target project compiles with -source 1.7"
        }
    elif "illegal character: '`'" in log_lower:
        return {
            "type": "syntax error",
            "subtype": "invalid character",
            "content": "Backticks (`) are not valid in Java. Possibly introduced by LLM formatting."
        }
    elif "cannot find symbol" in log_lower or "symbol:   class" in log_lower:
        return {
            "type": "dependency error",
            "content": "Missing or unrecognized classes; possibly a dependency issue."
        }
    elif " ';' expected" in log_lower or "illegal start of type" in log_lower:
        return {
            "type": "syntax error",
            "content": "Likely syntax issue in the Java code."
        }
    else:
        return {
            "type": "unknown",
            "content": get_last_lines(log_content, 10)
        }


def clean_llm_code(lines):
    """Strip markdown fences and normalize smart quotes/backticks from LLM output."""
    cleaned = []
    for line in lines:
        if line.strip().startswith("```"):
            continue
        line = line.replace("`", "")
        line = line.replace("’", "'").replace("‘", "'")
        line = line.replace("“", "\"").replace("”", "\"")
        cleaned.append(line)
    return cleaned


def parse_package_summary(path):
    """Look up (test_root, package) for a given (custom_id, stage) from
    package_structure_summary.txt, produced by the earlier static-analysis
    step (out of scope for this package - see data/ConfigFiles/)."""
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


def sanitize_class_name(name: str) -> str:
    """Sanitize a name into a valid Java identifier."""
    cleaned = []
    for ch in name:
        cleaned.append(ch if (ch.isalnum() or ch == "_") else "_")
    if not cleaned:
        return "XEmpty"
    base = "".join(cleaned)
    if base[0].isdigit():
        base = "X" + base
    return base


def to_java_filename(txt_name: str) -> tuple:
    """Convert a .txt filename to a .java filename and class name.

    Example: BBC10U1Test_prompt.txt -> (BBC10U1Test.java, BBC10U1Test)
    """
    if txt_name.endswith("_prompt.txt"):
        base = txt_name[:-len("_prompt.txt")]
    elif txt_name.endswith(".txt"):
        base = txt_name[:-len(".txt")]
    else:
        base = txt_name
    base = sanitize_class_name(base)
    return f"{base}.java", base


def extract_llm_java_block(text: str) -> str:
    """Extract the Java code from a raw LLM response.

    Most responses wrap the test in a ```java ... ``` (or bare ``` ... ```)
    fence. GPT-OSS-120b, however, returns completely unfenced raw Java in
    the large majority of its outputs (~85-90% of files in this dataset) -
    the original extractor only understood the fenced case, so it silently
    treated every unfenced GPT-OSS response as "no code found" and dropped
    it before it ever reached compilation. When no fence is found (or a
    fence is found but empty), fall back to the raw response if it looks
    like Java, instead of discarding it.
    """
    lines = text.splitlines()
    in_block = False
    fenced = False
    buf = []
    for line in lines:
        s = line.strip()
        if not in_block:
            if s.lower() in ("```java", "```"):
                in_block = True
                fenced = True
        else:
            if s == "```":
                break
            buf.append(line)

    if fenced:
        extracted = "\n".join(buf).strip()
        if extracted:
            return extracted
        # fenced but empty - fall through to the raw-text fallback below

    raw = text.strip()
    if raw.startswith(("import ", "package ", "public class", "class ")) or " class " in raw:
        return raw
    return ""


def rewrite_package_and_class(code_text: str, package_decl: str, class_name: str) -> str:
    """Rewrite the package declaration and public class name to match the
    target project's structure (required for the transplanted test to
    compile at the right path)."""
    code = code_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    if re.search(r"^\s*package\s+[\w\.]+;\s*$", code, flags=re.MULTILINE):
        code = re.sub(
            r"^\s*package\s+[\w\.]+;\s*$",
            f"package {package_decl};",
            code,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        code = f"package {package_decl};\n\n{code}"

    code = re.sub(r"(public\s+class\s+)([A-Za-z_]\w*)", r"\1" + class_name, code, count=1)
    return code
