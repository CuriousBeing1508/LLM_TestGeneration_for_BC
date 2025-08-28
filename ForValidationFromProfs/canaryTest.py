#!/usr/bin/env python3
import subprocess
import tempfile
from pathlib import Path
import shutil
import sys
import textwrap


# Wrote another script that collects the transplant directory for all images like thi
# ==== BBC19 | pre | ghcr.io/chains-project/breaking-updates:a3f4738330d23b9136044ae86c2093fba2c292e4-pre ====
# Test root: /IDS-Messaging-Services/appstore/src/test/java
# package: de.fraunhofer.ids.messaging.appstore
# Detected packages:
# de.fraunhofer.ids.messaging.appstore
# :::/IDS-Messaging-Services/appstore/src/test/java|package de.fraunhofer.ids.messaging.appstore;:::

# ==== BBC19 | breaking | ghcr.io/chains-project/breaking-updates:a3f4738330d23b9136044ae86c2093fba2c292e4-breaking ====
# Test root: /IDS-Messaging-Services/appstore/src/test/java
# package: de.fraunhofer.ids.messaging.appstore
# Detected packages:
# de.fraunhofer.ids.messaging.appstore
# :::/IDS-Messaging-Services/appstore/src/test/java|package de.fraunhofer.ids.messaging.appstore;:::


# ========= CONFIGURE THESE VALUES =========
PRE_IMAGE       = "ghcr.io/chains-project/breaking-updates:a3f4738330d23b9136044ae86c2093fba2c292e4-pre"
BREAK_IMAGE     = "ghcr.io/chains-project/breaking-updates:a3f4738330d23b9136044ae86c2093fba2c292e4-breaking"
TRANSPLANT_DIR  = "/IDS-Messaging-Services/appstore/src/test/java"  # inside container
PACKAGE_DECL    = "de.fraunhofer.ids.messaging.appstore"            # from above script
CLASS_NAME      = "HelloWorldTest"                                  # just a dummy test name 
# =========================================

def _print_header(title: str):
    bar = "=" * len(title)
    print(f"\n{bar}\n{title}\n{bar}")

def _docker(*args, check=True, **kwargs):
    return subprocess.run(["docker", *args], check=check, text=True, capture_output=True, **kwargs)

def _baseline_sanity_run(image: str) -> bool:
    """
    Baseline sanity = literally just `docker run IMAGE`.
    Mirrors:
      $ docker run ghcr.io/chains-project/breaking-updates:<tag>{-pre,-breaking}
    """
    _print_header(f"Baseline sanity run: {image}")
    res = subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64", image],
                         capture_output=True, text=True)
    if res.stdout.strip():
        print(f"\n--- baseline stdout ({image}) ---\n{res.stdout}")
    if res.stderr.strip():
        print(f"\n--- baseline stderr ({image}) ---\n{res.stderr}")
    ok = res.returncode == 0
    print(f"\n[baseline {image}] RESULT: {'OK' if ok else 'FAILED'} (exit={res.returncode})")
    return ok

def _run_mvn_only_this_test(container_name: str, at_path_inside_container: str):
    """
    After transplant, run only our test in offline mode (no dependency downloads).
      mvn -o -q -Dtest=HelloWorldTest test
    Repo root assumed three levels up from TRANSPLANT_DIR asumming it follows the src/test/java convention.
    """
    mvn_cmd = f"cd {at_path_inside_container}/../../.. && mvn -o -q -Dtest={CLASS_NAME} test"
    return subprocess.run(
        ["docker", "exec", container_name, "sh", "-lc", mvn_cmd],
        capture_output=True,
        text=True,
    )

def _start_container(image: str, name: str):
    _print_header(f"Starting container {name} from {image}")
    _docker("run", "--rm", "--platform", "linux/amd64", "--name", name, "-dit", image, "sh")

def _stop_container(name: str):
    subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _ensure_dir_in_container(name: str, path_inside: str):
    _docker("exec", name, "mkdir", "-p", path_inside)

