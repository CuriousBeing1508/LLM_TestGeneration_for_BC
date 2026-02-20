"""
Robust Java Assert Analyzer — tree-sitter AST edition
Handles: JUnit 4, JUnit 5, TestNG, AssertJ, Hamcrest
Covers all edge cases: comments, text blocks, qualified calls,
fluent chains, helper methods, multi-line calls, soft assertions.

Install:
    pip install tree-sitter==0.21.3 tree-sitter-java==0.21.0
"""

import os
import re
import csv
import sys
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

try:
    import tree_sitter_java as tsjava
    from tree_sitter import Language, Parser
except ImportError:
    sys.exit(
        "Missing deps. Run:\n"
        "  pip install tree-sitter==0.21.3 tree-sitter-java==0.21.0"
    )

# ── Build parser once ─────────────────────────────────────────────────────────
try:
    # Try newer API (tree-sitter >= 0.22)
    JAVA_LANG = Language(tsjava.language(), "java")
except TypeError:
    # Fall back to older API (tree-sitter 0.21)
    JAVA_LANG = Language(tsjava.language())

PARSER = Parser()
PARSER.set_language(JAVA_LANG)


# ─────────────────────────────────────────────────────────────────────────────
# 1.  FRAMEWORK DEFINITIONS
#     Each entry describes one assert framework's imports and call styles.
# ─────────────────────────────────────────────────────────────────────────────

FRAMEWORKS = {
    "junit4": {
        "imports": [
            "org.junit.Assert",
            "org.junit.Assert.*",
        ],
        # static methods: assertEquals, assertTrue, ...
        "static_methods": {
            "assertEquals", "assertNotEquals",
            "assertTrue", "assertFalse",
            "assertNull", "assertNotNull",
            "assertSame", "assertNotSame",
            "assertArrayEquals", "assertThat",
            "fail",
        },
        "qualified_class": "Assert",   # Assert.assertEquals(...)
    },
    "junit5": {
        "imports": [
            "org.junit.jupiter.api.Assertions",
            "org.junit.jupiter.api.Assertions.*",
        ],
        "static_methods": {
            "assertEquals", "assertNotEquals",
            "assertTrue", "assertFalse",
            "assertNull", "assertNotNull",
            "assertSame", "assertNotSame",
            "assertArrayEquals",
            "assertThrows", "assertDoesNotThrow",
            "assertTimeout", "assertTimeoutPreemptively",
            "assertIterableEquals", "assertLinesMatch",
            "assertAll", "fail",
        },
        "qualified_class": "Assertions",
    },
    "testng": {
        "imports": [
            "org.testng.Assert",
            "org.testng.Assert.*",
            "org.testng.AssertJUnit",
        ],
        "static_methods": {
            "assertEquals", "assertNotEquals",
            "assertTrue", "assertFalse",
            "assertNull", "assertNotNull",
            "assertSame", "assertNotSame",
            "assertEqualsNoOrder",
            "assertThrows", "expectThrows",
            "fail",
        },
        "qualified_class": "Assert",
    },
    "assertj": {
        "imports": [
            "org.assertj.core.api.Assertions",
            "org.assertj.core.api.Assertions.*",
            "org.assertj.core.api.SoftAssertions",
            "org.assertj.core.api.BDDAssertions",
            "org.assertj.core.api.BDDAssertions.*",
        ],
        # Entry points — the fluent chain starts here
        "static_methods": {
            "assertThat", "assertThatThrownBy", "assertThatCode",
            "assertThatExceptionOfType", "assertThatNoException",
            "assertThatObject", "assertThatList",
            "catchThrowable", "catchThrowableOfType",
            "fail",
            # BDD style
            "then", "thenThrownBy",
        },
        "qualified_class": "Assertions",
        "fluent": True,   # chain detection enabled
        # SoftAssertions: softly.assertThat(...).assertAll()
        "soft_classes": {"SoftAssertions", "BDDSoftAssertions", "JUnitSoftAssertions"},
    },
    "hamcrest": {
        "imports": [
            "org.hamcrest.MatcherAssert",
            "org.hamcrest.MatcherAssert.*",
        ],
        "static_methods": {"assertThat"},
        "qualified_class": "MatcherAssert",
    },
}

# Flat lookup: method name → list of frameworks that own it
_METHOD_TO_FRAMEWORKS: dict[str, list[str]] = defaultdict(list)
for _fw, _cfg in FRAMEWORKS.items():
    for _m in _cfg.get("static_methods", set()):
        _METHOD_TO_FRAMEWORKS[_m].append(_fw)

