from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def pytest_configure(config: object) -> None:
    """Redirect pytest tmp_path base to a dir outside the repo.

    Avoids two problems in sandboxed environments (e.g. Codex):
    1. The system temp directory may not be writable.
    2. A temp dir inside the repo causes git-detection to succeed for tests
       that create subdirectories expecting a non-git environment.

    Placing the base one level above the repo root sidesteps both issues.
    PYTEST_DEBUG_TEMPROOT takes precedence when set explicitly.
    """
    if os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return
    opts = getattr(config, "option", None)
    if opts is not None and not getattr(opts, "basetemp", None):
        # One level above the repo root — outside git working tree.
        basetemp = Path(__file__).parent.parent / ".pytest_tmp"
        basetemp.mkdir(parents=True, exist_ok=True)
        opts.basetemp = basetemp


# Tests that summarize CI without an explicit repo_root read the policy relative to
# the current directory, i.e. this repository's live docs/required-check-policy.json.
# Pin those reads to the pre-#290 "no required checks" policy so that switching the
# live file to "required" (a PO step after #290) does not change unrelated tests.
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