def _transplant_test_file(name: str, dest_test_root: str, java_file_on_host: Path):
    # Ensure proper package directory structure under src/test/java
    pkg_path = PACKAGE_DECL.replace(".", "/")
    full_dest = f"{dest_test_root}/{pkg_path}"
    _docker("exec", name, "mkdir", "-p", full_dest)
    _docker("cp", str(java_file_on_host), f"{name}:{full_dest}/")

def _hello_world_java() -> str:
    return textwrap.dedent(f"""\
        package {PACKAGE_DECL};

        import org.junit.Test;
        import static org.junit.Assert.*;

        public class {CLASS_NAME} {{
            @Test
            public void testHello() {{
                String msg = "Hello World";
                assertEquals("Hello World", msg);
            }}
        }}
    """)

def _build_and_report(container_name: str, test_root_inside: str, label: str) -> bool:
    res = _run_mvn_only_this_test(container_name, test_root_inside)
    print(f"\n--- {label}: mvn test stdout ---\n{res.stdout}")
    if res.stderr.strip():
        print(f"\n--- {label}: mvn test stderr ---\n{res.stderr}")
    ok = (res.returncode == 0) and ("BUILD SUCCESS" in res.stdout)
    print(f"\n[{label}] RESULT: {'BUILD SUCCESS' if ok else 'BUILD FAILED'} (exit={res.returncode})")
    return ok

def test_image(image_tag: str, transplant_dir_inside: str):
    """
    Flow:
      1) Baseline sanity: `docker run IMAGE`
      2) Start a fresh container, transplant HelloWorldTest into proper package dir
      3) Run only that test with mvn -o -q -Dtest=HelloWorldTest test
    """
    # (1) Baseline sanity run
    baseline_ok = _baseline_sanity_run(image_tag)

    # (2) Transplant + run only our test
    container_name = f"canary_sanity_{image_tag.split(':')[-1].replace('/', '_').replace('.', '_')}"
    tmpdir = Path(tempfile.mkdtemp(prefix="canary_sanity_"))
    try:
        _start_container(image_tag, container_name)
        _ensure_dir_in_container(container_name, transplant_dir_inside)

        java_text = _hello_world_java()
        java_file = tmpdir / f"{CLASS_NAME}.java"
        java_file.write_text(java_text, encoding="utf-8")

        _print_header(f"Transplanting {CLASS_NAME}.java into {transplant_dir_inside}/{PACKAGE_DECL.replace('.', '/')}")
        _transplant_test_file(container_name, transplant_dir_inside, java_file)

        post_ok = _build_and_report(container_name, transplant_dir_inside, f"{image_tag} after transplant")
        return baseline_ok, post_ok
    finally:
        _stop_container(container_name)
        shutil.rmtree(tmpdir, ignore_errors=True)

def main():
    try:
        _docker("version")
    except subprocess.CalledProcessError as e:
        print("Docker does not seem to be available/running. Please start Docker Desktop or your daemon.")
        print(e.stderr)
        sys.exit(2)

    _print_header("Running tests on PRE image")
    pre_base_ok, pre_post_ok = test_image(PRE_IMAGE, TRANSPLANT_DIR)

    _print_header("Running tests on BREAKING image")
    brk_base_ok, brk_post_ok = test_image(BREAK_IMAGE, TRANSPLANT_DIR)

    _print_header("SUMMARY")
    print(f"PRE image   | baseline: {'OK' if pre_base_ok else 'FAIL'} | after transplant: {'OK' if pre_post_ok else 'FAIL'}")
    print(f"BREAK image | baseline: {'OK' if brk_base_ok else 'FAIL'} | after transplant: {'OK' if brk_post_ok else 'FAIL'}")

    any_fail = not (pre_base_ok and pre_post_ok and brk_base_ok and brk_post_ok)
    sys.exit(1 if any_fail else 0)

if __name__ == "__main__":
    main()
