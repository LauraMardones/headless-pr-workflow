"""The dispatcher workflow gives the facts readers the PO login (issue #546).

`hpw dispatcher scheduler next` and `hpw dispatcher status sync` build their
facts from `os.environ`; `facts.py` falls back to `DEFAULT_PO_LOGIN`. The other
readers' constants and the workflow envs are pinned to that default here. The workflow is read as text, the way
tests/test_dispatcher_workflow_checkout.py does.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest

from headless_pr_workflow.dispatcher.facts import _Facts
from headless_pr_workflow.dispatcher.gateway import Gateway
from headless_pr_workflow.dispatcher.write import DEFAULT_PO_LOGIN

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"


def job_env() -> dict[str, str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    block = re.search(r"^  dispatch:\n(?:    .*\n)*?    env:\n((?:      .*\n|\s*\n)+?)\n    steps:", text, re.MULTILINE)
    assert block, "job-level env: block not found in dispatcher.yml"
    env = {}
    for line in block.group(1).splitlines():
        m = re.match(r"^      ([A-Z_]+): (.+)$", line)
        if m:
            env[m.group(1)] = m.group(2).strip()
    return env


def comments() -> list[dict[str, Any]]:
    return [
        {
            "body": "## Blocked Declaration\nType: decision\nQuestion: which Epic?",
            "user": {"login": "LauraMardones", "type": "User"},
            "author_association": "OWNER",
            "created_at": "2026-11-13T10:02:50Z",
            "html_url": "https://example.invalid/declaration",
        },
        {
            "body": "Option a\n/unblock",
            "user": {"login": DEFAULT_PO_LOGIN, "type": "User"},
            "author_association": "OWNER",
            "created_at": "2026-11-13T21:02:21Z",
            "html_url": "https://example.invalid/unblock",
        },
    ]


def test_job_env_sets_the_po_login_used_by_the_other_readers():
    assert job_env().get("DECISION_BLOCKER_PO_LOGIN") == DEFAULT_PO_LOGIN


def test_po_unblock_lifts_the_decision_blocker_with_the_workflow_env():
    facts = _Facts(cast(Gateway, None), job_env())
    blocker = facts.decision_blocker(comments())
    assert blocker["state"] == "unblocked", blocker
    assert blocker["unblocked_at"] == "2026-11-13T21:02:21Z"


def test_without_the_po_login_the_default_counts():
    facts = _Facts(cast(Gateway, None), {})
    assert facts.po_login == DEFAULT_PO_LOGIN
    assert facts.decision_blocker(comments())["state"] == "unblocked"


@pytest.mark.parametrize(
    "path, pattern",
    [
        ("scripts/dispatcher-poll.sh", r'^PO_LOGIN="([^"]*)"'),
        ("scripts/dispatcher-invoke.sh", r'^DECISION_BLOCKER_PO_LOGIN="([^"]*)"'),
        ("scripts/dispatcher-notify-retry.sh", r'^DECISION_BLOCKER_PO_LOGIN="\$\{DECISION_BLOCKER_PO_LOGIN:-([^}]*)\}"'),
        ("src/headless_pr_workflow/codex_review_bookkeeping.py", r'^DECISION_BLOCKER_PO_LOGIN = "([^"]*)"'),
    ],
)
def test_other_readers_hold_the_default_po_login(path, pattern):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    found = re.findall(pattern, text, re.MULTILINE)
    assert found == [DEFAULT_PO_LOGIN], (path, found)


@pytest.mark.parametrize("name", ["dispatcher.yml", "board-status.yml"])
def test_every_hpw_dispatcher_job_env_sets_the_default_po_login(name):
    text = (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    jobs = re.split(r"^  (?=[A-Za-z0-9_-]+:\n)", text.split("\njobs:\n", 1)[1], flags=re.MULTILINE)
    checked = 0
    for job in jobs:
        if not re.search(r"hpw dispatcher|dispatcher-workflow-step\.sh status-sync|dispatcher-poll\.sh", job):
            continue
        checked += 1
        values = re.findall(r"^      DECISION_BLOCKER_PO_LOGIN: (.+)$", job, re.MULTILINE)
        assert values == [DEFAULT_PO_LOGIN], (name, job.splitlines()[0], values)
    assert checked, name
