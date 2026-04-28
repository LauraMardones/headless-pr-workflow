from headless_pr_workflow import cli
from headless_pr_workflow.github import GHCommandError

from tests.github_scenarios import (
    build_check,
    build_pr_context,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_empty_status_rollup,
    scenario_solo_override,
    scenario_stale_approval,
    with_context,
)


def test_pr_context_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head123"))

    exit_code = cli.main(["pr-context", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"number": 123' in output
    assert '"head_ref_oid": "head123"' in output


def test_pr_context_json_error_output(monkeypatch, capsys):
    def fail(target, repo=None):
        raise GHCommandError(["gh", "pr", "view"], None, "GitHub CLI executable not found: gh", error="gh-not-found")

    monkeypatch.setattr(cli, "fetch_pr_context", fail)

    exit_code = cli.main(["pr-context", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"error": "gh-not-found"' in output
    assert '"returncode": null' in output


def test_catalog_marks_pr_context_implemented(capsys):
    exit_code = cli.main(["catalog"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "pr-context\tP1-high\tC-session\treport\tcore\timplemented" in output
    assert "approval-check\tP0-blocking\tF-review\thard-gate\tcore\timplemented" in output
    assert "review-sha\tP0-blocking\tF-review\thard-gate\tcore\timplemented" in output
    assert "pre-merge\tP0-blocking\tH-merge\thard-gate\tcore\timplemented" in output


def test_review_sha_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_current_approval(head_sha="head123"))

    exit_code = cli.main(["review-sha", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"head_ref_oid": "head123"' in output
    assert '"latest_approval_sha": "head123"' in output
    assert '"approval_status": "current"' in output
    assert '"hard_gate_passed": true' in output


def test_review_sha_json_output_fails_for_missing_approval(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_comment_only_review(head_sha="head123"))

    exit_code = cli.main(["review-sha", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"approval_status": "missing"' in output
    assert '"hard_gate_passed": false' in output


def test_review_sha_json_output_fails_for_stale_approval(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_stale_approval(head_sha="head123", approval_sha="old-head"))

    exit_code = cli.main(["review-sha", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"approval_status": "stale"' in output
    assert '"hard_gate_passed": false' in output


def test_review_sha_json_error_output(monkeypatch, capsys):
    def fail(target, repo=None):
        raise GHCommandError(["gh", "pr", "view"], 1, "not found")

    monkeypatch.setattr(cli, "fetch_pr_context", fail)

    exit_code = cli.main(["review-sha", "999", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"error": "gh-command-failed"' in output
    assert '"stderr": "not found"' in output


def test_approval_check_json_output_passes_for_formal_approval(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_current_approval(head_sha="head123"))

    exit_code = cli.main(["approval-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"approval_status": "current"' in output
    assert '"approval_source": "formal"' in output
    assert '"satisfied_by": "formal-approval"' in output
    assert '"hard_gate_passed": true' in output


def test_approval_check_json_output_passes_for_solo_override(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_solo_override(head_sha="head123"))

    exit_code = cli.main(["approval-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"approval_status": "missing"' in output
    assert '"status": "accepted"' in output
    assert '"approval_source": "solo-maintainer-override"' in output
    assert '"satisfied_by": "solo-maintainer-override"' in output
    assert '"hard_gate_passed": true' in output


def test_approval_check_json_output_fails_without_approval_or_override(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_comment_only_review(head_sha="head123"))

    exit_code = cli.main(["approval-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"status": "missing"' in output
    assert '"hard_gate_passed": false' in output
    assert '"blocking_reason": "comment-only review exists without formal approval or an accepted solo-maintainer override"' in output


def test_approval_check_json_output_fails_for_stale_approval(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_stale_approval(head_sha="head123", approval_sha="old-head"))

    exit_code = cli.main(["approval-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"approval_status": "stale"' in output
    assert '"hard_gate_passed": false' in output
    assert '"blocking_reason": "formal approval is stale for the current PR head SHA"' in output


def test_pre_merge_json_output_ready(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "fetch_pr_context",
        lambda target, repo=None: scenario_current_approval(
            head_sha="head123",
            status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS", url="https://checks/unit"),),
        ),
    )
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(cli, "fetch_required_status_checks", lambda repo, branch: ("unit",))

    exit_code = cli.main(["pre-merge", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"hard_gate_passed": true' in output
    assert '"code": "required-checks-passing"' in output
    assert '"blocking_reasons": []' in output


def test_pre_merge_json_output_lists_all_blockers(monkeypatch, capsys):
    blocked_context = with_context(
        scenario_stale_approval(head_sha="head123", approval_sha="old-head"),
        is_draft=True,
        mergeable="CONFLICTING",
        merge_state_status="DIRTY",
        status_checks=(
            build_check(name="unit", bucket="failure", status="COMPLETED", conclusion="FAILURE", url="https://checks/unit"),
            build_check(name="lint", bucket="pending", state="PENDING", url="https://checks/lint"),
        ),
    )
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: blocked_context)
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(cli, "fetch_required_status_checks", lambda repo, branch: ("unit", "lint"))

    exit_code = cli.main(["pre-merge", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"hard_gate_passed": false' in output
    assert '"PR is draft."' in output
    assert '"formal approval is stale for the current PR head SHA"' in output
    assert '"Status check unit is failing (status=COMPLETED, conclusion=FAILURE)."' in output
    assert '"Status check lint is pending (state=PENDING)."' in output
    assert '"PR mergeable state is CONFLICTING."' in output
    assert '"PR merge state status is DIRTY."' in output


def test_pre_merge_json_output_blocks_empty_status_checks_when_required(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_empty_status_rollup(head_sha="head123"))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(cli, "fetch_required_status_checks", lambda repo, branch: ("unit",))

    exit_code = cli.main(["pre-merge", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"GitHub reported no status checks for the current head SHA."' in output