ALL_ASSERT_METHODS: set[str] = set(_METHOD_TO_FRAMEWORKS.keys())

# All known assert class names for qualified-call detection
ALL_QUALIFIED_CLASSES: set[str] = {
    cfg["qualified_class"] for cfg in FRAMEWORKS.values() if "qualified_class" in cfg
}
ALL_SOFT_CLASSES: set[str] = {
    c for cfg in FRAMEWORKS.values() for c in cfg.get("soft_classes", set())
}


# ─────────────────────────────────────────────────────────────────────────────
# 2.  DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AssertCall:
    method:     str
    line:       int
    source:     str          # 'static' | 'qualified' | 'soft' | 'helper'
    framework:  str          # 'junit4' | 'junit5' | 'testng' | 'assertj' | 'hamcrest' | 'unknown'
    in_trycatch: bool = False
    call_text:  str = ""


@dataclass
class FileResult:
    path:              str
    # imports
    raw_imports:       list[str]            = field(default_factory=list)
    frameworks_imported: set[str]           = field(default_factory=set)
    # assert calls (real, in code)
    assert_calls:      list[AssertCall]     = field(default_factory=list)
    # helper methods that wrap asserts
    helper_methods:    list[str]            = field(default_factory=list)
    # flags
    has_assert_in_comments: bool            = False
    parse_error:       Optional[str]        = None

    @property
    def has_import(self):       return bool(self.frameworks_imported)
    @property
    def has_real_calls(self):   return bool(self.assert_calls)
    @property
    def import_only(self):      return self.has_import and not self.has_real_calls
    @property
    def comment_only(self):     return self.has_assert_in_comments and not self.has_real_calls and not self.has_import

    def method_counts(self) -> dict[str, int]:
        counts = defaultdict(int)
        for c in self.assert_calls:
            counts[c.method] += 1
        return dict(counts)

    def framework_counts(self) -> dict[str, int]:
        counts = defaultdict(int)
        for c in self.assert_calls:
            counts[c.framework] += 1
        return dict(counts)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  IMPORT ANALYSIS  (regex on raw source — safe here, imports are unambiguous)
# ─────────────────────────────────────────────────────────────────────────────

# Matches any import line and captures the imported name
_IMPORT_LINE_RE = re.compile(
    r'^\s*import\s+(?:static\s+)?([a-zA-Z][\w.]*(?:\.\*)?)\s*;', re.MULTILINE
)

def _detect_frameworks_from_imports(raw: str) -> tuple[list[str], set[str]]:
    """Return (raw_import_lines, set_of_framework_names)."""
    raw_imports = []
    detected    = set()

    for m in _IMPORT_LINE_RE.finditer(raw):
        imp = m.group(1)
        raw_imports.append(imp)
        for fw, cfg in FRAMEWORKS.items():
            for pattern in cfg["imports"]:
                # exact or wildcard match
                if imp == pattern or imp.startswith(pattern.replace(".*", ".")):
                    detected.add(fw)

    return raw_imports, detected


# ─────────────────────────────────────────────────────────────────────────────
# 4.  COMMENT SCANNING  (regex on raw source for "assert" mentions)
# ─────────────────────────────────────────────────────────────────────────────

_COMMENT_RE = re.compile(
    r'//[^\n]*|/\*.*?\*/',
    re.DOTALL
)
_ASSERT_WORD_RE = re.compile(r'(?i)\bassert\b')

def _has_assert_in_comments(raw: str) -> bool:
    for m in _COMMENT_RE.finditer(raw):
        if _ASSERT_WORD_RE.search(m.group()):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 5.  AST HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _node_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

def _node_line(node) -> int:
    return node.start_point[0] + 1   # 1-based

def _is_inside_trycatch(node) -> bool:
    """Walk up the AST to check if node is inside a try_statement."""
    cur = node.parent
    while cur:
        if cur.type == "try_statement":
            return True
        cur = cur.parent
    return False

def _method_name_of(node, src: bytes) -> Optional[str]:
    """
    Given a method_invocation node, return the method name string.
    Handles:  foo()  /  Obj.foo()  /  a.b.c.foo()
    """
    # tree-sitter java: method_invocation children vary by style
    # direct call:    (method_invocation name:(identifier) ...)
    # qualified call: (method_invocation object:... name:(identifier) ...)
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, src)
    return None

