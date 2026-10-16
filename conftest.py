from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def default_temproot(conftest_dir: Path) -> Path:
    """``.pytest_tmp`` one level above the checkout: outside the git working tree."""
    return conftest_dir.parent / ".pytest_tmp"


def configure_temproot(environ: dict[str, str] | os._Environ, conftest_dir: Path,
                       basetemp: str | os.PathLike[str] | None = None) -> None:
    """Point pytest's own numbered temp root at ``default_temproot`` unless the user chose a temp location.

    Avoids two problems in sandboxed environments (e.g. Codex):
    1. The system temp directory may not be writable.
    2. A temp dir inside the repo causes git-detection to succeed for tests
       that create subdirectories expecting a non-git environment.

    pytest then gives every session its own locked ``pytest-of-<user>/pytest-<N>``
    tree under that root, keeps the last 3 and never removes a tree that a running
    session still holds; xdist workers get ``popen-gwN`` inside their session's tree.
    An explicit ``basetemp`` would instead be emptied at every session start, so a
    run in a sibling checkout (or a second run here) deleted the first run's files
    (issue #384). With PYTEST_DEBUG_TEMPROOT set or ``--basetemp`` given, nothing
    is created or changed, so a run works even where the default root's parent is
    not writable.
    """
    if environ.get("PYTEST_DEBUG_TEMPROOT") or basetemp:
        return
    root = default_temproot(conftest_dir)
    root.mkdir(parents=True, exist_ok=True)
    environ["PYTEST_DEBUG_TEMPROOT"] = str(root)


def pytest_configure(config: object) -> None:
    basetemp = getattr(getattr(config, "option", None), "basetemp", None)
    configure_temproot(os.environ, Path(__file__).resolve().parent, basetemp)


# Tests that summarize CI without an explicit repo_root read the policy relative to
# the current directory, i.e. this repository's live docs/required-check-policy.json.
# Pin those reads to the "no required checks" policy that main had before #322, so that
# the live file's switch to "required" does not change unrelated tests.
_REPO_ROOT = Path(__file__).resolve().parent
_PINNED_POLICY = {
    "schema": "headless-pr-workflow.required-check-policy.v1",
    "branches": {
        "main": {
            "required_status_checks": "absent",
            "ci_workflows": "present_non_required",
            "source": "docs/MERGE-POLICY.md#main-required-check-policy",
        }
    },
}


@pytest.fixture(scope="session")
def _pinned_policy_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("pinned-policy")
    (root / "docs").mkdir()
    (root / "docs" / "required-check-policy.json").write_text(json.dumps(_PINNED_POLICY), encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _pin_repo_required_check_policy(monkeypatch, _pinned_policy_root):
    from headless_pr_workflow import required_check_policy as policy_module

    real_load = policy_module.load_required_check_policy

    def load(*, repo_root=None, policy_path=policy_module.DEFAULT_POLICY_PATH):
        if repo_root is None and Path.cwd().resolve() == _REPO_ROOT:
            repo_root = _pinned_policy_root
        return real_load(repo_root=repo_root, policy_path=policy_path)

    monkeypatch.setattr(policy_module, "load_required_check_policy", load)
