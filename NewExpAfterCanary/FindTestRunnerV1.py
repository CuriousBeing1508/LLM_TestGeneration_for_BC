import csv
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
import sys

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PRIMARY_DRIVE, SECONDARY_DRIVE
# recursive in all poms..
# ====== CONFIG ======
# CSV_PATH_IN  = PRIMARY_DRIVE / "updated_FinalBUMP_Instances.csv"       
# CSV_PATH_OUT = PRIMARY_DRIVE / "updated_FinalBUMP_Instances_with_TestRunner.csv" 
# ===============================================



# ====== CONFIG ======
CSV_PATH_IN  = SECONDARY_DRIVE / "configFiles/BUMP_with_NoLibraryGitHubURL.csv"       # existing CSV, includes custom_id, breakingCommit
CSV_PATH_OUT = SECONDARY_DRIVE / "configFiles/BUMP_with_NoLibraryGitHubURL_with_TestRunner.csv"  # new CSV to write
LOG_PATH = Path(CSV_PATH_OUT).with_suffix(".log")

# ----- Docker helpers (override entrypoint so container doesn't "run") -----

def docker_exec(image: str, shell_cmd: str, timeout: int = 45):
    """Run a shell command inside the image with entrypoint overridden to 'sh'. Return (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", image, "-lc", shell_cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 124, "", str(e)

# ----- XML helpers -----

def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag

def _iter_elems(root, localname):
    for el in root.iter():
        if _strip_ns(el.tag) == localname:
            yield el

def parse_single_pom_for_framework(pom_xml_text: str):
    """
    Return signals dict: {'junit5':bool, 'junit4':bool, 'testng':bool}
    Never raises.
    """
    sig = {"junit5": False, "junit4": False, "testng": False}
    if not pom_xml_text.strip():
        return sig

    # Try XML parse; fall back to textual scan if malformed
    try:
        root = ET.fromstring(pom_xml_text)
    except Exception:
        t = pom_xml_text.lower()
        if "org.testng" in t: sig["testng"] = True
        if "org.junit.jupiter" in t or "junit-jupiter" in t or "junit-platform" in t: sig["junit5"] = True
        if "<groupid>junit</groupid>" in t and "<artifactid>junit</artifactid>" in t: sig["junit4"] = True
        return sig

    def read_text(el, name):
        child = next((c for c in el if _strip_ns(c.tag) == name), None)
        return (child.text or "").strip() if child is not None else ""

    # Check dependencies & dependencyManagement
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
        if "testng" in gid or "testng" in aid:
            sig["testng"] = True
        if gid.startswith("org.junit.jupiter") or "junit-jupiter" in aid or "junit-platform" in aid or "junit-platform" in gid:
            sig["junit5"] = True
        if (gid == "junit" and aid == "junit") or aid == "junit4":
            sig["junit4"] = True

    # surefire/failsafe plugins
    def plugin_iter():
        for build in _iter_elems(root, "build"):
            for plugins in _iter_elems(build, "plugins"):
                for plugin in _iter_elems(plugins, "plugin"):
                    yield plugin
            for pm in _iter_elems(root, "pluginManagement"):
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
                        sig["testng"] = True
                    if "junit-platform" in dgid or "junit-platform" in daid or "junit-jupiter" in daid:
                        sig["junit5"] = True
            # plugin configuration (text)
            for conf in _iter_elems(plugin, "configuration"):
                conf_txt = ET.tostring(conf, encoding="unicode").lower()
                if "testng" in conf_txt:
                    sig["testng"] = True
                if "junitplatform" in conf_txt or "<provider>junit5" in conf_txt:
                    sig["junit5"] = True

    return sig

def decide_framework(sig: dict) -> str:
    # Priority: TestNG > JUnit5 > JUnit4
    if sig.get("testng"):  return "testng"
    if sig.get("junit5"):  return "junit5"
    if sig.get("junit4"):  return "junit4"
    return "unknown"

# ----- Option 2: find and parse ALL POMs; pick first decisive -----

FIND_POMS_CMD = r"""
set -e
# find up to 60 POMs quickly
find / -type f -name pom.xml -maxdepth 10 2>/dev/null | head -n 60
"""

def detect_framework_by_all_poms(image_tag: str):
    """
    Return (framework, chosen_pom_path, scanned_count)
    Scans up to 60 pom.xml files and returns the first with a decisive framework.
    If none decisive, aggregates signals across all POMs.
    """
    rc, out, err = docker_exec(image_tag, FIND_POMS_CMD, timeout=45)
    if rc != 0 or not out.strip():
        return "unknown", "", 0

    pom_paths = [p.strip() for p in out.splitlines() if p.strip()]
    scanned = 0
    aggregate = {"junit5": False, "junit4": False, "testng": False}

    for p in pom_paths:
        rc2, pom_txt, _ = docker_exec(image_tag, f"cat '{p}' 2>/dev/null", timeout=20)
        if rc2 != 0 or not pom_txt:
            continue
        scanned += 1

        sig = parse_single_pom_for_framework(pom_txt)

        # Decisive: any framework signal found in this POM
        fw = decide_framework(sig)
        if fw != "unknown":
            return fw, p, scanned

        # Not decisive yet; aggregate for fallback
        for k in aggregate:
            aggregate[k] = aggregate[k] or sig[k]

    # Fallback: decide from aggregate if any signal appeared at all
    fw = decide_framework(aggregate)
    return fw, (pom_paths[0] if pom_paths else ""), scanned

def main():
    # Open log
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w") as lf:
        lf.write("custom_id,breakingCommit,test_framework,decided_from_pom,num_poms_scanned\n")

    # Read input CSV
    with open(CSV_PATH_IN, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    # Prepare output header (append if missing)
    out_fieldnames = list(fieldnames)
    if "test_framework" not in out_fieldnames:
        out_fieldnames.append("test_framework")

    updated_rows = []
    total = len(rows)
    for idx, row in enumerate(rows, 1):
        custom_id = (row.get("custom_id") or "").strip()
        commit = (row.get("breakingCommit") or "").strip()

        if not commit:
            fw, pom_used, scanned = "unknown", "", 0
        else:
            image_tag = f"ghcr.io/chains-project/breaking-updates:{commit}-pre"
            fw, pom_used, scanned = detect_framework_by_all_poms(image_tag)

        # Update row
        row["test_framework"] = fw
        updated_rows.append(row)

        # Print to console
        prefix = f"[{idx}/{total}] {custom_id or '?'}"
        if pom_used:
            print(f"{prefix} → {fw} (via POM: {pom_used})")
        else:
            print(f"{prefix} → {fw} (no POM found or not decisive)")

        # Append to log
        with open(LOG_PATH, "a") as lf:
            lf.write(f"{custom_id},{commit},{fw},{pom_used},{scanned}\n")

    # Write output CSV with all original columns + new column at the end
    Path(CSV_PATH_OUT).parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH_OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)

    print(f"✅ Wrote updated CSV with test_framework column to: {CSV_PATH_OUT}")
    print(f"📝 Detailed log (framework & POM path): {LOG_PATH}")

if __name__ == "__main__":
    main()
