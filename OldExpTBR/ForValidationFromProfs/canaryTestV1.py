#!/usr/bin/env python3
import subprocess
import tempfile
from pathlib import Path
import sys
import textwrap

# ========= CONFIGURE THESE VALUES =========
PRE_IMAGE      = "ghcr.io/chains-project/breaking-updates:a3f4738330d23b9136044ae86c2093fba2c292e4-pre"
BREAK_IMAGE    = "ghcr.io/chains-project/breaking-updates:a3f4738330d23b9136044ae86c2093fba2c292e4-breaking"
TEST_ROOT_IN_CONTAINER = "/IDS-Messaging-Services/appstore/src/test/java"
PACKAGE_DECL  = "de.fraunhofer.ids.messaging.appstore"
CLASS_NAME    = "HelloWorldTest"
# =========================================

def _print_header(title: str):
    bar = "=" * len(title)
    print(f"\n{bar}\n{title}\n{bar}")

def _docker_run(image: str, extra_args=None):
    cmd = ["docker", "run", "--rm", "--platform", "linux/amd64"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(image)
    return subprocess.run(cmd, capture_output=True, text=True)

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

def _container_test_path() -> str:
    # mount to the exact file path so we don't mask other tests
    pkg_path = PACKAGE_DECL.replace(".", "/")
    return f"{TEST_ROOT_IN_CONTAINER}/{pkg_path}/{CLASS_NAME}.java"

def _baseline(image: str) -> bool:
    _print_header(f"Baseline sanity: docker run {image}")
    res = _docker_run(image)
    if res.stdout.strip():
        print(f"\n--- baseline stdout ({image}) ---\n{res.stdout}")
    if res.stderr.strip():
        print(f"\n--- baseline stderr ({image}) ---\n{res.stderr}")
    ok = res.returncode == 0
    print(f"[baseline {image}] RESULT: {'OK' if ok else 'FAILED'} (exit={res.returncode})")
    return ok

def _after_transplant(image: str) -> bool:
    # create a temp HelloWorldTest.java and bind-mount it into the container at the exact path
    tmpdir = Path(tempfile.mkdtemp(prefix="canary_mount_"))
    try:
        host_file = tmpdir / f"{CLASS_NAME}.java"
        host_file.write_text(_hello_world_java(), encoding="utf-8")

        dest_in_container = _container_test_path()
        _print_header(f"Transplant via bind-mount → {dest_in_container}")

        res = _docker_run(
            image,
            extra_args=["-v", f"{host_file}:{dest_in_container}:ro"]
        )
        if res.stdout.strip():
            print(f"\n--- after-transplant stdout ({image}) ---\n{res.stdout}")
        if res.stderr.strip():
            print(f"\n--- after-transplant stderr ({image}) ---\n{res.stderr}")
        ok = res.returncode == 0
        print(f"[after transplant {image}] RESULT: {'OK' if ok else 'FAILED'} (exit={res.returncode})")
        return ok
    finally:
        # temp dir auto-clean not strictly needed; leave as-is or uncomment to remove
        # import shutil; shutil.rmtree(tmpdir, ignore_errors=True)
        pass

def test_image(image: str):
    base_ok = _baseline(image)
    post_ok = _after_transplant(image)
    return base_ok, post_ok

def main():
    # quick docker availability check
    try:
        subprocess.run(["docker", "version"], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print("Docker is not available/running. Start Docker and try again.")
        print(e.stderr)
        sys.exit(2)

    _print_header("Running on PRE image")
    pre_base_ok, pre_post_ok = test_image(PRE_IMAGE)

    _print_header("Running on BREAKING image")
    brk_base_ok, brk_post_ok = test_image(BREAK_IMAGE)

    _print_header("SUMMARY")
    print(f"PRE image   | baseline: {'OK' if pre_base_ok else 'FAIL'} | after transplant: {'OK' if pre_post_ok else 'FAIL'}")
    print(f"BREAK image | baseline: {'OK' if brk_base_ok else 'FAIL'} | after transplant: {'OK' if brk_post_ok else 'FAIL'}")

    any_fail = not (pre_base_ok and pre_post_ok and brk_base_ok and brk_post_ok)
    sys.exit(1 if any_fail else 0)

if __name__ == "__main__":
    main()
