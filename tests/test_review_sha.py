from headless_pr_workflow.review_sha import summarize_review_sha

from tests.github_scenarios import (
    build_pr_context,
    scenario_changes_requested,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_solo_override,
    scenario_stale_approval,
)


def test_review_sha_reports_current_approval():
    summary = summarize_review_sha(scenario_current_approval(head_sha="head"))

    assert summary.head_ref_oid == "head"
    assert summary.latest_review_sha == "head"
    assert summary.latest_approval_sha == "head"
    assert summary.approval_status == "current"
    assert summary.hard_gate_passed is True


def test_review_sha_reports_stale_approval():
    summary = summarize_review_sha(scenario_stale_approval(head_sha="new-head", approval_sha="old-head"))

    assert summary.latest_review_sha == "old-head"
    assert summary.latest_approval_sha == "old-head"
    assert summary.approval_status == "stale"
    assert summary.hard_gate_passed is False


def test_review_sha_reports_missing_approval_with_comment_only_review():
    summary = summarize_review_sha(scenario_comment_only_review(head_sha="head"))

    assert summary.latest_review_sha == "head"
    assert summary.latest_review_state == "COMMENTED"
    assert summary.latest_approval_sha is None
    assert summary.approval_status == "missing"
    assert summary.hard_gate_passed is False


def test_review_sha_reports_no_reviews():
    summary = summarize_review_sha(build_pr_context(head_ref_oid="head"))

    assert summary.latest_review_sha is None
    assert summary.latest_approval_sha is None
    assert summary.approval_status == "missing"
    assert summary.hard_gate_passed is False


def test_review_sha_blocks_current_approval_when_changes_requested():
    summary = summarize_review_sha(scenario_changes_requested(head_sha="head"))

    assert summary.approval_status == "current"
    assert summary.hard_gate_passed is False


def test_review_sha_accepts_current_head_solo_override():
    summary = summarize_review_sha(scenario_solo_override(head_sha="head"))

    assert summary.approval_status == "missing"
    assert summary.latest_review_sha == "head"
    assert summary.hard_gate_passed is True