def _object_name_of(node, src: bytes) -> Optional[str]:
    """
    Return the receiver object/class name for a.foo() style calls.
    Only returns the immediate left-side identifier.
    """
    # In tree-sitter-java the object is the first child before '.'
    prev = None
    for child in node.children:
        if child.type == ".":
            break
        prev = child
    if prev and prev.type == "identifier":
        return _node_text(prev, src)
    # object could itself be a method_invocation (chained), type_identifier, etc.
    if prev and prev.type == "type_identifier":
        return _node_text(prev, src)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 6.  VARIABLE TYPE TRACKER
#     Tracks local variable declarations so we can resolve
#     softly.assertThat() when softly is a SoftAssertions instance.
# ─────────────────────────────────────────────────────────────────────────────

def _collect_local_var_types(root_node, src: bytes) -> dict[str, str]:
    """
    Walk all local_variable_declaration nodes and return {var_name: type_name}.
    Only captures the simple type name (not generics).
    """
    var_types: dict[str, str] = {}

    def walk(node):
        if node.type in ("local_variable_declaration", "field_declaration"):
            # children: type declarator(s)
            type_node = None
            for child in node.children:
                if child.type in ("type_identifier", "generic_type"):
                    type_node = child
                    break
            if type_node:
                type_name = _node_text(type_node, src).split("<")[0].strip()
                # find all variable_declarator children
                for child in node.children:
                    if child.type == "variable_declarator":
                        for sub in child.children:
                            if sub.type == "identifier":
                                var_types[_node_text(sub, src)] = type_name
                                break
        for child in node.children:
            walk(child)

    walk(root_node)
    return var_types


# ─────────────────────────────────────────────────────────────────────────────
# 7.  HELPER METHOD DETECTOR
#     Finds methods in the test class that call assert inside them.
#     These are wrappers that the test methods delegate to.
# ─────────────────────────────────────────────────────────────────────────────

def _find_helper_method_names(root_node, src: bytes) -> set[str]:
    """
    Return names of non-@Test methods that contain assert calls inside them.
    """
    helpers = set()

    def walk(node):
        if node.type == "method_declaration":
            # Check if this method has @Test annotation
            is_test = False
            for child in node.children:
                if child.type == "modifiers":
                    for mod in child.children:
                        if mod.type == "marker_annotation":
                            ann_name = _node_text(mod, src).lstrip("@")
                            if ann_name == "Test":
                                is_test = True
            if not is_test:
                # Check if body contains assert calls
                if _body_has_assert(node, src):
                    # get method name
                    for child in node.children:
                        if child.type == "identifier":
                            helpers.add(_node_text(child, src))
                            break
        for child in node.children:
            walk(child)

    walk(root_node)
    return helpers


def _body_has_assert(method_node, src: bytes) -> bool:
    def walk(node):
        if node.type == "method_invocation":
            name = _method_name_of(node, src)
            if name and name in ALL_ASSERT_METHODS:
                return True
        for child in node.children:
            if walk(child):
                return True
        return False
    return walk(method_node)


# ─────────────────────────────────────────────────────────────────────────────
# 8.  CORE AST WALKER — finds every real assert call
# ─────────────────────────────────────────────────────────────────────────────

def _collect_assert_calls(
    root_node,
    src: bytes,
    frameworks_imported: set[str],
    var_types: dict[str, str],
) -> list[AssertCall]:

    calls: list[AssertCall] = []

    def walk(node):
        if node.type == "method_invocation":
            method_name = _method_name_of(node, src)
            obj_name    = _object_name_of(node, src)

            if method_name:
                call_text   = _node_text(node, src)
                line        = _node_line(node)
                in_try      = _is_inside_trycatch(node)

                # ── A. Static assert call (bare name, e.g. assertEquals(...)) ──
                if method_name in ALL_ASSERT_METHODS and obj_name is None:
                    fw = _resolve_framework(method_name, frameworks_imported, "static")
                    calls.append(AssertCall(
                        method=method_name, line=line,
                        source="static", framework=fw,
                        in_trycatch=in_try, call_text=call_text[:120]
                    ))

                # ── B. Qualified class call (Assert.assertEquals / Assertions.assertThat) ──
                elif method_name in ALL_ASSERT_METHODS and obj_name in ALL_QUALIFIED_CLASSES:
                    fw = _resolve_framework(method_name, frameworks_imported, "qualified", obj_name)
                    calls.append(AssertCall(
                        method=method_name, line=line,
                        source="qualified", framework=fw,
                        in_trycatch=in_try, call_text=call_text[:120]
                    ))

                # ── C. Fully qualified (org.junit.Assert.assertEquals) ──
                elif method_name in ALL_ASSERT_METHODS and obj_name is not None:
                    # Check if it looks like a package path in the call text
                    if re.search(r'org\.(junit|testng)|org\.assertj|org\.hamcrest', call_text):
                        fw = _resolve_framework(method_name, frameworks_imported, "fqn")
                        calls.append(AssertCall(
                            method=method_name, line=line,
                            source="qualified", framework=fw,
                            in_trycatch=in_try, call_text=call_text[:120]
                        ))

                # ── D. Soft assertions: softly.assertThat(...) ──
                elif obj_name and obj_name in var_types:
                    type_name = var_types[obj_name]
                    if type_name in ALL_SOFT_CLASSES and method_name in ALL_ASSERT_METHODS:
                        calls.append(AssertCall(
                            method=method_name, line=line,
                            source="soft", framework="assertj",
                            in_trycatch=in_try, call_text=call_text[:120]
                        ))

                # ── E. assertAll() lambda bodies (JUnit 5) ──
                # tree-sitter will recurse into lambdas automatically,
                # so inner assertEquals inside assertAll are caught by A above.

        for child in node.children:
            walk(child)

    walk(root_node)
    return calls


