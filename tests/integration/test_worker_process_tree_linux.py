"""Linux-only: a hung worker's real Chromium process tree must not survive the runner's stop."""
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from app.workers.bounded_runner import _popen_options, stop_process

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not Path("/proc").is_dir(), reason="requires Linux /proc"
)

CHILD = textwrap.dedent(
    """
    import sys, time
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            sys.argv[1], executable_path=sys.argv[2] or None, args=["--no-sandbox"]
        )
        ctx.new_page().goto("about:blank")
        print("READY", flush=True)
        time.sleep(600)
    """
)


def _chromium_path() -> str:
    """Optional override for hosts whose Playwright build differs from the installed browser."""
    explicit = os.environ.get("JOBPUNTER_TEST_CHROMIUM")
    if explicit:
        return explicit
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if root:
        found = sorted(Path(root).glob("chromium-*/chrome-linux*/chrome"))
        if found:
            return str(found[-1])
    return ""


def _marked_pids(marker: str) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().decode(errors="ignore")
        except OSError:
            continue
        if marker in cmd:
            found.append(int(entry.name))
    return found


def test_stop_process_leaves_no_chromium_descendants():
    with tempfile.TemporaryDirectory(prefix="jp-tree-") as marker:
        process = subprocess.Popen(
            [sys.executable, "-c", CHILD, marker, _chromium_path()],
            stdout=subprocess.PIPE,
            text=True,
            **_popen_options(),
        )
        try:
            assert process.stdout.readline().strip() == "READY"
            assert _marked_pids(marker), "chromium was expected to be running"
            stop_process(process, 2.0)
            deadline = time.monotonic() + 5
            while _marked_pids(marker) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert _marked_pids(marker) == []
        finally:
            if process.poll() is None:
                process.kill()


STUBBORN_CHILD = textwrap.dedent(
    """
    import signal, subprocess, sys, time
    subprocess.Popen([sys.executable, "-c",
        "import signal,time,sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)",
        sys.argv[1]])
    print("READY", flush=True)
    time.sleep(600)
    """
)


def _run_stubborn(marker: str, stop) -> list[int]:
    process = subprocess.Popen(
        [sys.executable, "-c", STUBBORN_CHILD, marker],
        stdout=subprocess.PIPE,
        text=True,
        **_popen_options(),
    )
    try:
        assert process.stdout.readline().strip() == "READY"
        time.sleep(0.5)
        assert _marked_pids(marker)
        stop(process)
        time.sleep(0.5)
        return _marked_pids(marker)
    finally:
        subprocess.run(["pkill", "-9", "-f", marker], check=False)
        if process.poll() is None:
            process.kill()


def test_stop_process_kills_sigterm_ignoring_grandchild():
    assert _run_stubborn("jp-stubborn-a", lambda p: stop_process(p, 1.0)) == []


def test_negative_control_leader_only_kill_would_leave_orphan():
    survivors = _run_stubborn("jp-stubborn-b", lambda p: (p.kill(), p.wait()))
    assert survivors, "test cannot discriminate: leader-only kill left nothing behind"
