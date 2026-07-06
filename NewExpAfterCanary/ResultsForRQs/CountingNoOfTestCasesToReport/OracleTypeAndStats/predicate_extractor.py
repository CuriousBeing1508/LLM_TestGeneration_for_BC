"""
Extracts the "predicate" (the actual condition/value expression(s) being
checked) and the "message" (if any) out of an assert call's raw call_text.

Rough approach: split the call's argument list on top-level commas (respecting
nested parens/brackets/braces and string/char literals), then use a lookup
table of (method, framework, arg_count) -> per-position role ('predicate' or
'message') to decide which argument is the message. The role tables encode
each framework's real overload argument order (JUnit4 puts message first,
JUnit5 puts it last, mirroring org.junit.Assert vs org.junit.jupiter.api.Assertions).
Unrecognized (method, framework, arg_count) combos fall back to treating every
argument as part of the predicate (never silently drops data).

"""
import re
from pathlib import Path

_ARG_SPLIT_RE = re.compile(r'^\s*[A-Za-z_][A-Za-z0-9_.]*\s*\((.*)\)\s*$', re.DOTALL)
TRUNCATION_LENGTH = 120


def reextract_full_call_text(java_file_path, line, method):
    """Re-read the source file and balanced-paren-scan the call starting at
    `line` (1-indexed) to recover the full call text, undoing the upstream
    120-char truncation. Returns None if the file/method can't be found."""
    try:
        text = Path(java_file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines = text.splitlines(keepends=True)
    if line - 1 >= len(lines):
        return None
    snippet = "".join(lines[line - 1:])

    match = re.search(rf"\b{re.escape(method)}\s*\(", snippet)
    if not match:
        return None

    start = match.start()
    i = match.end() - 1  # position of the opening '('
    depth = 0
    in_string = False
    in_char = False
    n = len(snippet)
    while i < n:
        c = snippet[i]
        if in_string:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if in_char:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == "'":
                in_char = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c == "'":
            in_char = True
            i += 1
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            if depth == 0:
                return snippet[start:i]
            continue
        i += 1
    return None  # ran off the end of the file still unbalanced


def resolve_call_text(call, java_file_path):
    """Returns (call_text, was_truncated). If the stored call_text hit the
    120-char cap, tries to recover the full text from source; was_truncated
    is True only if that recovery also failed (i.e. call_text is still cut off)."""
    call_text = call["call_text"]
    if len(call_text) != TRUNCATION_LENGTH:
        return call_text, False

    full_text = reextract_full_call_text(java_file_path, call["line"], call["method"])
    if full_text is None:
        return call_text, True
    return full_text, False


def split_top_level_args(inner):
    """Split a call's argument-list text on top-level commas only."""
    args = []
    current = []
    depth = 0
    in_string = False
    in_char = False
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if in_string:
            current.append(c)
            if c == "\\" and i + 1 < n:
                current.append(inner[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if in_char:
            current.append(c)
            if c == "\\" and i + 1 < n:
                current.append(inner[i + 1])
                i += 2
                continue
            if c == "'":
                in_char = False
            i += 1
            continue
        if c == '"':
            in_string = True
            current.append(c)
            i += 1
            continue
        if c == "'":
            in_char = True
            current.append(c)
            i += 1
            continue
        if c in "([{":
            depth += 1
            current.append(c)
            i += 1
            continue
        if c in ")]}":
            depth -= 1
            current.append(c)
            i += 1
            continue
        if c == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    tail = "".join(current).strip()
    if tail or args:
        args.append(tail)
    return args


def extract_args(call_text):
    """Top-level arguments of an assert-style call, e.g. 'fail()' -> []."""
    match = _ARG_SPLIT_RE.match(call_text)
    if not match:
        return []
    inner = match.group(1).strip()
    if not inner:
        return []
    return split_top_level_args(inner)


# (method, framework) -> {arg_count: [role, role, ...]}, role in {"predicate", "message"}
_ROLE_TABLES = {
    ("assertTrue", "junit4"): {1: ["predicate"], 2: ["message", "predicate"]},
    ("assertFalse", "junit4"): {1: ["predicate"], 2: ["message", "predicate"]},
    ("assertTrue", "junit5"): {1: ["predicate"], 2: ["predicate", "message"]},
    ("assertFalse", "junit5"): {1: ["predicate"], 2: ["predicate", "message"]},

    ("assertNull", "junit4"): {1: ["predicate"], 2: ["message", "predicate"]},
    ("assertNotNull", "junit4"): {1: ["predicate"], 2: ["message", "predicate"]},
    ("assertNull", "junit5"): {1: ["predicate"], 2: ["predicate", "message"]},
    ("assertNotNull", "junit5"): {1: ["predicate"], 2: ["predicate", "message"]},

    ("fail", "junit4"): {0: [], 1: ["message"]},
    ("fail", "junit5"): {0: [], 1: ["message"], 2: ["message", "predicate"]},

    ("assertDoesNotThrow", "junit5"): {1: ["predicate"], 2: ["predicate", "message"]},
}

# Binary comparison asserts: expected/actual (or expecteds/actuals) pair, plus
# an optional message. JUnit4 = message first when 3 args; JUnit5 = message last.
for _method in ("assertEquals", "assertNotEquals", "assertArrayEquals", "assertSame", "assertNotSame"):
    _ROLE_TABLES[(_method, "junit4")] = {2: ["predicate", "predicate"], 3: ["message", "predicate", "predicate"]}
    _ROLE_TABLES[(_method, "junit5")] = {2: ["predicate", "predicate"], 3: ["predicate", "predicate", "message"]}

# assertThrows(expectedType, executable[, message]) - JUnit5 only, but be
# permissive about the framework tag since it's sometimes mis-tagged junit4.
for _fw in ("junit4", "junit5"):
    _ROLE_TABLES[("assertThrows", _fw)] = {2: ["predicate", "predicate"], 3: ["predicate", "predicate", "message"]}
    _ROLE_TABLES[("assertThrowsExactly", _fw)] = {2: ["predicate", "predicate"], 3: ["predicate", "predicate", "message"]}


def get_roles(method, framework, n_args):
    table = _ROLE_TABLES.get((method, framework))
    if table is not None and n_args in table:
        return table[n_args]
    return ["predicate"] * n_args


def extract_predicate_and_message(method, framework, call_text):
    """Returns (predicate_str, message_str_or_None)."""
    args = extract_args(call_text)
    roles = get_roles(method, framework, len(args))

    predicate_parts = [a for a, r in zip(args, roles) if r == "predicate"]
    message_parts = [a for a, r in zip(args, roles) if r == "message"]

    predicate = ", ".join(predicate_parts) if predicate_parts else None
    message = message_parts[0] if message_parts else None
    return predicate, message


CALL_SEP = " ;; "


def format_call(call):
    """Renders one assert call dict ({method, predicate, message, line}) as
    'method(predicate | msg=message) [Lline]', omitting predicate/message
    when absent, so a CSV cell built from these reads like plain text
    instead of escaped JSON. Used by the export_instance_oracle_vs_errortypes_*
    scripts."""
    parts = []
    if call.get("predicate") is not None:
        parts.append(call["predicate"])
    if call.get("message") is not None:
        parts.append(f"msg={call['message']}")
    body = " | ".join(parts)
    return f"{call['method']}({body}) [L{call['line']}]"
