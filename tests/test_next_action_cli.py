"""CLI coverage for hpw next-action."""

from __future__ import annotations

import json

from headless_pr_workflow import cli
from headless_pr_workflow.next_action import NextActionResult


def test_catalog_marks_next_action_implemented(capsys):
    exit_code = cli.main(["catalog"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "next-action\tP1-high\tC-session\tadvisory\tcore\timplemented" in output


def test_next_action_json_output(monkeypatch, capsys):
    result = NextActionResult(
        ok=True,
        repository="owner/repo",
        pr=123,
        action="review",
        rationale="workflow-status reported posture review_required: review needed",
        source_posture="review_required",
        blocking_reasons=("No current-head approval exists.",),
        source_commands=("workflow-status", "approval-check"),
        warnings=(),
    )
    calls = []

    def fake_summary(target, *, repo, path=None):
        calls.append((target, repo, path))
        return result

    monkeypatch.setattr(cli, "summarize_next_action_from_subprocess", fake_summary)

    exit_code = cli.main(["next-action", "123", "--repo", "owner/repo", "--path", "/repo", "--json"])

    assert exit_code == 0
    assert calls == [("123", "owner/repo", "/repo")]
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "next-action"
    assert output["action"] == "review"
    assert output["source_commands"] == ["workflow-status", "approval-check"]


def test_next_action_human_output(monkeypatch, capsys):
    result = NextActionResult(
        ok=True,
        repository="owner/repo",
        pr=123,
        action="wait",
        rationale="workflow-status reports pending checks.",
        source_posture="waiting",
        blocking_reasons=("Pending check: unit.",),
        source_commands=("workflow-status", "ci-summary"),
        warnings=("No required checks configured.",),
    )
    monkeypatch.setattr(cli, "summarize_next_action_from_subprocess", lambda target, repo, path=None: result)

    exit_code = cli.main(["next-action", "123", "--repo", "owner/repo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Next action: wait" in output
    assert "Rationale: workflow-status reports pending checks." in output
    assert "Blocking reasons:" in output
    assert "Source commands: workflow-status, ci-summary" in output
    assert "Warnings:" in output


def test_next_action_human_output_for_merged_pr(monkeypatch, capsys):
    result = NextActionResult(
        ok=True,
        repository="owner/repo",
        pr=123,
        action="post-merge-sync",
        rationale="PR is merged. Run post-merge-sync to update local state.",
        source_posture="human_decision_required",
        blocking_reasons=(),
        source_commands=("workflow-status",),
        warnings=(),
    )
    monkeypatch.setattr(cli, "summarize_next_action_from_subprocess", lambda target, repo, path=None: result)

    exit_code = cli.main(["next-action", "123", "--repo", "owner/repo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Next action: post-merge-sync" in output
    assert "Rationale: PR is merged. Run post-merge-sync to update local state." in output


def test_next_action_json_error_output(monkeypatch, capsys):
    result = NextActionResult(
        ok=False,
        repository="owner/repo",
        pr=123,
        action=None,
        rationale="Unable to fetch or parse workflow-status output.",
        source_posture=None,
        blocking_reasons=(),
        source_commands=("workflow-status",),
        warnings=(),
        errors={"type": "workflow-status-failed", "message": "Unable to fetch or parse workflow-status output."},
    )
    monkeypatch.setattr(cli, "summarize_next_action_from_subprocess", lambda target, repo, path=None: result)

    exit_code = cli.main(["next-action", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["errors"]["type"] == "workflow-status-failed"


def test_next_action_usage_error_missing_repo(capsys):
    exit_code = cli.main(["next-action", "123"])

    assert exit_code == 2
    assert "requires <pr> and --repo" in capsys.readouterr().err
