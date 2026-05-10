"""Tests for hpw pr-takeover command."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from headless_pr_workflow.approval_check import summarize_approval_check
from headless_pr_workflow.ci_summary import summarize_ci
from headless_pr_workflow.github import GHCommandError, RequiredStatusChecks
from headless_pr_workflow.github.review_threads import summarize_review_threads
from headless_pr_workflow.pre_merge import summarize_pre_merge
from headless_pr_workflow.pr_takeover import summarize_pr_takeover, TakeoverNextAction

from tests.github_scenarios import (
    build_check,
    build_pr_context,
    scenario_changes_requested,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_draft_pr,
    scenario_failing_checks,
    scenario_mergeable_unknown,
    scenario_pending_checks,
    scenario_solo_override,
    scenario_stale_approval,
    with_context,
)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def make_required_checks(names: tuple[str, ...] = ()) -> RequiredStatusChecks:
    return RequiredStatusChecks(
        names=names,
        status="configured" if names else "not_configured",
    )


def make_ci(context, *, required_check_names: tuple[str, ...] = ()):
    return summarize_ci(context, required_checks=make_required_checks(required_check_names))


def make_threads(context, *, raw_threads=None):
    return summarize_review_threads(context, raw_threads or ())


def make_pre_merge(context, *, expected_base: str = "main", required_check_names: tuple[str, ...] = ()):
    return summarize_pre_merge(
        context,
        expected_base_ref_name=expected_base,
        required_checks=make_required_checks(required_check_names),
        review_threads=make_threads(context),
    )


def make_takeover(
    context,
    *,
    repository: str | None = "owner/repo",
    required_check_names: tuple[str, ...] = (),
    raw_threads=None,
):
    approval = summarize_approval_check(context)
    ci = make_ci(context, required_check_names=required_check_names)
    review_threads = make_threads(context, raw_threads=raw_threads)
    merge_readiness = summarize_pre_merge(
        context,
        expected_base_ref_name="main",
        required_checks=make_required_checks(required_check_names),
        review_threads=review_threads,
    )
    return summarize_pr_takeover(
        context,
        repository=repository,
        approval=approval,
        ci=ci,
        review_threads=review_threads,
        merge_readiness=merge_readiness,
    )


# ---------------------------------------------------------------------------
# Next action: implementation
# ---------------------------------------------------------------------------

def test_next_action_implementation_draft_pr():
    summary = make_takeover(scenario_draft_pr(head_ref_oid="head123"))
    assert summary.next_action.action_class == "implementation"
    reasons = summary.next_action.reasons
    assert any("draft" in r.lower() for r in reasons)


def test_next_action_implementation_changes_requested():
    context = scenario_changes_requested(head_sha="head123")
    summary = make_takeover(context)
    assert summary.next_action.action_class == "implementation"
    reasons = summary.next_action.reasons
    assert any("CHANGES_REQUESTED" in r for r in reasons)


def test_next_action_implementation_unresolved_threads():
    raw_thread = {
        "id": "thread-1",
        "path": "src/app.py",
        "line": 5,
        "startLine": None,
        "isResolved": False,
        "isOutdated": False,
        "comments": {"totalCount": 1, "nodes": []},
    }
    context = scenario_current_approval(head_sha="head123")
    summary = make_takeover(context, raw_threads=(raw_thread,))
    assert summary.next_action.action_class == "implementation"
    reasons = summary.next_action.reasons
    assert any("unresolved" in r.lower() for r in reasons)


def test_next_action_implementation_failing_checks():
    context = with_context(
        scenario_current_approval(head_sha="head123"),
        status_checks=(
            build_check(name="unit", bucket="failure", status="COMPLETED", conclusion="FAILURE"),
        ),
    )
    summary = make_takeover(context, required_check_names=("unit",))
    assert summary.next_action.action_class == "implementation"
    reasons = summary.next_action.reasons
    assert any("unit" in r for r in reasons)


# ---------------------------------------------------------------------------
# Next action: review
# ---------------------------------------------------------------------------

def test_next_action_review_stale_approval():
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    summary = make_takeover(context)
    assert summary.next_action.action_class == "review"
    reasons = summary.next_action.reasons
    assert any("stale" in r.lower() for r in reasons)


def test_next_action_review_missing_approval():
    context = build_pr_context(head_ref_oid="head123", latest_reviews=())
    summary = make_takeover(context)
    assert summary.next_action.action_class == "review"


def test_next_action_review_comment_only():
    context = scenario_comment_only_review(head_sha="head123")
    summary = make_takeover(context)
    assert summary.next_action.action_class == "review"


def test_next_action_review_stale_solo_override():
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    summary = make_takeover(context)
    assert summary.next_action.action_class == "review"
    assert summary.approval.approval_status == "stale"


# ---------------------------------------------------------------------------
# Next action: merge
# ---------------------------------------------------------------------------

def test_next_action_merge_all_gates_pass():
    context = scenario_current_approval(
        head_sha="head123",
        status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
    )
    summary = make_takeover(context, required_check_names=("unit",))
    assert summary.next_action.action_class == "merge"
    assert summary.merge_readiness.hard_gate_passed is True


def test_next_action_merge_no_required_checks():
    context = scenario_current_approval(head_sha="head123", status_checks=())
    summary = make_takeover(context, required_check_names=())
    assert summary.next_action.action_class == "merge"


def test_next_action_merge_current_solo_override():
    context = scenario_solo_override(head_sha="head123")
    summary = make_takeover(context)
    assert summary.next_action.action_class == "merge"
    assert summary.approval.approval_source == "solo-maintainer-override"


# ---------------------------------------------------------------------------
# Next action: human_decision
# ---------------------------------------------------------------------------

def test_next_action_human_decision_pending_checks():
    context = with_context(
        scenario_current_approval(head_sha="head123"),
        status_checks=(
            build_check(name="lint", bucket="pending", state="PENDING"),
        ),
    )
    summary = make_takeover(context, required_check_names=("lint",))
    assert summary.next_action.action_class == "human_decision"
    reasons = summary.next_action.reasons
    assert any("pending" in r.lower() for r in reasons)


def test_next_action_human_decision_unknown_checks():
    context = with_context(
        scenario_current_approval(head_sha="head123"),
        status_checks=(
            build_check(name="security", bucket="unknown"),
        ),
    )
    summary = make_takeover(context, required_check_names=("security",))
    assert summary.next_action.action_class == "human_decision"


def test_next_action_human_decision_unknown_mergeability():
    context = scenario_mergeable_unknown(head_sha="head123")
    summary = make_takeover(context)
    assert summary.next_action.action_class == "human_decision"
    reasons = summary.next_action.reasons
    assert any("UNKNOWN" in r or "unknown" in r.lower() for r in reasons)


def test_next_action_human_decision_dirty_merge_state():
    context = scenario_current_approval(head_sha="head123", merge_state_status="DIRTY")
    summary = make_takeover(context)
    assert summary.next_action.action_class == "human_decision"


# ---------------------------------------------------------------------------
# Draft PR safety guarantees
# ---------------------------------------------------------------------------

def test_draft_pr_not_recommended_as_merge():
    context = scenario_draft_pr(head_ref_oid="head123")
    summary = make_takeover(context)
    assert summary.next_action.action_class != "merge"


def test_draft_pr_surfaced_explicitly():
    context = scenario_draft_pr(head_ref_oid="head123")
    summary = make_takeover(context)
    output = summary.to_dict()
    assert output["pr"]["is_draft"] is True
    reasons = output["next_action"]["reasons"]
    assert any("draft" in r.lower() for r in reasons)


# ---------------------------------------------------------------------------
# Stale approval guarantees
# ---------------------------------------------------------------------------

def test_stale_approval_not_treated_as_merge_ready():
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    summary = make_takeover(context)
    assert summary.next_action.action_class != "merge"
    assert summary.merge_readiness.hard_gate_passed is False


def test_stale_solo_override_surfaced_explicitly():
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    summary = make_takeover(context)
    output = summary.to_dict()
    assert output["approval"]["approval_status"] == "stale"
    assert output["re_review"]["re_review_needed"] is True


# ---------------------------------------------------------------------------
# JSON output contract
# ---------------------------------------------------------------------------

def test_json_output_top_level_keys():
    context = scenario_current_approval(head_sha="head123")
    summary = make_takeover(context)
    output = summary.to_dict()
    for key in ("command", "ok", "repository", "pr", "approval", "re_review", "checks",
                "review_threads", "merge_readiness", "next_action", "warnings"):
        assert key in output, f"Missing key: {key}"


def test_json_output_command_field():
    context = scenario_current_approval(head_sha="head123")
    output = make_takeover(context).to_dict()
    assert output["command"] == "pr-takeover"


def test_json_output_ok_true_on_success():
    context = scenario_current_approval(head_sha="head123")
    output = make_takeover(context).to_dict()
    assert output["ok"] is True


def test_json_output_pr_fields():
    context = build_pr_context(
        number=42,
        title="Test PR",
        url="https://github.com/owner/repo/pull/42",
        state="OPEN",
        is_draft=False,
        base_ref_name="main",
        head_ref_name="feature/foo",
        head_ref_oid="sha42",
        labels=("bug",),
        review_requests=("reviewer1",),
        latest_reviews=(),
    )
    output = make_takeover(context, repository="owner/repo").to_dict()
    pr = output["pr"]
    assert pr["number"] == 42
    assert pr["title"] == "Test PR"
    assert pr["url"] == "https://github.com/owner/repo/pull/42"
    assert pr["state"] == "OPEN"
    assert pr["is_draft"] is False
    assert pr["base_ref_name"] == "main"
    assert pr["head_ref_name"] == "feature/foo"
    assert pr["head_ref_oid"] == "sha42"
    assert pr["labels"] == ["bug"]
    assert pr["review_requests"] == ["reviewer1"]


def test_json_output_approval_fields():
    context = scenario_current_approval(head_sha="head123")
    output = make_takeover(context).to_dict()
    approval = output["approval"]
    for key in ("approval_status", "latest_review_sha", "latest_approval_sha",
                "solo_override", "approval_source", "satisfied_by",
                "blocking_reasons", "hard_gate_passed"):
        assert key in approval, f"Missing approval key: {key}"
    assert approval["approval_status"] == "current"
    assert approval["hard_gate_passed"] is True


def test_json_output_re_review_fields():
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    output = make_takeover(context).to_dict()
    rr = output["re_review"]
    assert "re_review_needed" in rr
    assert "hard_gate_passed" in rr
    assert "blocking_reasons" in rr
    assert rr["re_review_needed"] is True
    assert rr["hard_gate_passed"] is False


def test_json_output_checks_fields():
    context = scenario_current_approval(head_sha="head123")
    output = make_takeover(context).to_dict()
    checks = output["checks"]
    for key in ("status_rollup", "required_check_status", "check_buckets", "messages"):
        assert key in checks, f"Missing checks key: {key}"


def test_json_output_review_threads_fields():
    context = scenario_current_approval(head_sha="head123")
    output = make_takeover(context).to_dict()
    threads = output["review_threads"]
    assert "thread_counts" in threads
    assert "hard_gate_passed" in threads


def test_json_output_merge_readiness_fields():
    context = scenario_current_approval(head_sha="head123")
    output = make_takeover(context).to_dict()
    mr = output["merge_readiness"]
    assert "hard_gate_passed" in mr
    assert "blocking_reasons" in mr
    assert "checks" in mr


def test_json_output_next_action_fields():
    context = scenario_current_approval(head_sha="head123")
    output = make_takeover(context).to_dict()
    na = output["next_action"]
    for key in ("class", "summary", "reasons", "follow_up_commands"):
        assert key in na, f"Missing next_action key: {key}"
    assert na["class"] in ("implementation", "review", "merge", "human_decision")


def test_json_output_repository():
    context = scenario_current_approval(head_sha="head123")
    output = make_takeover(context, repository="myorg/myrepo").to_dict()
    assert output["repository"] == "myorg/myrepo"


# ---------------------------------------------------------------------------
# Exit code: success even when next action is not merge
# ---------------------------------------------------------------------------

def test_ok_true_when_next_action_is_review():
    context = scenario_stale_approval(head_sha="new", approval_sha="old")
    summary = make_takeover(context)
    assert summary.ok is True
    assert summary.next_action.action_class == "review"


def test_ok_true_when_next_action_is_implementation():
    context = scenario_draft_pr(head_ref_oid="head123")
    summary = make_takeover(context)
    assert summary.ok is True
    assert summary.next_action.action_class == "implementation"


def test_ok_true_when_next_action_is_human_decision():
    context = scenario_mergeable_unknown(head_sha="head123")
    summary = make_takeover(context)
    assert summary.ok is True
    assert summary.next_action.action_class == "human_decision"


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def test_warning_for_empty_status_rollup():
    context = scenario_current_approval(head_sha="head123", status_checks=())
    summary = make_takeover(context, required_check_names=())
    assert any("No status checks" in w for w in summary.warnings)


def test_warning_for_unknown_mergeability():
    context = scenario_mergeable_unknown(head_sha="head123")
    summary = make_takeover(context)
    assert any("UNKNOWN" in w for w in summary.warnings)


def test_no_spurious_warnings_when_all_ok():
    context = scenario_current_approval(
        head_sha="head123",
        status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
    )
    summary = make_takeover(context, required_check_names=("unit",))
    # Should not have errors; may have "no required checks" warning but not serious ones
    assert summary.ok is True


# ---------------------------------------------------------------------------
# CLI output tests (via main())
# ---------------------------------------------------------------------------

from headless_pr_workflow.cli import main


def _make_pr_context_for_cli(head_sha="head123"):
    return scenario_current_approval(
        head_sha=head_sha,
        status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
    )


def _mock_fetch_side_effects(context, repo_default="main"):
    """Return a side_effect list suitable for patching fetch functions."""
    return context


def test_cli_pr_takeover_json_output(capsys):
    context = scenario_current_approval(
        head_sha="head123",
        status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
    )
    required_checks = RequiredStatusChecks(names=("unit",), status="configured")

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
    ):
        rc = main(["pr-takeover", "123", "--repo", "owner/repo", "--json"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "pr-takeover"
    assert output["ok"] is True
    assert output["next_action"]["class"] == "merge"


def test_cli_pr_takeover_human_output(capsys):
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
    ):
        rc = main(["pr-takeover", "123", "--repo", "owner/repo"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #123" in out
    assert "next action: review" in out


def test_cli_pr_takeover_github_fetch_failure_json(capsys):
    error = GHCommandError(
        command=["gh", "pr", "view"],
        returncode=1,
        stderr="not found",
        error="not-found",
    )
    with patch("headless_pr_workflow.cli.fetch_pr_context", side_effect=error):
        rc = main(["pr-takeover", "999", "--repo", "owner/repo", "--json"])

    assert rc == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "error" in output


def test_cli_pr_takeover_github_fetch_failure_human(capsys):
    error = GHCommandError(
        command=["gh", "pr", "view"],
        returncode=1,
        stderr="not found",
        error="not-found",
    )
    with patch("headless_pr_workflow.cli.fetch_pr_context", side_effect=error):
        rc = main(["pr-takeover", "999", "--repo", "owner/repo"])

    assert rc == 1


def test_cli_pr_takeover_human_decision_pending(capsys):
    context = with_context(
        scenario_current_approval(head_sha="head123"),
        status_checks=(build_check(name="lint", bucket="pending", state="PENDING"),),
    )
    required_checks = RequiredStatusChecks(names=("lint",), status="configured")

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
    ):
        rc = main(["pr-takeover", "123", "--repo", "owner/repo", "--json"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["next_action"]["class"] == "human_decision"


def test_cli_pr_takeover_implementation_changes_requested(capsys):
    context = scenario_changes_requested(head_sha="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
    ):
        rc = main(["pr-takeover", "123", "--repo", "owner/repo", "--json"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["next_action"]["class"] == "implementation"
