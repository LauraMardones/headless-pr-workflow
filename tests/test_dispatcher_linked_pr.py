"""The one linked-PR rule (issue #556): ``facts.closes_issue``, ``facts.linked_pr`` and
``hpw dispatcher in-flight linked``. Entry-equivalence over the poll, resume, status and Codex
worker paths is in ``tests/test_dispatcher_parity_in_flight.py`` (``linked_pr_lookup``)."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher.cli import main
from headless_pr_workflow.dispatcher.facts import closes_issue, linked_pr

REPO_ROOT = Path(__file__).resolve().parent.parent
LINKED_PR_SHELL = ("scripts/lib/in-flight.sh", "scripts/dispatcher-invoke.sh", "scripts/dispatcher-poll.sh")


@pytest.mark.parametrize("path", LINKED_PR_SHELL)
def test_no_process_substitution_path_is_handed_to_jq(path):
    """A native ``jq.exe`` under Git Bash cannot open the ``/dev/fd`` path of ``<(...)``, so ``inflight_linked``
    and its callers pass JSON through stdin. Linux cannot show the failure, so the source is checked (#556 review)."""

    lines = (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
    offenders = [f"{path}:{n}: {line.strip()}" for n, line in enumerate(lines, 1)
                 if re.search(r"jq\b.*(--slurpfile|--rawfile|--argfile)\s+\S+\s+<\(", line)]  # fmt: skip
    assert not offenders, offenders


@pytest.mark.parametrize("body", ["Closes #500", "fixes #500", "Resolved #500", "CLOSED #500", "close #500",
                                  "Fix #500.", "resolves  #500"])  # fmt: skip
def test_every_github_closing_keyword_links(body):
    # PO decision of 2026-11-16 on #556, option A (1): GitHub's full keyword list (case closed-past-tense).
    assert closes_issue(body, 500)


@pytest.mark.parametrize("body", ["Closes #5000", "Refs #500", "encloses #500", "Closes 500", ""])
def test_other_text_does_not_link(body):
    assert not closes_issue(body, 500)


def test_the_first_of_two_linked_prs_counts():
    # PO decision of 2026-11-16 on #556, option A (2) (case two-linked-prs).
    prs = [{"number": 1, "body": "Closes #9"}, {"number": 900, "body": "Closes #500"}, {"number": 901, "body": "Fixes #500"}]
    assert linked_pr(prs, 500)["number"] == 900
    assert linked_pr(prs, 7) is None


def test_merged_takes_only_a_merged_pr():
    closed = [{"number": 800, "body": "Closes #500", "merged_at": None},
              {"number": 801, "body": "Closes #500", "merged_at": "2026-11-10T10:00:00Z"}]  # fmt: skip
    assert linked_pr(closed, 500, merged=True)["number"] == 801


def run(monkeypatch, capsys, stdin: str) -> tuple[int, dict]:
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8"))))
    rc = main(["in-flight", "linked", "--repo", "owner/repo", "--input", "-"])
    return rc, json.loads(capsys.readouterr().out)


def test_cli_maps_each_issue_to_its_linked_and_merged_pr(monkeypatch, capsys):
    data = {"issues": [500, 7], "open": [{"number": 900, "body": "Closes #500", "draft": True}],
            "closed": [{"number": 5, "body": "Resolves #7", "merged_at": "2026-11-10T10:00:00Z"}]}  # fmt: skip
    rc, out = run(monkeypatch, capsys, json.dumps(data))
    assert rc == 0 and out["result"] == "read"
    assert out["linked"] == {"500": data["open"][0], "7": None}
    assert out["merged"] == {"500": None, "7": 5}


@pytest.mark.parametrize("stdin", ["not json", json.dumps({"open": []}), json.dumps({"issues": [1], "open": {}})])
def test_cli_fails_closed_on_unreadable_input(monkeypatch, capsys, stdin):
    rc, out = run(monkeypatch, capsys, stdin)
    assert rc == 3 and out["result"] == "unreadable"
