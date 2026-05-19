"""Tests for hpw workflow-status command."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from headless_pr_workflow.approval_check import summarize_approval_check
from headless_pr_workflow.ci_summary import summarize_ci
from headless_pr_workflow.github import GHCommandError, RequiredStatusChecks
from headless_pr_workflow.github.review_threads import summarize_review_threads
from headless_pr_workflow.pre_merge import summarize_pre_merge
from headless_pr_workflow.re_review_needed import summarize_re_review_needed
from headless_pr_workflow.workflow_status import (
    WorkflowPosture,
    WorkflowStatusSummary,
    summarize_workflow_status,
)
from headless_pr_workflow.worktree_status import (
    BranchStatus,
    FileStatus,
    WorktreeStatusSummary,
)

from tests.github_scenarios import (
    build_check,
    build_pr_context,
    scenario_changes_requested,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_draft_pr,
    scenario_failing_checks,
    scenario_merged_pr,
    scenario_mergeable_unknown,
    scenario_pending_checks,
    scenario_solo_override,
    scenario_stale_approval,
    with_context,
)


# ---------------------------------------------------------------------------
# Local worktree builders
# ---------------------------------------------------------------------------

def make_clean_local_state(*, path: str = "/repo") -> WorktreeStatusSummary:
    return WorktreeStatusSummary(
        command="worktree-status",
        ok=True,
        path=path,
        repository_root=path,
        worktree_path=path,
        head_sha="local-sha",
        branch=BranchStatus(
            name="feature/test",
            detached=False,
            upstream="origin/feature/test",
            upstream_sha="remote-sha",
            ahead=0,
            behind=0,
            tracking_status="up_to_date",
        ),
        status=FileStatus(clean=True, staged=(), unstaged=(), untracked=(), conflicted=()),
        unpushed_commits=(),
        linked_worktrees=(),
        branch_in_use_by_other_worktree=False,
        warnings=(),
        error=None,
    )


def make_dirty_local_state(
    *,
    staged: tuple[str, ...] = ("src/foo.py",),
    unstaged: tuple[str, ...] = ("src/bar.py",),
    untracked: tuple[str, ...] = ("tmp.txt",),
    conflicted: tuple[str, ...] = (),
) -> WorktreeStatusSummary:
    clean = not (staged or unstaged or conflicted)
    return WorktreeStatusSummary(
        command="worktree-status",
        ok=True,
        path="/repo",
        repository_root="/repo",
        worktree_path="/repo",
        head_sha="local-sha",
        branch=BranchStatus(
            name="feature/test",
            detached=False,
            upstream="origin/feature/test",
            upstream_sha="remote-sha",
            ahead=0,
            behind=0,
            tracking_status="up_to_date",
        ),
        status=FileStatus(
            clean=clean,
            staged=staged,
            unstaged=unstaged,
            untracked=untracked,
            conflicted=conflicted,
        ),
        unpushed_commits=(),
        linked_worktrees=(),
        branch_in_use_by_other_worktree=False,
        warnings=(),
        error=None,
    )


def make_failed_local_state() -> WorktreeStatusSummary:
    return WorktreeStatusSummary(
        command="worktree-status",
        ok=False,
        path="/not-a-repo",
        repository_root=None,
        worktree_path=None,
        head_sha=None,
        branch=BranchStatus(
            name=None,
            detached=False,
            upstream=None,
            upstream_sha=None,
            ahead=None,
            behind=None,
            tracking_status="no_upstream",
        ),
        status=FileStatus(clean=True, staged=(), unstaged=(), untracked=(), conflicted=()),
        unpushed_commits=(),
        linked_worktrees=(),
        branch_in_use_by_other_worktree=None,
        warnings=(),
        error={"message": "not inside a Git repository"},
    )


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def make_required_checks(names: tuple[str, ...] = ()) -> RequiredStatusChecks:
    return RequiredStatusChecks(
        names=names,
        status="configured" if names else "not_configured",
    )


def make_workflow_status(
    context,
    *,
    repository: str | None = "owner/repo",
    required_check_names: tuple[str, ...] = (),
    raw_threads: tuple = (),
    local_state: WorktreeStatusSummary | None = None,
) -> WorkflowStatusSummary:
    required_checks = make_required_checks(required_check_names)
    approval = summarize_approval_check(context)
    re_review = summarize_re_review_needed(context)
    ci = summarize_ci(context, required_checks=required_checks)
    review_threads = summarize_review_threads(context, raw_threads)
    merge_readiness = summarize_pre_merge(
        context,
        expected_base_ref_name="main",
        required_checks=required_checks,
        review_threads=review_threads,
    )
    return summarize_workflow_status(
        context,
        repository=repository,
        approval=approval,
        re_review=re_review,
        ci=ci,
        review_threads=review_threads,
        merge_readiness=merge_readiness,
        local_state=local_state or make_clean_local_state(),
    )


# ---------------------------------------------------------------------------
# Workflow posture: implementation_required
# ---------------------------------------------------------------------------

def test_posture_implementation_required_draft_pr():
    context = scenario_draft_pr(head_ref_oid="head123")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "implementation_required"
    assert any("draft" in r.lower() for r in summary.workflow_posture.reasons)


def test_posture_implementation_required_changes_requested():
    context = scenario_changes_requested(head_sha="head123")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "implementation_required"
    assert any("CHANGES_REQUESTED" in r for r in summary.workflow_posture.reasons)


def test_posture_implementation_required_unresolved_threads():
    raw_thread = {
        "id": "thread-1",
        "path": "src/app.py",
        "line": 5,
        "startLine": None,
        "isResolved": False,
        "isOutdated": False,
        "comments": {"totalCount": 0, "nodes": []},
    }
    context = scenario_current_approval(head_sha="head123")
    summary = make_workflow_status(context, raw_threads=(raw_thread,))
    assert summary.workflow_posture.status == "implementation_required"
    assert any("unresolved" in r.lower() for r in summary.workflow_posture.reasons)


def test_posture_implementation_required_failing_checks():
    context = with_context(
        scenario_current_approval(head_sha="head123"),
        status_checks=(build_check(name="unit", bucket="failure", status="COMPLETED", conclusion="FAILURE"),),
    )
    summary = make_workflow_status(context, required_check_names=("unit",))
    assert summary.workflow_posture.status == "implementation_required"
    assert any("unit" in r for r in summary.workflow_posture.reasons)


# ---------------------------------------------------------------------------
# Workflow posture: review_required
# ---------------------------------------------------------------------------

def test_posture_review_required_stale_approval():
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "review_required"
    assert any("stale" in r.lower() for r in summary.workflow_posture.reasons)


def test_posture_review_required_missing_approval():
    context = build_pr_context(head_ref_oid="head123", latest_reviews=())
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "review_required"


def test_posture_review_required_comment_only():
    context = scenario_comment_only_review(head_sha="head123")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "review_required"


# ---------------------------------------------------------------------------
# Workflow posture: waiting
# ---------------------------------------------------------------------------

def test_posture_waiting_pending_required_checks():
    context = with_context(
        scenario_current_approval(head_sha="head123"),
        status_checks=(build_check(name="lint", bucket="pending", state="PENDING"),),
    )
    summary = make_workflow_status(context, required_check_names=("lint",))
    assert summary.workflow_posture.status == "waiting"
    assert any("lint" in r for r in summary.workflow_posture.reasons)


# ---------------------------------------------------------------------------
# Workflow posture: merge_validation_required
# ---------------------------------------------------------------------------

def test_posture_merge_validation_all_gates_pass():
    context = scenario_current_approval(
        head_sha="head123",
        status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
    )
    summary = make_workflow_status(context, required_check_names=("unit",))
    assert summary.workflow_posture.status == "merge_validation_required"
    assert summary.merge_readiness.hard_gate_passed is True


def test_posture_merge_validation_no_required_checks():
    context = scenario_current_approval(head_sha="head123", status_checks=())
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "merge_validation_required"


def test_posture_merge_validation_solo_override():
    context = scenario_solo_override(head_sha="head123")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "merge_validation_required"
    assert summary.approval.approval_source == "solo-maintainer-override"


# Workflow posture: merged
# ---------------------------------------------------------------------------

def test_posture_merged_terminal_state_suppresses_merge_blockers():
    context = scenario_merged_pr(head_sha="head123")
    summary = make_workflow_status(context)

    assert summary.workflow_posture.status == "merged"
    assert summary.workflow_posture.summary == (
        "PR is merged. No further merge action required. Run post-merge-sync to update local state."
    )
    assert summary.workflow_posture.reasons == ()
    assert "pre-merge" not in summary.workflow_posture.source_commands
    assert summary.merge_readiness.blocking_reasons == ()
    assert not any("mergeability" in warning.lower() for warning in summary.warnings)


# ---------------------------------------------------------------------------
# Effective approval status for solo-maintainer override (issue #79)
# ---------------------------------------------------------------------------

def test_solo_override_json_effective_status_satisfied():
    context = scenario_solo_override(head_sha="head123")
    output = make_workflow_status(context).to_dict()
    approval = output["approval"]
    assert approval["effective_status"] == "satisfied"
    assert approval["effective_source"] == "solo-maintainer-override"


def test_solo_override_json_preserves_formal_approval_status():
    context = scenario_solo_override(head_sha="head123")
    output = make_workflow_status(context).to_dict()
    approval = output["approval"]
    assert approval["approval_status"] == "missing"


def test_solo_override_json_preserves_existing_fields():
    context = scenario_solo_override(head_sha="head123")
    output = make_workflow_status(context).to_dict()
    approval = output["approval"]
    assert approval["solo_override"]["status"] == "accepted"
    assert approval["approval_source"] == "solo-maintainer-override"
    assert approval["satisfied_by"] == "solo-maintainer-override"
    assert approval["hard_gate_passed"] is True


def test_formal_approval_json_effective_status_satisfied():
    context = scenario_current_approval(head_sha="head123")
    output = make_workflow_status(context).to_dict()
    approval = output["approval"]
    assert approval["effective_status"] == "satisfied"
    assert approval["effective_source"] == "formal"


def test_missing_approval_json_effective_status_not_satisfied():
    context = build_pr_context(head_ref_oid="head123", latest_reviews=())
    output = make_workflow_status(context).to_dict()
    approval = output["approval"]
    assert approval["effective_status"] == "missing"
    assert approval["effective_source"] is None


def test_stale_approval_json_effective_status_not_satisfied():
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    output = make_workflow_status(context).to_dict()
    approval = output["approval"]
    assert approval["effective_status"] == "stale"
    assert approval["effective_source"] is None


# ---------------------------------------------------------------------------
# Workflow posture: human_decision_required
# ---------------------------------------------------------------------------

def test_posture_human_decision_unknown_mergeability():
    context = scenario_mergeable_unknown(head_sha="head123")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "human_decision_required"
    assert any("UNKNOWN" in r or "unknown" in r.lower() for r in summary.workflow_posture.reasons)


def test_posture_human_decision_dirty_merge_state():
    context = scenario_current_approval(head_sha="head123", merge_state_status="DIRTY")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status == "human_decision_required"


# ---------------------------------------------------------------------------
# Draft PR and stale approval safety guarantees
# ---------------------------------------------------------------------------

def test_draft_pr_not_merge_validation():
    context = scenario_draft_pr(head_ref_oid="head123")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status != "merge_validation_required"


def test_stale_approval_not_merge_validation():
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    summary = make_workflow_status(context)
    assert summary.workflow_posture.status != "merge_validation_required"
    assert summary.merge_readiness.hard_gate_passed is False


# ---------------------------------------------------------------------------
# ok=True even for blocked postures
# ---------------------------------------------------------------------------

def test_ok_true_when_implementation_required():
    context = scenario_draft_pr(head_ref_oid="head123")
    summary = make_workflow_status(context)
    assert summary.ok is True


def test_ok_true_when_review_required():
    context = scenario_stale_approval(head_sha="new", approval_sha="old")
    summary = make_workflow_status(context)
    assert summary.ok is True


def test_ok_true_when_waiting():
    context = with_context(
        scenario_current_approval(head_sha="head123"),
        status_checks=(build_check(name="ci", bucket="pending", state="PENDING"),),
    )
    summary = make_workflow_status(context, required_check_names=("ci",))
    assert summary.ok is True


def test_ok_true_when_human_decision():
    context = scenario_mergeable_unknown(head_sha="head123")
    summary = make_workflow_status(context)
    assert summary.ok is True


# ---------------------------------------------------------------------------
# JSON output contract
# ---------------------------------------------------------------------------

def test_json_output_top_level_keys():
    context = scenario_current_approval(head_sha="head123")
    output = make_workflow_status(context).to_dict()
    for key in (
        "command", "ok", "repository", "pr", "github_truth", "local_state",
        "approval", "re_review", "checks", "review_threads", "merge_readiness",
        "workflow_posture", "warnings", "errors",
    ):
        assert key in output, f"Missing key: {key}"


def test_json_output_command_field():
    context = scenario_current_approval(head_sha="head123")
    assert make_workflow_status(context).to_dict()["command"] == "workflow-status"


def test_json_output_ok_true():
    context = scenario_current_approval(head_sha="head123")
    assert make_workflow_status(context).to_dict()["ok"] is True


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
    output = make_workflow_status(context, repository="owner/repo").to_dict()
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


def test_json_output_github_truth_keys():
    context = scenario_current_approval(head_sha="head123")
    truth = make_workflow_status(context).to_dict()["github_truth"]
    for key in ("state", "is_draft", "base_ref_name", "head_ref_name", "head_ref_oid", "mergeable", "merge_state_status", "review_decision"):
        assert key in truth, f"Missing github_truth key: {key}"


def test_json_output_approval_fields():
    context = scenario_current_approval(head_sha="head123")
    approval = make_workflow_status(context).to_dict()["approval"]
    for key in ("approval_status", "latest_review_sha", "latest_approval_sha", "solo_override",
                "approval_source", "satisfied_by", "blocking_reasons", "hard_gate_passed"):
        assert key in approval, f"Missing approval key: {key}"
    assert approval["approval_status"] == "current"
    assert approval["hard_gate_passed"] is True


def test_json_output_re_review_fields():
    context = scenario_stale_approval(head_sha="new", approval_sha="old")
    rr = make_workflow_status(context).to_dict()["re_review"]
    assert "re_review_needed" in rr
    assert "hard_gate_passed" in rr
    assert "blocking_reasons" in rr
    assert rr["re_review_needed"] is True
    assert rr["hard_gate_passed"] is False


def test_json_output_checks_fields():
    context = scenario_current_approval(head_sha="head123")
    checks = make_workflow_status(context).to_dict()["checks"]
    for key in ("status_rollup", "required_check_status", "check_buckets", "messages"):
        assert key in checks, f"Missing checks key: {key}"


def test_json_output_review_threads_fields():
    context = scenario_current_approval(head_sha="head123")
    threads = make_workflow_status(context).to_dict()["review_threads"]
    assert "thread_counts" in threads
    assert "hard_gate_passed" in threads
    assert "unresolved_blocking_threads" in threads


def test_json_output_merge_readiness_fields():
    context = scenario_current_approval(head_sha="head123")
    mr = make_workflow_status(context).to_dict()["merge_readiness"]
    for key in ("hard_gate_passed", "blocking_reasons", "checks", "mergeable", "merge_state_status"):
        assert key in mr, f"Missing merge_readiness key: {key}"


def test_json_output_merged_pr_contract():
    context = scenario_merged_pr(head_sha="head123")
    output = make_workflow_status(context).to_dict()

    assert output["ok"] is True
    assert output["pr"]["state"] == "MERGED"
    assert output["github_truth"]["state"] == "MERGED"
    assert output["workflow_posture"]["status"] == "merged"
    assert output["workflow_posture"]["summary"] == (
        "PR is merged. No further merge action required. Run post-merge-sync to update local state."
    )
    assert "pre-merge" not in output["workflow_posture"]["source_commands"]
    assert output["merge_readiness"]["blocking_reasons"] == []
    assert not any("mergeability" in warning.lower() for warning in output["warnings"])


def test_json_output_workflow_posture_fields():
    context = scenario_current_approval(head_sha="head123")
    posture = make_workflow_status(context).to_dict()["workflow_posture"]
    for key in ("status", "summary", "reasons", "source_commands"):
        assert key in posture, f"Missing workflow_posture key: {key}"
    assert posture["status"] in (
        "implementation_required",
        "review_required",
        "merge_validation_required",
        "waiting",
        "human_decision_required",
        "merged",
    )


def test_json_output_local_state_present():
    context = scenario_current_approval(head_sha="head123")
    output = make_workflow_status(context).to_dict()
    assert "local_state" in output
    assert output["local_state"]["command"] == "worktree-status"


def test_json_output_repository():
    context = scenario_current_approval(head_sha="head123")
    output = make_workflow_status(context, repository="myorg/myrepo").to_dict()
    assert output["repository"] == "myorg/myrepo"


# ---------------------------------------------------------------------------
# Local worktree state surfacing
# ---------------------------------------------------------------------------

def test_dirty_local_state_produces_warnings():
    context = scenario_current_approval(head_sha="head123")
    local = make_dirty_local_state(staged=("a.py",), unstaged=("b.py",))
    summary = make_workflow_status(context, local_state=local)
    assert any("uncommitted changes" in w for w in summary.warnings)


def test_conflicted_local_state_produces_warnings():
    context = scenario_current_approval(head_sha="head123")
    local = make_dirty_local_state(staged=(), unstaged=(), conflicted=("CONFLICT.md",))
    summary = make_workflow_status(context, local_state=local)
    assert any("conflicted" in w for w in summary.warnings)


def test_dirty_local_state_does_not_affect_ok():
    context = scenario_current_approval(head_sha="head123")
    local = make_dirty_local_state()
    summary = make_workflow_status(context, local_state=local)
    assert summary.ok is True


def test_dirty_local_state_does_not_change_github_posture():
    context = scenario_current_approval(head_sha="head123", status_checks=())
    local = make_dirty_local_state(staged=("x.py",), unstaged=("y.py",))
    summary = make_workflow_status(context, local_state=local)
    assert summary.workflow_posture.status == "merge_validation_required"


def test_untracked_only_no_warning():
    context = scenario_current_approval(head_sha="head123")
    local = make_dirty_local_state(staged=(), unstaged=(), untracked=("scratch.txt",))
    summary = make_workflow_status(context, local_state=local)
    assert not any("uncommitted changes" in w for w in summary.warnings)


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def test_warning_empty_status_rollup():
    context = scenario_current_approval(head_sha="head123", status_checks=())
    summary = make_workflow_status(context)
    assert any("No status checks" in w for w in summary.warnings)


def test_warning_unknown_mergeability():
    context = scenario_mergeable_unknown(head_sha="head123")
    summary = make_workflow_status(context)
    assert any("UNKNOWN" in w for w in summary.warnings)


def test_failed_local_state_warning():
    context = scenario_current_approval(head_sha="head123")
    local = make_failed_local_state()
    summary = make_workflow_status(context, local_state=local)
    assert any("worktree" in w.lower() for w in summary.warnings)


# ---------------------------------------------------------------------------
# Shared helper alignment
# ---------------------------------------------------------------------------

def test_approval_matches_approval_check_helper():
    context = scenario_stale_approval(head_sha="new", approval_sha="old")
    summary = make_workflow_status(context)
    standalone = summarize_approval_check(context)
    assert summary.approval.approval_status == standalone.approval_status
    assert summary.approval.hard_gate_passed == standalone.hard_gate_passed
    assert summary.approval.blocking_reasons == standalone.blocking_reasons


def test_re_review_matches_re_review_needed_helper():
    context = scenario_stale_approval(head_sha="new", approval_sha="old")
    summary = make_workflow_status(context)
    standalone = summarize_re_review_needed(context)
    assert summary.re_review.re_review_needed == standalone.re_review_needed
    assert summary.re_review.hard_gate_passed == standalone.hard_gate_passed


def test_ci_matches_ci_summary_helper():
    context = scenario_current_approval(head_sha="head123", status_checks=())
    required_checks = make_required_checks()
    ci_standalone = summarize_ci(context, required_checks=required_checks)
    summary = make_workflow_status(context)
    assert summary.ci.status_rollup == ci_standalone.status_rollup
    assert summary.ci.required_check_status == ci_standalone.required_check_status


def test_review_threads_matches_summarize_review_threads_helper():
    raw_thread = {
        "id": "t1",
        "path": "a.py",
        "line": 1,
        "startLine": None,
        "isResolved": False,
        "isOutdated": False,
        "comments": {"totalCount": 0, "nodes": []},
    }
    context = scenario_current_approval(head_sha="head123")
    standalone = summarize_review_threads(context, (raw_thread,))
    summary = make_workflow_status(context, raw_threads=(raw_thread,))
    assert summary.review_threads.hard_gate_passed == standalone.hard_gate_passed
    assert summary.review_threads.thread_counts == standalone.thread_counts


def test_merge_readiness_matches_pre_merge_helper():
    context = scenario_current_approval(head_sha="head123", status_checks=())
    required_checks = make_required_checks()
    threads = summarize_review_threads(context, ())
    standalone = summarize_pre_merge(
        context,
        expected_base_ref_name="main",
        required_checks=required_checks,
        review_threads=threads,
    )
    summary = make_workflow_status(context)
    assert summary.merge_readiness.hard_gate_passed == standalone.hard_gate_passed
    assert summary.merge_readiness.blocking_reasons == standalone.blocking_reasons


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

from headless_pr_workflow.cli import main


def test_cli_workflow_status_json_output(capsys):
    context = scenario_current_approval(
        head_sha="head123",
        status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
    )
    required_checks = RequiredStatusChecks(names=("unit",), status="configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo", "--json"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "workflow-status"
    assert output["ok"] is True
    assert output["workflow_posture"]["status"] == "merge_validation_required"


def test_cli_workflow_status_json_output_merged_pr(capsys):
    context = scenario_merged_pr(head_sha="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo", "--json"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["pr"]["state"] == "MERGED"
    assert output["github_truth"]["state"] == "MERGED"
    assert output["workflow_posture"]["status"] == "merged"
    assert output["merge_readiness"]["blocking_reasons"] == []


def test_cli_workflow_status_human_output(capsys):
    context = scenario_stale_approval(head_sha="new-head", approval_sha="old-head")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #123" in out
    assert "workflow posture: review_required" in out


def test_cli_workflow_status_human_output_merged_pr(capsys):
    context = scenario_merged_pr(head_sha="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "state: MERGED" in out
    assert "workflow posture: merged" in out
    assert (
        "posture summary: PR is merged. No further merge action required. "
        "Run post-merge-sync to update local state."
    ) in out
    assert "workflow posture: human_decision_required" not in out
    assert "merge readiness: blocked" not in out
    assert "merge blocking reasons:" not in out
    assert "PR mergeable state is UNKNOWN." not in out
    assert "PR merge state status is UNKNOWN." not in out


def test_cli_workflow_status_github_fetch_failure_json(capsys):
    error = GHCommandError(
        command=["gh", "pr", "view"],
        returncode=1,
        stderr="not found",
        error="not-found",
    )
    with patch("headless_pr_workflow.cli.fetch_pr_context", side_effect=error):
        rc = main(["workflow-status", "999", "--repo", "owner/repo", "--json"])

    assert rc == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "error" in output


def test_cli_workflow_status_github_fetch_failure_human(capsys):
    error = GHCommandError(
        command=["gh", "pr", "view"],
        returncode=1,
        stderr="not found",
        error="not-found",
    )
    with patch("headless_pr_workflow.cli.fetch_pr_context", side_effect=error):
        rc = main(["workflow-status", "999", "--repo", "owner/repo"])

    assert rc == 1


def test_cli_workflow_status_local_inspection_failure_json(capsys):
    context = scenario_current_approval(head_sha="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_failed_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo", "--json"])

    assert rc == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "errors" in output


def test_cli_workflow_status_local_inspection_failure_human(capsys):
    context = scenario_current_approval(head_sha="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_failed_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "local worktree" in err.lower() or "workflow-status" in err.lower()


def test_cli_workflow_status_implementation_posture_exits_zero(capsys):
    context = scenario_draft_pr(head_ref_oid="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo", "--json"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["workflow_posture"]["status"] == "implementation_required"


def test_cli_workflow_status_with_path_flag(capsys):
    context = scenario_current_approval(head_sha="head123", status_checks=())
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state(path="/custom/path")

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state) as mock_worktree,
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo", "--path", "/custom/path", "--json"])

    assert rc == 0
    mock_worktree.assert_called_once_with("/custom/path")


def test_cli_workflow_status_usage_error():
    with pytest.raises(SystemExit) as exc_info:
        main(["workflow-status", "--unknown-flag"])
    assert exc_info.value.code == 2


def test_cli_workflow_status_human_shows_local_section(capsys):
    context = scenario_current_approval(head_sha="head123", status_checks=())
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[local worktree]" in out
    assert "[github truth]" in out
    assert "workflow posture:" in out


def test_cli_workflow_status_human_shows_dirty_warning(capsys):
    context = scenario_current_approval(head_sha="head123", status_checks=())
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_dirty_local_state(staged=("a.py",), unstaged=("b.py",))

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "warnings:" in out
    assert "uncommitted changes" in out


# ---------------------------------------------------------------------------
# CLI tests: solo-maintainer override approval label (issue #79)
# ---------------------------------------------------------------------------

def test_cli_workflow_status_human_solo_override_shows_satisfied(capsys):
    context = scenario_solo_override(head_sha="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo"])

    assert rc == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "approval status: satisfied (solo-maintainer override)" in lines
    assert "formal approval status: missing" in lines
    assert "approval status: missing" not in lines


def test_cli_workflow_status_human_solo_override_preserves_override_facts(capsys):
    context = scenario_solo_override(head_sha="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "solo-maintainer override: accepted" in out
    assert "approval source: solo-maintainer-override" in out
    assert "satisfied by: solo-maintainer-override" in out


def test_cli_workflow_status_human_formal_approval_unchanged(capsys):
    context = scenario_current_approval(head_sha="head123", status_checks=())
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "approval status: current" in out
    assert "formal approval status:" not in out


def test_cli_workflow_status_json_solo_override_effective_fields(capsys):
    context = scenario_solo_override(head_sha="head123")
    required_checks = RequiredStatusChecks(names=(), status="not_configured")
    local_state = make_clean_local_state()

    with (
        patch("headless_pr_workflow.cli.fetch_pr_context", return_value=context),
        patch("headless_pr_workflow.cli.fetch_repo_default_branch", return_value="main"),
        patch("headless_pr_workflow.cli.fetch_required_status_check_context", return_value=required_checks),
        patch("headless_pr_workflow.cli.fetch_review_threads_for_context", return_value=()),
        patch("headless_pr_workflow.cli.summarize_worktree_status", return_value=local_state),
    ):
        rc = main(["workflow-status", "123", "--repo", "owner/repo", "--json"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    approval = output["approval"]
    assert approval["effective_status"] == "satisfied"
    assert approval["effective_source"] == "solo-maintainer-override"
    assert approval["approval_status"] == "missing"
    assert approval["solo_override"]["status"] == "accepted"
    assert approval["approval_source"] == "solo-maintainer-override"
    assert approval["satisfied_by"] == "solo-maintainer-override"
    assert approval["hard_gate_passed"] is True