def _resolve_framework(
    method: str,
    imported: set[str],
    source: str,
    qualifier: Optional[str] = None,
) -> str:
    """Best-effort framework attribution given what's imported."""
    candidates = _METHOD_TO_FRAMEWORKS.get(method, [])

    # If only one framework imported and it owns this method, easy
    matching_imported = [fw for fw in candidates if fw in imported]
    if len(matching_imported) == 1:
        return matching_imported[0]
    if len(matching_imported) > 1:
        # qualifier hint
        if qualifier == "Assertions":
            for fw in ("junit5", "assertj"):
                if fw in matching_imported:
                    return fw
        if qualifier == "Assert":
            for fw in ("junit4", "testng"):
                if fw in matching_imported:
                    return fw
        return matching_imported[0]

    # Nothing imported but call exists — best guess from method name
    if candidates:
        return candidates[0]
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 9.  TOP-LEVEL FILE ANALYZER
# ─────────────────────────────────────────────────────────────────────────────

def analyze_file(filepath: str) -> FileResult:
    result = FileResult(path=filepath)

    try:
        raw_bytes = Path(filepath).read_bytes()
        raw_str   = raw_bytes.decode("utf-8", errors="replace")
    except OSError as e:
        result.parse_error = str(e)
        return result

    # ── Imports (regex on raw) ────────────────────────────────────────────────
    result.raw_imports, result.frameworks_imported = \
        _detect_frameworks_from_imports(raw_str)

    # ── Comment check (regex on raw) ─────────────────────────────────────────
    result.has_assert_in_comments = _has_assert_in_comments(raw_str)

    # ── Parse AST ─────────────────────────────────────────────────────────────
    try:
        tree = PARSER.parse(raw_bytes)
    except Exception as e:
        result.parse_error = f"tree-sitter parse error: {e}"
        return result

    if tree.root_node.has_error:
        # Still continue — partial parse is usually good enough
        result.parse_error = "syntax warnings (partial parse used)"

    root = tree.root_node

    # ── Variable types (for soft assertion resolution) ───────────────────────
    var_types = _collect_local_var_types(root, raw_bytes)

    # ── Helper methods ────────────────────────────────────────────────────────
    helper_names = _find_helper_method_names(root, raw_bytes)
    result.helper_methods = sorted(helper_names)

    # ── Real assert calls ─────────────────────────────────────────────────────
    result.assert_calls = _collect_assert_calls(
        root, raw_bytes, result.frameworks_imported, var_types
    )

    return result


