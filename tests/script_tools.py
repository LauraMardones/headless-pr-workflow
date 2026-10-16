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
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import PureWindowsPath

import pytest

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


def script_path(fake_bin: str, os_name: str = os.name, host_path: str | None = None) -> str:
    """``PATH`` for a script under test: the fakes first, then the narrow system dirs or, on Windows, the host ``PATH``."""
    if os_name == "nt":
        host = os.environ.get("PATH", "") if host_path is None else host_path
        return ";".join([fake_bin, host]) if host else fake_bin
    return ":".join([fake_bin, "/usr/bin", "/bin"])


# Windows processes misbehave without these, e.g. a child creates a literal ``%SystemDrive%`` folder
# in its working directory, which makes a test repository dirty.
WINDOWS_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PROGRAMDATA", "COMSPEC", "PATHEXT")


def windows_env(os_name: str = os.name, environ: dict[str, str] | None = None) -> dict[str, str]:
    """The host's Windows system variables to keep in a script's narrow environment; empty elsewhere."""
    if os_name != "nt":
        return {}
    source = os.environ if environ is None else environ
    return {name: source[name] for name in WINDOWS_ENV if name in source}


BASH = find_bash()
