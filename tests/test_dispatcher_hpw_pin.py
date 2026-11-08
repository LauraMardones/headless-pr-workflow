"""The dispatcher runs ``hpw`` from the commit the run started on (issue #497).

A session leaves its story branch checked out, and ``scripts/hpw`` runs the ``src/`` of the
checkout it sits in. The tests run the real ``scripts/dispatcher-invoke.sh`` from a throwaway
git copy of the scripts whose ``hpw`` code is a stub that logs a marker and hands the call to
the fake ``hpw`` of ``tests/dispatcher_fakes.py``. ``HPW_BIN`` is left unset, so the default
(pinned) ``hpw`` is the one that runs.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

import tests.dispatcher_fakes as fakes
from tests.dispatcher_fakes import REPO_ROOT, Dispatcher, needs_tools

pytestmark = needs_tools

STORY = 700

STUB = '''import os, sys
with open(os.path.join(os.environ["MOCK_DIR"], "pin.log"), "a") as log:
    log.write("{marker} " + " ".join(sys.argv[1:3]) + "\\n")
os.execv(os.environ["FAKE_HPW"], ["hpw", "dispatcher", *sys.argv[1:]])
'''


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   capture_output=True)


def write_stub(repo, marker: str) -> None:
    pkg = repo / "src" / "headless_pr_workflow" / "dispatcher"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text(STUB.format(marker=marker))


@pytest.fixture
def copy(tmp_path, monkeypatch):
    repo = tmp_path / "checkout"
    for name in ("scripts", ".claude", "src"):
        shutil.copytree(REPO_ROOT / name, repo / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repo / "src" / "headless_pr_workflow" / "dispatcher")
    write_stub(repo, "main-code")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    git(repo, "switch", "-q", "-c", "story")
    write_stub(repo, "branch-code")
    git(repo, "commit", "-q", "-am", "older code")
    git(repo, "switch", "-q", "main")
    monkeypatch.setattr(fakes, "INVOKE", repo / "scripts" / "dispatcher-invoke.sh")
    return repo


def run(tmp_path, copy, **env):
    d = (tmp_path / "fx").mkdir() or Dispatcher(tmp_path / "fx")
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(970, STORY)
    d.schedule(("implement", STORY), ("idle",))
    d.on_session("implement", f'git -C "{copy.as_posix()}" switch -q story\n')
    proc = d.run(env={"HPW_BIN": "", "FAKE_HPW": (d.bin / "hpw").as_posix(), **env})
    return d, proc


def markers(d, command):
    log = (d.mock / "pin.log").read_text().splitlines()
    return [line.split()[0] for line in log if line.endswith(command)]


def test_ac2_the_scheduler_call_after_a_session_uses_the_start_commits_code(tmp_path, copy):
    d, proc = run(tmp_path, copy)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=copy, capture_output=True, text=True).stdout
    assert branch.strip() == "story"  # the session left the branch checked out
    calls = markers(d, "scheduler next")
    assert len(calls) >= 2, proc.stdout
    assert set(calls) == {"main-code"}
    assert "hpw is pinned to the run's start commit" in proc.stdout


def test_ac4_no_copy_of_the_start_commit_starts_no_session(tmp_path, copy):
    missing = (tmp_path / "no-such-dir").as_posix()
    d, proc = run(tmp_path, copy, TMPDIR=missing)

    assert proc.returncode != 0
    assert d.sessions == []
    assert "cannot pin hpw to the run's start commit" in proc.stderr


def test_the_pinned_copy_is_removed_at_exit(tmp_path, copy):
    pins = tmp_path / "pins"
    pins.mkdir()
    d, proc = run(tmp_path, copy, TMPDIR=pins.as_posix())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert list(pins.iterdir()) == []