def analyze_directory(root: str, pattern: str = "*Test*.java") -> list[FileResult]:
    results = []
    for p in Path(root).rglob(pattern):
        results.append(analyze_file(str(p)))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 10. REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def print_report(results: list[FileResult]):
    total          = len(results)
    with_import    = sum(1 for r in results if r.has_import)
    with_calls     = sum(1 for r in results if r.has_real_calls)
    import_only    = sum(1 for r in results if r.import_only)
    comment_only   = sum(1 for r in results if r.comment_only)
    no_assert      = sum(1 for r in results if not r.has_import and not r.has_real_calls)
    in_trycatch    = sum(1 for r in results if any(c.in_trycatch for c in r.assert_calls))
    has_helpers    = sum(1 for r in results if r.helper_methods)
    has_soft       = sum(1 for r in results if any(c.source == "soft" for c in r.assert_calls))

    global_method_counts:    defaultdict[str, int] = defaultdict(int)
    global_framework_counts: defaultdict[str, int] = defaultdict(int)
    for r in results:
        for c in r.assert_calls:
            global_method_counts[c.method]    += 1
            global_framework_counts[c.framework] += 1

    W = 65
    print("=" * W)
    print("  ASSERT USAGE REPORT  (AST-based)")
    print("=" * W)
    print(f"  Total test files analyzed       : {total}")
    print(f"  Files with assert import        : {with_import}")
    print(f"  Files with real assert calls    : {with_calls}")
    print(f"  ├─ assert calls inside try/catch: {in_trycatch}")
    print(f"  ├─ using soft assertions         : {has_soft}")
    print(f"  └─ delegates to helper methods  : {has_helpers}")
    print(f"  Import only, no call in code    : {import_only}")
    print(f"  Assert in comments only         : {comment_only}")
    print(f"  No assert at all                : {no_assert}")
    print()

    if global_framework_counts:
        print("  By framework:")
        for fw, cnt in sorted(global_framework_counts.items(), key=lambda x: -x[1]):
            print(f"    {fw:<20} {cnt} calls")
        print()

    if global_method_counts:
        print("  By method (all files):")
        for m, cnt in sorted(global_method_counts.items(), key=lambda x: -x[1]):
            fws = "/".join(_METHOD_TO_FRAMEWORKS.get(m, ["?"]))
            print(f"    {m:<42} {cnt:>4}   ({fws})")
        print()

    categories = [
        (" Real assert calls",          [r for r in results if r.has_real_calls]),
        ("   Import only, no call",       [r for r in results if r.import_only]),
        ("  Assert in comments only",    [r for r in results if r.comment_only]),
        ("  No assert at all",            [r for r in results if not r.has_import
                                                                  and not r.has_real_calls
                                                                  and not r.comment_only]),
    ]

    for label, group in categories:
        if not group:
            continue
        print(f"  {label}  ({len(group)} files)")
        print("  " + "-" * (W - 2))
        for r in group:
            name = Path(r.path).name
            if r.has_real_calls:
                m_str = ", ".join(f"{m}×{c}" for m, c in sorted(r.method_counts().items()))
                fw_str = ", ".join(r.framework_counts().keys())
                flags  = []
                if any(c.in_trycatch for c in r.assert_calls):
                    flags.append("in-try")
                if r.helper_methods:
                    flags.append(f"helpers:{','.join(r.helper_methods)}")
                if any(c.source == "soft" for c in r.assert_calls):
                    flags.append("soft")
                flag_str = f"  [{', '.join(flags)}]" if flags else ""
                print(f"    {name}")
                print(f"      frameworks : {fw_str}")
                print(f"      methods    : {m_str}{flag_str}")
                for c in r.assert_calls[:4]:
                    src_tag = f"[{c.source}]"
                    print(f"      line {c.line:>4} {src_tag:<12} {c.call_text[:72]}")
                if len(r.assert_calls) > 4:
                    print(f"      ... +{len(r.assert_calls) - 4} more")
            elif r.has_import:
                print(f"    {name}")
                for imp in r.raw_imports[:4]:
                    print(f"      import: {imp}")
            else:
                print(f"    {name}")
            if r.parse_error:
                print(f"      ⚠ parse: {r.parse_error}")
        print()


def export_csv(results: list[FileResult], out: str = "assert_report.csv"):
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "file", "frameworks_imported", "has_real_calls",
            "import_only", "comment_only", "total_calls",
            "calls_in_trycatch", "has_soft_assertions",
            "helper_methods", "methods_used", "parse_error",
        ])
        for r in results:
            methods   = ";".join(f"{m}:{c}" for m, c in r.method_counts().items())
            helpers   = ";".join(r.helper_methods)
            frameworks= ";".join(r.frameworks_imported)
            w.writerow([
                Path(r.path).name,
                frameworks,
                r.has_real_calls,
                r.import_only,
                r.comment_only,
                len(r.assert_calls),
                sum(1 for c in r.assert_calls if c.in_trycatch),
                any(c.source == "soft" for c in r.assert_calls),
                helpers,
                methods,
                r.parse_error or "",
            ])
    print(f"  CSV saved → {out}")


# ─────────────────────────────────────────────────────────────────────────────
# 11. ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(f"\nScanning: {os.path.abspath(root)}\n")
    results = analyze_directory(root)
    if not results:
        print("No *Test*.java files found.")
    else:
        print_report(results)
        export_csv(results)