import json

from headless_pr_workflow import cli
from headless_pr_workflow.github import GHCommandError, RequiredStatusChecks

from tests.github_scenarios import (
    build_check,
    build_pr_context,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_draft_pr,
    scenario_empty_status_rollup,
    scenario_mergeable_unknown,
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
    assert "ci-summary\tP1-high\tE-review-readiness\treport\tcore\timplemented" in output
    assert "target-branch-check\tP0-blocking\tH-merge\thard-gate\tcore\timplemented" in output
    assert "merge-owner\tP1-high\tH-merge\thard-gate\tcore\timplemented" in output
    assert "unresolved-review-threads\tP1-high\tF-review\thard-gate\tcore\timplemented" in output
    assert "pre-merge\tP0-blocking\tH-merge\thard-gate\tcore\timplemented" in output
    assert "merge-pr\tP0-blocking\tH-merge\taction\tcore\timplemented" in output


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


def test_review_sha_json_output_passes_for_solo_override(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_solo_override(head_sha="head123"))

    exit_code = cli.main(["review-sha", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"approval_status": "missing"' in output
    assert '"hard_gate_passed": true' in output


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
    monkeypatch.setattr(cli, "fetch_required_status_check_context", lambda repo, branch: RequiredStatusChecks(names=("unit",), status="configured"))
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

    exit_code = cli.main(["pre-merge", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hard_gate_passed"] is True
    assert output["current_head_sha"] == "head123"
    assert output["pr"]["number"] == 123
    assert output["approval_review_source"]["approval_source"] == "formal"
    assert output["target_branch_comparison"]["result"] == "pass"
    assert output["required_check_summary"]["required_check_status"] == "satisfied"
    assert output["mergeability_facts"]["mergeable"] == "MERGEABLE"
    assert output["unresolved_thread_summary"]["thread_counts"]["unresolved_blocking"] == 0
    assert any(check["code"] == "required-checks-passing" for check in output["checks"])
    assert output["blocking_reasons"] == []


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
    monkeypatch.setattr(
        cli,
        "fetch_required_status_check_context",
        lambda repo, branch: RequiredStatusChecks(names=("unit", "lint"), status="configured"),
    )
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

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
    monkeypatch.setattr(cli, "fetch_required_status_check_context", lambda repo, branch: RequiredStatusChecks(names=("unit",), status="configured"))
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

    exit_code = cli.main(["pre-merge", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"GitHub reported no status checks for the current head SHA."' in output


def test_merge_pr_json_output_ready(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "fetch_pr_context",
        lambda target, repo=None: scenario_current_approval(
            head_sha="head123",
            status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
        ),
    )
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(cli, "fetch_required_status_check_context", lambda repo, branch: RequiredStatusChecks(names=("unit",), status="configured"))
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

    exit_code = cli.main(["merge-pr", "123", "--repo", "owner/repo", "--method", "squash", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "merge-pr"
    assert output["mode"] == "dry_run"
    assert output["dry_run"] is True
    assert output["would_merge"] is True
    assert output["selected_method"] == "squash"
    assert output["number"] == 123
    assert output["url"] == "https://github.com/owner/repo/pull/123"
    assert output["head_sha"] == "head123"
    assert output["base_branch"] == "main"
    assert output["approval_review_source"]["approval_source"] == "formal"
    assert output["readiness"]["hard_gate_passed"] is True
    assert output["blocking_reasons"] == []


def test_merge_pr_human_output_ready_names_dry_run_facts(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_solo_override(head_sha="head123"))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(cli, "fetch_required_status_check_context", lambda repo, branch: RequiredStatusChecks(names=(), status="not_configured"))
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

    exit_code = cli.main(["merge-pr", "123", "--repo", "owner/repo", "--method", "rebase"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "mode: dry-run (no GitHub merge mutation will be performed)" in output
    assert "selected method: rebase" in output
    assert "head sha: head123" in output
    assert "base branch: main" in output
    assert "approval source: solo-maintainer-override" in output
    assert "would merge: true" in output


def test_merge_pr_refuses_draft_pr(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "fetch_pr_context",
        lambda target, repo=None: with_context(
            scenario_draft_pr(head_ref_oid="head123"),
            latest_reviews=(scenario_current_approval(head_sha="head123").latest_reviews[0],),
        ),
    )
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(cli, "fetch_required_status_check_context", lambda repo, branch: RequiredStatusChecks(names=(), status="not_configured"))
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

    exit_code = cli.main(["merge-pr", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["would_merge"] is False
    assert "PR is draft." in output["blocking_reasons"]


def test_merge_pr_refuses_stale_approval_for_current_head(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_stale_approval(head_sha="new-head", approval_sha="old-head"))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(cli, "fetch_required_status_check_context", lambda repo, branch: RequiredStatusChecks(names=(), status="not_configured"))
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

    exit_code = cli.main(["merge-pr", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["head_sha"] == "new-head"
    assert "formal approval is stale for the current PR head SHA" in output["blocking_reasons"]


def test_merge_pr_refuses_pending_and_failing_checks(monkeypatch, capsys):
    blocked_context = scenario_current_approval(
        head_sha="head123",
        status_checks=(
            build_check(name="unit", bucket="failure", status="COMPLETED", conclusion="FAILURE"),
            build_check(name="lint", bucket="pending", state="PENDING"),
        ),
    )
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: blocked_context)
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(
        cli,
        "fetch_required_status_check_context",
        lambda repo, branch: RequiredStatusChecks(names=("unit", "lint"), status="configured"),
    )
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

    exit_code = cli.main(["merge-pr", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert "Status check unit is failing (status=COMPLETED, conclusion=FAILURE)." in output["blocking_reasons"]
    assert "Status check lint is pending (state=PENDING)." in output["blocking_reasons"]


def test_merge_pr_refuses_unacceptable_mergeability(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_mergeable_unknown(head_sha="head123"))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(cli, "fetch_required_status_check_context", lambda repo, branch: RequiredStatusChecks(names=(), status="not_configured"))
    monkeypatch.setattr(cli, "fetch_review_threads_for_context", lambda context, repo=None: ())

    exit_code = cli.main(["merge-pr", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert "PR mergeable state is UNKNOWN." in output["blocking_reasons"]


def test_target_branch_check_json_output_passes_for_matching_default_branch(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(base_ref_name="main"))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")

    exit_code = cli.main(["target-branch-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"number": 123' in output
    assert '"url": "https://github.com/owner/repo/pull/123"' in output
    assert '"base_ref_name": "main"' in output
    assert '"expected_base_ref_name": "main"' in output
    assert '"result": "pass"' in output
    assert '"hard_gate_passed": true' in output
    assert '"blocking_reasons": []' in output


def test_target_branch_check_json_output_fails_for_mismatched_branch(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(base_ref_name="release"))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")

    exit_code = cli.main(["target-branch-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"result": "fail"' in output
    assert '"PR targets base branch release, expected main."' in output


def test_target_branch_check_json_output_fails_for_unknown_pr_base_branch(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(base_ref_name=""))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")

    exit_code = cli.main(["target-branch-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"Target base branch is unknown."' in output
    assert '"hard_gate_passed": false' in output


def test_target_branch_check_json_output_fails_for_unknown_expected_branch(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(base_ref_name="main"))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "")

    exit_code = cli.main(["target-branch-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"expected_base_ref_name": ""' in output
    assert '"Expected target base branch is unknown."' in output


def test_target_branch_check_json_output_uses_explicit_expected_branch(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(base_ref_name="release"))

    def fail(repo=None):
        raise AssertionError("default branch should not be fetched when --expected-base is provided")

    monkeypatch.setattr(cli, "fetch_repo_default_branch", fail)

    exit_code = cli.main(["target-branch-check", "123", "--repo", "owner/repo", "--expected-base", "release", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"expected_base_ref_name": "release"' in output
    assert '"result": "pass"' in output


def test_target_branch_check_human_output_states_fail_and_compared_branches(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(base_ref_name="release"))
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")

    exit_code = cli.main(["target-branch-check", "123", "--repo", "owner/repo"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "actual base: release" in output
    assert "expected base: main" in output
    assert "target branch check: fail" in output
    assert "hard gate passed: false" in output


def test_target_branch_check_json_error_output(monkeypatch, capsys):
    def fail(target, repo=None):
        raise GHCommandError(["gh", "pr", "view"], 1, "not found")

    monkeypatch.setattr(cli, "fetch_pr_context", fail)

    exit_code = cli.main(["target-branch-check", "999", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"error": "gh-command-failed"' in output
    assert '"stderr": "not found"' in output


def test_ci_summary_json_output_reports_required_check_state(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "fetch_pr_context",
        lambda target, repo=None: scenario_current_approval(
            head_sha="head123",
            status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
        ),
    )
    monkeypatch.setattr(
        cli,
        "fetch_required_status_check_context",
        lambda repo, branch: RequiredStatusChecks(names=("unit", "lint"), status="configured"),
    )

    exit_code = cli.main(["ci-summary", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"required_check_status": "missing"' in output
    assert '"missing": [' in output
    assert '"lint"' in output
    assert '"passing": [' in output
    assert '"unit"' in output


def test_ci_summary_human_output_is_explicit_for_absent_required_checks(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_empty_status_rollup(head_sha="head123"))
    monkeypatch.setattr(
        cli,
        "fetch_required_status_check_context",
        lambda repo, branch: RequiredStatusChecks(names=(), status="not_configured"),
    )

    exit_code = cli.main(["ci-summary", "123", "--repo", "owner/repo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status rollup: empty" in output
    assert "required checks: not_configured" in output
    assert "No required status checks are configured for the target branch." in output
