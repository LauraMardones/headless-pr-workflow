"""Shared bash and ``PATH`` selection for tests that run the repository's shell scripts (issue #384).

On Linux a harness runs scripts with a narrow ``PATH`` (the fake bin, ``/usr/bin``,
``/bin``), so CI still catches a script that uses a tool it should not. On Windows
that ``PATH`` hides ``jq`` (e.g. Chocolatey's ``C:\\ProgramData\\chocolatey\\lib\\jq\\tools``),
``git`` and ``python3``, so the harness keeps the host ``PATH`` behind the fakes.

On Windows ``bash`` must be Git Bash: ``shutil.which("bash")`` can return the WSL
launcher ``C:\\Windows\\System32\\bash.exe`` (and ``subprocess`` resolves a bare
``"bash"`` to it before ``PATH``), so Git Bash is located from the ``git`` executable
first. Every function takes ``which``, ``os_name`` and ``isfile`` so the selection
is testable on any OS.

Two Git Bash habits break scripts that pass on Linux (issue #398), so on Windows
``windows_env`` sets two variables. ``BASH_ENV`` points at ``tests/windows_bash_env.sh``,
which defines ``jq`` as ``jq -b``: a native ``jq.exe`` ends lines with CRLF and
``$(jq -r ...)`` then keeps a trailing ``\\r``. A function, unlike a wrapper script,
starts no extra process, so the dispatcher is not slowed down. And
``MSYS2_ARG_CONV_EXCL`` names the slash commands, because Git Bash rewrites an
argument such as ``/review #398`` to ``C:/Program Files/Git/review #398`` before a
native tool sees it. Paths such as ``/tmp/...`` are still converted.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path, PureWindowsPath

import pytest

# Windows-only bash start-up file that makes jq write LF (see the module docstring).
WINDOWS_BASH_ENV = (Path(__file__).resolve().parent / "windows_bash_env.sh").as_posix()

# Slash commands the scripts pass to native tools, e.g. ``jq --arg label "/review #398"``.
# Git Bash leaves an argument that starts with one of these unconverted (issue #398).
SLASH_COMMANDS = ("/cleanup", "/implement", "/merge", "/refine", "/review", "/spec", "/unblock", "/verify-closure")

Which = Callable[[str], "str | None"]


def _is_windows_launcher(path: str) -> bool:
    """True for the WSL ``bash.exe`` in System32 or the WindowsApps alias, which are not Git Bash."""
    parts = [p.lower() for p in PureWindowsPath(path).parts]
    return "system32" in parts or "windowsapps" in parts


def find_bash(which: Which = shutil.which, os_name: str = os.name,
              isfile: Callable[[str], bool] = os.path.isfile) -> str | None:
    """Return the bash to run scripts with: Git Bash on Windows, ``which("bash")`` elsewhere."""
    if os_name != "nt":
        return which("bash")
    git = which("git")
    if git:
        # git.exe lives in <root>\cmd, <root>\bin or <root>\mingw64\bin; bash.exe in <root>\usr\bin or <root>\bin.
        for root in list(PureWindowsPath(git).parents)[:3]:
            for candidate in (root / "usr" / "bin" / "bash.exe", root / "bin" / "bash.exe"):
                if isfile(str(candidate)):
                    return str(candidate)
    bash = which("bash")
    if bash and not _is_windows_launcher(bash):
        return bash
    return None


def missing_tools(tools: Iterable[str], which: Which = shutil.which, os_name: str = os.name,
                  isfile: Callable[[str], bool] = os.path.isfile) -> list[str]:
    """Names of the required tools that cannot be found; ``bash`` means Git Bash on Windows."""
    missing = []
    for tool in tools:
        found = find_bash(which, os_name, isfile) if tool == "bash" else which(tool)
        if not found:
            missing.append(tool)
    return missing


def requires(*tools: str) -> pytest.MarkDecorator:
    """``skipif`` mark that names every missing tool, e.g. ``requires jq (not found: jq)``."""
    missing = missing_tools(tools)
    label = "Git Bash" if os.name == "nt" else "bash"
    names = [label if t == "bash" else t for t in missing]
    return pytest.mark.skipif(bool(missing), reason=f"requires {', '.join(tools)} (not found: {', '.join(names)})")


def git_bash_dirs(bash: str | None) -> list[str]:
    """Git for Windows' ``usr\\bin`` (coreutils) and ``mingw64\\bin`` for a Git Bash ``bash.exe``.

    A PowerShell or cmd ``PATH`` has neither, so a script run there cannot find ``dirname`` or ``mktemp``.
    """
    if not bash:
        return []
    parent = PureWindowsPath(bash).parent
    is_usr_bin = parent.name.lower() == "bin" and parent.parent.name.lower() == "usr"
    root = parent.parent.parent if is_usr_bin else parent.parent
    return [str(root / "usr" / "bin"), str(root / "mingw64" / "bin")]


def script_path(fake_bin: str, os_name: str = os.name, host_path: str | None = None,
                bash: str | None = None) -> str:
    """``PATH`` for a script under test: the fakes first, then the narrow system dirs or, on Windows,
    Git Bash's own tool dirs and the host ``PATH``."""
    if os_name == "nt":
        host = os.environ.get("PATH", "") if host_path is None else host_path
        dirs = git_bash_dirs(BASH if bash is None else bash)
        return ";".join(p for p in [fake_bin, *dirs, host] if p)
    return ":".join([fake_bin, "/usr/bin", "/bin"])


