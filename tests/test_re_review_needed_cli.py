import json

from headless_pr_workflow import cli
from headless_pr_workflow.github import GHCommandError

from tests.github_scenarios import (
    build_pr_context,
    scenario_changes_requested,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_solo_override,
    scenario_stale_approval,
)


def test_catalog_marks_re_review_needed_implemented(capsys):
    exit_code = cli.main(["catalog"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "re-review-needed\tP1-high\tF-review\thard-gate\tcore\timplemented" in output


def test_re_review_needed_json_output_passes_for_formal_approval(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_current_approval(head_sha="head123"))

    exit_code = cli.main(["re-review-needed", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["head_ref_oid"] == "head123"
    assert output["latest_review_sha"] == "head123"
    assert output["latest_review_state"] == "APPROVED"
    assert output["latest_review_author"] == "reviewer"
    assert output["latest_approval_sha"] == "head123"
    assert output["approval_status"] == "current"
    assert output["solo_override"]["status"] == "missing"
    assert output["approval_source"] == "formal"
    assert output["satisfied_by"] == "formal-approval"
    assert output["re_review_needed"] is False
    assert output["hard_gate_passed"] is True
    assert output["blocking_reasons"] == []


def test_re_review_needed_json_output_passes_for_solo_override(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_solo_override(head_sha="head123"))

    exit_code = cli.main(["re-review-needed", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["approval_status"] == "missing"
    assert output["solo_override"]["status"] == "accepted"
    assert output["approval_source"] == "solo-maintainer-override"
    assert output["re_review_needed"] is False
    assert output["hard_gate_passed"] is True


def test_re_review_needed_json_output_fails_for_stale_review_evidence(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_stale_approval(head_sha="new-head", approval_sha="old-head"))

    exit_code = cli.main(["re-review-needed", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["head_ref_oid"] == "new-head"
    assert output["latest_approval_sha"] == "old-head"
    assert output["approval_status"] == "stale"
    assert output["re_review_needed"] is True
    assert output["hard_gate_passed"] is False
    assert "formal approval is stale for the current PR head SHA" in output["blocking_reasons"]


def test_re_review_needed_json_output_fails_for_missing_review_evidence(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head123"))

    exit_code = cli.main(["re-review-needed", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["re_review_needed"] is True
    assert output["hard_gate_passed"] is False
    assert "no formal approval or accepted solo-maintainer override exists for the current PR head SHA" in output["blocking_reasons"]


def test_re_review_needed_json_output_fails_for_changes_requested(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_changes_requested(head_sha="head123"))

    exit_code = cli.main(["re-review-needed", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["approval_status"] == "current"
    assert output["re_review_needed"] is True
    assert "GitHub review decision is CHANGES_REQUESTED for the current PR head." in output["blocking_reasons"]


def test_re_review_needed_human_output_names_result_and_blockers(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_comment_only_review(head_sha="head123"))

    exit_code = cli.main(["re-review-needed", "123", "--repo", "owner/repo"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "PR #123: Scenario PR" in output
    assert "url: https://github.com/owner/repo/pull/123" in output
    assert "head sha: head123" in output
    assert "latest review sha: head123" in output
    assert "latest review state: COMMENTED" in output
    assert "latest review author: reviewer" in output
    assert "latest approval sha: none" in output
    assert "approval status: missing" in output
    assert "solo-maintainer override: missing" in output
    assert "approval source: none" in output
    assert "re-review needed: true" in output
    assert "blocking reasons:" in output
    assert "- comment-only review exists without formal approval or an accepted solo-maintainer override" in output
    assert "hard gate passed: false" in output


def test_re_review_needed_json_error_output(monkeypatch, capsys):
    def fail(target, repo=None):
        raise GHCommandError(["gh", "pr", "view"], 1, "not found")

    monkeypatch.setattr(cli, "fetch_pr_context", fail)

    exit_code = cli.main(["re-review-needed", "999", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"error": "gh-command-failed"' in output
    assert '"stderr": "not found"' in output
