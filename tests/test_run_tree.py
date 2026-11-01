"""The harness ends the whole process tree on timeout (issue #398, AC3)."""

import subprocess
import sys
import time

import pytest

from tests.script_tools import run_tree


def test_timeout_kills_a_grandchild_holding_the_output_pipe():
    child = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match="timed out"):
        run_tree([sys.executable, "-c", child], timeout=2)
    assert time.monotonic() - started < 15


def test_normal_run_returns_output_and_exit_code():
    proc = run_tree([sys.executable, "-c", "print('hi'); raise SystemExit(3)"], timeout=30)
    assert (proc.returncode, proc.stdout.strip()) == (3, "hi")
    assert isinstance(proc, subprocess.CompletedProcess)
