"""The local pytest loop on any OS and checkout matches CI (issue #384).

Covers the per-session temp tree from ``conftest.py`` (no cross-run deletion, outside
git, ``PYTEST_DEBUG_TEMPROOT`` kept), the Git Bash and ``PATH`` selection in
``tests/script_tools.py`` with an injected ``which`` and ``os_name``, and the LF rule
for shell scripts in ``.gitattributes``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import script_tools
from tests.script_tools import (
    find_bash, git_bash_dirs, host_script_path, missing_tools, script_path, windows_env,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# A test in a scratch checkout: leaves a sentinel in its tmp_path and reports where that is.
SESSION_TEST = '''
import os
from pathlib import Path

def test_leave_sentinel(tmp_path):
    (tmp_path / "sentinel").write_text("alive")
    Path(os.environ["REPORT"]).write_text(str(tmp_path))
'''


def make_checkout(path: Path) -> Path:
    """A scratch checkout with this repository's real conftest.py and one test."""
    path.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "conftest.py", path / "conftest.py")
    (path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (path / "test_session.py").write_text(SESSION_TEST, encoding="utf-8")
    return path


def run_session(checkout: Path, report: Path, temproot: str | None = None) -> Path:
    """Run pytest in ``checkout`` as a new session and return the tmp_path its test used."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_DEBUG_TEMPROOT", "PYTEST_ADDOPTS")}
    if temproot is not None:
        env["PYTEST_DEBUG_TEMPROOT"] = temproot
    env["REPORT"] = str(report)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT / "src"), env.get("PYTHONPATH", "")])
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist"],
                          cwd=checkout, env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return Path(report.read_text(encoding="utf-8"))


# ── AC1: no cross-run deletion ──────────────────────────────────────────────


def test_a_session_in_a_sibling_checkout_keeps_the_first_sessions_tree(tmp_path):
    first = run_session(make_checkout(tmp_path / "main"), tmp_path / "first.txt")
    second = run_session(make_checkout(tmp_path / "hpw-issue-376"), tmp_path / "second.txt")
    assert first != second
    assert (first / "sentinel").read_text() == "alive"
    assert (second / "sentinel").read_text() == "alive"


def test_a_second_session_in_the_same_checkout_keeps_the_first_sessions_tree(tmp_path):
    checkout = make_checkout(tmp_path / "main")
    first = run_session(checkout, tmp_path / "first.txt")
    second = run_session(checkout, tmp_path / "second.txt")
    assert first != second
    assert (first / "sentinel").read_text() == "alive"


# ── AC2: temp base outside git ─────────────────────────────────────────────


def test_session_tree_is_next_to_the_checkout_not_inside_it(tmp_path):
    checkout = make_checkout(tmp_path / "main")
    used = run_session(checkout, tmp_path / "report.txt")
    assert used.is_relative_to((tmp_path / ".pytest_tmp").resolve())
    assert not used.is_relative_to(checkout.resolve())


@pytest.mark.skipif(shutil.which("git") is None, reason="requires git")
def test_tmp_path_is_outside_any_git_working_tree(tmp_path):
    proc = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode != 0, f"tmp_path {tmp_path} is inside a git working tree: {proc.stdout}"


# ── AC3: PYTEST_DEBUG_TEMPROOT override ─────────────────────────────────────


def test_pytest_debug_temproot_is_used_when_set(tmp_path):
    custom = tmp_path / "custom-root"
    custom.mkdir()
    used = run_session(make_checkout(tmp_path / "main"), tmp_path / "report.txt", temproot=str(custom))
    assert used.is_relative_to(custom.resolve())
    assert not (tmp_path / ".pytest_tmp").exists()


def test_configure_temproot_keeps_an_explicit_value(tmp_path):
    from conftest import configure_temproot

    environ = {"PYTEST_DEBUG_TEMPROOT": "C:\\tmp"}
    configure_temproot(environ, tmp_path / "checkout")
    assert environ == {"PYTEST_DEBUG_TEMPROOT": "C:\\tmp"}
    environ = {}
    configure_temproot(environ, tmp_path / "checkout")
    assert environ == {"PYTEST_DEBUG_TEMPROOT": str(tmp_path / ".pytest_tmp")}


def test_explicit_basetemp_skips_the_default_root_when_its_parent_is_unavailable(tmp_path):
    from conftest import configure_temproot

    blocked = tmp_path / "not-a-dir"  # a file, so <blocked>/.pytest_tmp cannot be created
    blocked.write_text("")
    checkout = blocked / "checkout"
    with pytest.raises(OSError):
        configure_temproot({}, checkout)
    environ: dict[str, str] = {}
    configure_temproot(environ, checkout, basetemp=tmp_path / "writable")
    assert environ == {}


def test_basetemp_on_the_command_line_runs_where_the_default_root_is_unavailable(tmp_path):
    checkout = make_checkout(tmp_path / "checkout")
    (tmp_path / ".pytest_tmp").write_text("")  # a file, so the default root cannot be created
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_DEBUG_TEMPROOT", "PYTEST_ADDOPTS")}
    env["REPORT"] = str(tmp_path / "report.txt")
    basetemp = tmp_path / "chosen"
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                           f"--basetemp={basetemp}"], cwd=checkout, env=env, capture_output=True, text=True,
                          timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert Path((tmp_path / "report.txt").read_text(encoding="utf-8")).is_relative_to(basetemp.resolve())


# ── AC4: Git Bash and PATH selection, on any OS ─────────────────────────────

GIT_ROOT = r"C:\Program Files\Git"
WSL_BASH = r"C:\Windows\System32\bash.exe"


def fake_which(table: dict[str, str]):
    return lambda name: table.get(name)


def fake_isfile(*paths: str):
    present = {p.lower() for p in paths}
    return lambda path: path.lower() in present


def test_windows_prefers_git_bash_over_the_wsl_launcher_on_path():
    which = fake_which({"bash": WSL_BASH, "git": GIT_ROOT + r"\mingw64\bin\git.exe"})
    isfile = fake_isfile(GIT_ROOT + r"\usr\bin\bash.exe", WSL_BASH)
    assert find_bash(which, "nt", isfile) == GIT_ROOT + r"\usr\bin\bash.exe"


def test_windows_finds_git_bash_from_git_in_cmd():
    which = fake_which({"git": GIT_ROOT + r"\cmd\git.exe"})
    assert find_bash(which, "nt", fake_isfile(GIT_ROOT + r"\bin\bash.exe")) == GIT_ROOT + r"\bin\bash.exe"


@pytest.mark.parametrize("bash", [WSL_BASH, r"C:\Users\u\AppData\Local\Microsoft\WindowsApps\bash.exe"])
def test_windows_never_returns_the_wsl_bash(bash):
    assert find_bash(fake_which({"bash": bash}), "nt", fake_isfile(bash)) is None
    assert missing_tools(["bash", "jq"], fake_which({"bash": bash, "jq": "jq.exe"}), "nt", fake_isfile(bash)) == ["bash"]


def test_windows_falls_back_to_a_non_system_bash_on_path():
    bash = r"D:\msys64\usr\bin\bash.exe"
    assert find_bash(fake_which({"bash": bash}), "nt", fake_isfile()) == bash


def test_posix_uses_bash_from_path():
    assert find_bash(fake_which({"bash": "/usr/bin/bash"}), "posix", fake_isfile()) == "/usr/bin/bash"


def test_missing_tools_names_each_missing_tool():
    which = fake_which({"bash": "/bin/bash", "git": "/usr/bin/git"})
    assert missing_tools(["bash", "jq", "git", "python3"], which, "posix") == ["jq", "python3"]


POWERSHELL_PATH = r"C:\Windows\system32;C:\Program Files\Git\cmd;C:\ProgramData\chocolatey\lib\jq\tools"


@pytest.mark.parametrize("bash", [GIT_ROOT + r"\usr\bin\bash.exe", GIT_ROOT + r"\bin\bash.exe"])
def test_windows_path_from_powershell_gets_git_bash_coreutils_before_the_host_path(bash):
    # A PowerShell PATH has Git's cmd dir only, so dirname/mktemp were "command not found" (issue #384).
    path = script_path("C:/t/bin", "nt", POWERSHELL_PATH, bash=bash).split(";")
    assert path[:3] == ["C:/t/bin", GIT_ROOT + r"\usr\bin", GIT_ROOT + r"\mingw64\bin"]
    assert path[3:] == POWERSHELL_PATH.split(";")  # jq stays reachable, after Git's own timeout and coreutils


def test_host_script_path_keeps_the_host_path_and_adds_git_bash_dirs_on_windows():
    bash = GIT_ROOT + r"\usr\bin\bash.exe"
    assert host_script_path("C:/t/bin", "nt", POWERSHELL_PATH, bash=bash).split(";")[:3] == [
        "C:/t/bin", GIT_ROOT + r"\usr\bin", GIT_ROOT + r"\mingw64\bin"]
    assert host_script_path(None, "posix", "/opt/jq/bin:/usr/bin") == "/opt/jq/bin:/usr/bin"
    assert git_bash_dirs(None) == []


def test_posix_path_stays_narrow():
    assert script_path("/t/bin", "posix", "/opt/jq/bin:/usr/bin") == "/t/bin:/usr/bin:/bin"


def test_windows_env_keeps_system_variables_only_on_windows():
    environ = {"SYSTEMROOT": r"C:\Windows", "SYSTEMDRIVE": "C:", "SECRET": "x"}
    assert windows_env("nt", environ) == {"SYSTEMROOT": r"C:\Windows", "SYSTEMDRIVE": "C:"}
    assert windows_env("posix", environ) == {}


def test_windows_env_points_mktemp_at_the_pytest_temp_root():
    # Git Bash's /tmp can fall back to C:\Program Files\Git\tmp, which only administrators may write.
    environ = {"PYTEST_DEBUG_TEMPROOT": r"C:\Users\u\Dev\.pytest_tmp", "TEMP": r"C:\Users\u\AppData\Local\Temp"}
    assert windows_env("nt", environ)["TMPDIR"] == "C:/Users/u/Dev/.pytest_tmp"
    assert windows_env("nt", {"TEMP": r"C:\t"})["TMPDIR"] == "C:/t"
    assert "TMPDIR" not in windows_env("nt", {})


@pytest.mark.skipif(os.name != "nt" or script_tools.BASH is None, reason="Git Bash on Windows only")
def test_mktemp_in_git_bash_uses_tmpdir_not_slash_tmp():
    env = {**windows_env(), "PATH": host_script_path()}
    proc = subprocess.run([script_tools.BASH, "-c", 'f=$(mktemp) && cygpath -m "$f" && rm -f "$f"'], env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().lower().startswith(env["TMPDIR"].lower())


def test_requires_skip_reason_names_the_missing_tool(monkeypatch):
    monkeypatch.setattr(script_tools, "missing_tools", lambda tools: ["jq"])
    mark = script_tools.requires("bash", "jq")
    assert mark.args == (True,) and "jq" in mark.kwargs["reason"]
    monkeypatch.setattr(script_tools, "missing_tools", lambda tools: [])
    assert script_tools.requires("bash").args == (False,)


# ── AC5: LF shell scripts ───────────────────────────────────────────────────


def shell_scripts() -> list[str]:
    files = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True,
                           check=True).stdout.split("\0")
    scripts = [f for f in files if f.endswith(".sh")]
    for name in files:
        if name.startswith("scripts/") and "." not in Path(name).name:
            first = (REPO_ROOT / name).read_bytes().split(b"\n", 1)[0]
            if first.startswith(b"#!"):
                scripts.append(name)
    return scripts


@pytest.mark.skipif(shutil.which("git") is None or not (REPO_ROOT / ".git").exists(), reason="requires a git checkout")
def test_every_shell_script_is_checked_out_with_lf():
    scripts = shell_scripts()
    assert "scripts/hpw" in scripts and "scripts/set-project-status" in scripts
    out = subprocess.run(["git", "check-attr", "eol", "--", *scripts], cwd=REPO_ROOT, capture_output=True,
                         text=True, check=True).stdout
    not_lf = [line for line in out.splitlines() if not line.endswith(": eol: lf")]
    assert not not_lf, f"missing eol=lf in .gitattributes: {not_lf}"