def host_script_path(fake_bin: str | None = None, os_name: str = os.name, host_path: str | None = None,
                     bash: str | None = None) -> str:
    """``PATH`` for a harness that keeps the host ``PATH`` on every OS, with Git Bash's tool dirs on Windows."""
    host = os.environ.get("PATH", "") if host_path is None else host_path
    if os_name == "nt":
        dirs = git_bash_dirs(BASH if bash is None else bash)
        return ";".join(p for p in [fake_bin, *dirs, host] if p)
    return ":".join(p for p in [fake_bin, host] if p)


# Windows processes misbehave without these, e.g. a child creates a literal ``%SystemDrive%`` folder
# in its working directory, which makes a test repository dirty.
WINDOWS_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PROGRAMDATA", "COMSPEC", "PATHEXT")


def windows_env(os_name: str = os.name, environ: dict[str, str] | None = None) -> dict[str, str]:
    """The host's Windows system variables to keep in a script's environment; empty elsewhere.

    Also sets ``TMPDIR`` for ``mktemp``: Git Bash's ``/tmp`` can resolve to ``C:\\Program Files\\Git\\tmp``,
    which only administrators may write (issue #384). The pytest temp root is known to be writable.
    """
    if os_name != "nt":
        return {}
    source = os.environ if environ is None else environ
    kept = {name: source[name] for name in WINDOWS_ENV if name in source}
    kept["MSYS2_ARG_CONV_EXCL"] = ";".join(SLASH_COMMANDS)
    kept["BASH_ENV"] = WINDOWS_BASH_ENV
    tmpdir = source.get("PYTEST_DEBUG_TEMPROOT") or source.get("TEMP") or source.get("TMP")
    if tmpdir:
        kept["TMPDIR"] = PureWindowsPath(tmpdir).as_posix()
    return kept


def host_env(fake_bin: str | None = None) -> dict[str, str]:
    """A copy of the host environment for a script under test, with the Windows additions above."""
    return {**os.environ, **windows_env(), "PATH": host_script_path(fake_bin)}


BASH = find_bash()


def run_tree(cmd: list[str], *, timeout: float, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` that ends the whole process tree when ``timeout`` expires (issue #398).

    ``subprocess.run`` kills only the direct child; a grandchild that inherited the output
    pipes keeps them open and ``communicate()`` then waits forever. The child starts in its
    own process group (POSIX) or process group tree (Windows ``taskkill /T``); on timeout the
    whole tree is killed, the pipes are drained and ``pytest.fail`` reports the timeout.
    """
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE if kwargs.pop("capture_output") else None,
                          stderr=subprocess.PIPE, **kwargs) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.kill()
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            pytest.fail(f"{cmd[:3]} timed out after {timeout}s; the whole process tree was killed")
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
