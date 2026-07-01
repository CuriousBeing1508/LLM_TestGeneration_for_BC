import csv
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PRIMARY_DRIVE

# ====== CONFIG (edit these two paths only) ======
CSV_PATH_IN  = PRIMARY_DRIVE / "updated_FinalBUMP_Instances.csv"       # existing CSV, includes custom_id, breakingCommit
CSV_PATH_OUT = PRIMARY_DRIVE / "updated_FinalBUMP_Instances_with_TestRunner.csv"  # new CSV to write
# ===============================================

def _docker_read_first_pom(image_tag: str, timeout: int = 45) -> str:
    """Return the text of the first pom.xml found inside the image, or '' if none."""
    search_cmd = r"""
set -e
for base in . /workspace /work /app /project /src /home /opt /; do
  if [ -d "$base" ]; then
    P=$(find "$base" -maxdepth 5 -name pom.xml 2>/dev/null | head -n1 || true)
    if [ -n "$P" ]; then
      cat "$P"
      exit 0
    fi
  fi
done
exit 0
""".strip()

    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", image_tag, "sh", "-lc", search_cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return proc.stdout
    except Exception:
        return ""

def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag

def _iter_elems(root, localname):
    for el in root.iter():
        if _strip_ns(el.tag) == localname:
            yield el

def parse_pom_for_test_framework(pom_xml_text: str) -> str:
    """
    Infer 'junit5', 'junit4', 'testng', or 'unknown' from the POM.
    Priority when multiple appear: testng > junit5 > junit4.
    """
    if not pom_xml_text.strip():
        return "unknown"

    # Try XML parse first
    try:
        root = ET.fromstring(pom_xml_text)
    except Exception:
        txt = pom_xml_text.lower()
        if "org.testng" in txt:
            return "testng"
        if "org.junit.jupiter" in txt or "junit-jupiter" in txt or "junit-platform" in txt:
            return "junit5"
        if "<groupid>junit</groupid>" in txt and "<artifactid>junit</artifactid>" in txt:
            return "junit4"
        return "unknown"

    def read_text(el, name):
        child = next((c for c in el if _strip_ns(c.tag) == name), None)
        return (child.text or "").strip() if child is not None else ""

    found_testng = False
    found_junit5 = False
    found_junit4 = False

    # dependencies + dependencyManagement
    def dep_iter():
        for deps_parent in _iter_elems(root, "dependencies"):
            for dep in _iter_elems(deps_parent, "dependency"):
                yield dep
        for dm in _iter_elems(root, "dependencyManagement"):
            for deps_parent in _iter_elems(dm, "dependencies"):
                for dep in _iter_elems(deps_parent, "dependency"):
                    yield dep

    for dep in dep_iter():
        gid = read_text(dep, "groupId").lower()
        aid = read_text(dep, "artifactId").lower()
        if gid.startswith("org.testng") or "testng" in (gid + ":" + aid):
            found_testng = True
        if gid.startswith("org.junit.jupiter") or "junit-jupiter" in aid or "junit-platform" in aid or "junit-platform" in gid:
            found_junit5 = True
        if (gid == "junit" and aid == "junit") or aid == "junit4":
            found_junit4 = True

    # surefire/failsafe plugin hints
    def plugin_iter():
        for build in _iter_elems(root, "build"):
            for plugins in _iter_elems(build, "plugins"):
                for plugin in _iter_elems(plugins, "plugin"):
                    yield plugin
            for pm in _iter_elems(build, "pluginManagement"):
                for plugins in _iter_elems(pm, "plugins"):
                    for plugin in _iter_elems(plugins, "plugin"):
                        yield plugin

    for plugin in plugin_iter():
        gid = "".join(c.text or "" for c in plugin if _strip_ns(c.tag) == "groupId").strip().lower()
        aid = "".join(c.text or "" for c in plugin if _strip_ns(c.tag) == "artifactId").strip().lower()
        if gid == "org.apache.maven.plugins" and aid in ("maven-surefire-plugin", "maven-failsafe-plugin"):
            # plugin dependencies
            for deps_parent in _iter_elems(plugin, "dependencies"):
                for dep in _iter_elems(deps_parent, "dependency"):
                    dgid = "".join(c.text or "" for c in dep if _strip_ns(c.tag) == "groupId").strip().lower()
                    daid = "".join(c.text or "" for c in dep if _strip_ns(c.tag) == "artifactId").strip().lower()
                    if "testng" in dgid or "testng" in daid:
                        found_testng = True
                    if "junit-platform" in dgid or "junit-platform" in daid or "junit-jupiter" in daid:
                        found_junit5 = True
            # configuration text
            for conf in _iter_elems(plugin, "configuration"):
                conf_txt = ET.tostring(conf, encoding="unicode").lower()
                if "testng" in conf_txt:
                    found_testng = True
                if "junitplatform" in conf_txt or "<provider>junit5" in conf_txt:
                    found_junit5 = True

    if found_testng:
        return "testng"
    if found_junit5:
        return "junit5"
    if found_junit4:
        return "junit4"
    return "unknown"

def detect_framework_for_row(breaking_commit: str) -> str:
    """Build image tag and detect framework via POM."""
    if not breaking_commit:
        return "unknown"
    image_tag = f"ghcr.io/chains-project/breaking-updates:{breaking_commit}-pre"
    pom = _docker_read_first_pom(image_tag)
    return parse_pom_for_test_framework(pom)

def main():
    # Read input CSV
    with open(CSV_PATH_IN, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    # Ensure we don't duplicate the column if it already exists
    out_fieldnames = list(fieldnames)
    if "test_framework" not in out_fieldnames:
        out_fieldnames.append("test_framework")

    # Process rows and append new column
    updated_rows = []
    for row in rows:
        commit = (row.get("breakingCommit") or "").strip()
        framework = detect_framework_for_row(commit)
        row["test_framework"] = framework
        updated_rows.append(row)

    # Write output CSV with all original columns + new column at the end
    Path(CSV_PATH_OUT).parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH_OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)

    print(f" Wrote updated CSV with test_framework column to: {CSV_PATH_OUT}")

if __name__ == "__main__":
    main()
