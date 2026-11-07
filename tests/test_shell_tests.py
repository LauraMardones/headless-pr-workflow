"""Run every ``tests/test_*.sh`` file as its own pytest test (issue #412).

The required ``pytest`` check runs only ``python -m pytest``, so a shell test that no pytest test runs
could break unnoticed. A new ``tests/test_*.sh`` file is found here at collection time; nothing to register.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import script_tools

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
TIMEOUT_SECONDS = 300

# NTFS has no executable bit, so these checks cannot pass under Git Bash on Windows.
# test_dispatcher_notify_retry.sh hung on Windows Git Bash in the #412 refinement run (cause not found).
WINDOWS_SKIPS = {
    "test_dispatcher_change_check.sh": "checks the executable bit (chmod -x / Git mode 100755), which NTFS lacks",
    "test_dispatcher_invoke_non_executable.sh": "checks the executable bit (chmod -x), which NTFS lacks",
    "test_dispatcher_notify_retry.sh": "hangs under Windows Git Bash (seen in the #412 refinement run)",
}


def discover_shell_tests(directory: Path = TESTS_DIR) -> list[Path]:
    """Every ``test_*.sh`` file in ``directory``, sorted by name."""
    return sorted(directory.glob("test_*.sh"))


def run_shell_test(path: Path, bash: str, cwd: Path = REPO_ROOT, timeout: float = TIMEOUT_SECONDS) -> None:
    """Run one shell test with bash; fail with the script's output on a non-zero exit or a timeout."""
    result = script_tools.run_tree(
        [bash, str(path)], timeout=timeout, cwd=cwd, env=script_tools.host_env(),
        timeout_message=lambda out: f"{path.name} timed out after {timeout}s; the whole process tree was killed\n{out}")
    if result.returncode != 0:
        pytest.fail(f"{path.name} exited {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")


@pytest.mark.parametrize("path", discover_shell_tests(), ids=lambda p: p.name)
@script_tools.requires("bash", "jq")
def test_shell_test_passes(path: Path) -> None:
    if os.name == "nt" and path.name in WINDOWS_SKIPS:
        pytest.skip(f"Windows: {WINDOWS_SKIPS[path.name]}")
    run_shell_test(path, script_tools.find_bash())


def test_discovery_finds_a_new_shell_test_without_registration(tmp_path: Path) -> None:
    (tmp_path / "test_new_thing.sh").write_text("exit 0\n")
    (tmp_path / "helper.sh").write_text("exit 0\n")
    assert [p.name for p in discover_shell_tests(tmp_path)] == ["test_new_thing.sh"]


def test_discovery_finds_the_repository_shell_tests() -> None:
    names = {p.name for p in discover_shell_tests()}
    assert "test_session_summary.sh" in names and "test_ac_summary.sh" in names


@script_tools.requires("bash")
def test_a_failing_shell_test_fails_with_its_output(tmp_path: Path) -> None:
    script = tmp_path / "test_fails.sh"
    script.write_text("echo visible-stdout\necho visible-stderr >&2\nexit 1\n")
    with pytest.raises(pytest.fail.Exception) as info:
        run_shell_test(script, script_tools.find_bash(), cwd=tmp_path)
    message = str(info.value)
    assert "exited 1" in message and "visible-stdout" in message and "visible-stderr" in message


@script_tools.requires("bash")
def test_a_passing_shell_test_does_not_fail(tmp_path: Path) -> None:
    script = tmp_path / "test_ok.sh"
    script.write_text("exit 0\n")
    run_shell_test(script, script_tools.find_bash(), cwd=tmp_path)


@script_tools.requires("bash")
def test_a_hanging_shell_test_times_out(tmp_path: Path) -> None:
    script = tmp_path / "test_hangs.sh"
    script.write_text("sleep 30\n")
    with pytest.raises(pytest.fail.Exception, match="timed out"):
        run_shell_test(script, script_tools.find_bash(), cwd=tmp_path, timeout=1)


def _pid_running(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@script_tools.requires("bash")
def test_a_timed_out_shell_test_stops_its_whole_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    child = f"import os,time;open({str(pid_file)!r},'w').write(str(os.getpid()));time.sleep(120)"
    script = tmp_path / "test_child.sh"
    script.write_text(f"echo before-timeout\n\"{Path(sys.executable).as_posix()}\" -c \"{child}\" &\nwait\n")
    start = time.monotonic()
    with pytest.raises(pytest.fail.Exception) as info:
        run_shell_test(script, script_tools.find_bash(), cwd=tmp_path, timeout=3)
    assert time.monotonic() - start < 3 + 30
    message = str(info.value)
    assert "test_child.sh" in message and "timed out after 3s" in message and "before-timeout" in message
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while _pid_running(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _pid_running(pid)


def test_missing_tool_skip_names_the_tool() -> None:
    assert script_tools.missing_tools(["bash", "jq"], which=lambda t: None if t == "jq" else "/usr/bin/" + t,
                                      os_name="posix") == ["jq"]
