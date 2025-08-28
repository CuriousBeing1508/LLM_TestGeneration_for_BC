#!/usr/bin/env python3
import csv
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET


# we got 10 instances where the test runner cannot be found by the script, so trying to exectract all dependency with test scope to manually decide on the test runner.
# ====== CONFIG (adjust the two paths only if yours differ) ======
#!/usr/bin/env python3

CSV_PATH_IN  = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances_with_TestRunner.csv"
CSV_PATH_LOG = Path(CSV_PATH_IN).with_suffix(".test-scope-deps.csv")

def docker_exec(image: str, shell_cmd: str, timeout: int = 300):
    try:
        p = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64",
             "--entrypoint", "sh", image, "-lc", shell_cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return -1, "", str(e)

def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag

def _iter_elems(root, localname):
    for el in root.iter():
        if _strip_ns(el.tag) == localname:
            yield el

def _read_text(el, name):
    child = next((c for c in el if _strip_ns(c.tag) == name), None)
    return (child.text or "").strip() if child is not None else ""

def extract_test_scope_deps(pom_xml_text: str):
    rows = []
    if not pom_xml_text.strip():
        return rows
    try:
        root = ET.fromstring(pom_xml_text)
    except Exception:
        return rows

    def dep_blocks(parent_root):
        for deps_parent in _iter_elems(parent_root, "dependencies"):
            for dep in _iter_elems(deps_parent, "dependency"):
                yield dep

    # Normal + dependencyManagement deps
    for parent in [root] + list(_iter_elems(root, "dependencyManagement")):
        for dep in dep_blocks(parent):
            gid = _read_text(dep, "groupId")
            aid = _read_text(dep, "artifactId")
            ver = _read_text(dep, "version")
            scp = _read_text(dep, "scope").lower()
            if scp == "test":
                rows.append({
                    "source": "dependency",
                    "groupId": gid, "artifactId": aid, "version": ver, "scope": scp
                })
    return rows

FIND_POMS_CMD = "find / -type f -name pom.xml -maxdepth 10 2>/dev/null | head -n 60"

def collect_pom_paths(image_tag: str):
    rc, out, _ = docker_exec(image_tag, FIND_POMS_CMD, timeout=60)
    return [p.strip() for p in out.splitlines() if p.strip()] if rc == 0 else []

def cat_file(image_tag: str, path: str):
    rc, out, _ = docker_exec(image_tag, f"cat '{path}' 2>/dev/null", timeout=30)
    return out if rc == 0 else ""

def main():
    with open(CSV_PATH_IN, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # ⬅️ include "source" so writer accepts rows
    out_cols = [
        "custom_id", "breakingCommit", "image", "pom_path",
        "groupId", "artifactId", "version", "scope", "source"
    ]
    with open(CSV_PATH_LOG, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=out_cols).writeheader()

    unknowns = [r for r in rows if (r.get("test_framework") or "").strip().lower() == "unknown"]

    for r in unknowns:
        cid = (r.get("custom_id") or "").strip()
        commit = (r.get("breakingCommit") or "").strip()
        if not commit:
            continue
        image = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"

        pom_paths = collect_pom_paths(image)
        for p in pom_paths:
            pom_txt = cat_file(image, p)
            for dep in extract_test_scope_deps(pom_txt):
                with open(CSV_PATH_LOG, "a", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=out_cols)
                    w.writerow({
                        "custom_id": cid, "breakingCommit": commit,
                        "image": image, "pom_path": p, **dep
                    })
        print(f"[{cid}] scanned {len(pom_paths)} pom(s) for test-scope deps")

    print(f"Wrote test-scope dependencies to: {CSV_PATH_LOG}")

if __name__ == "__main__":
    main()
