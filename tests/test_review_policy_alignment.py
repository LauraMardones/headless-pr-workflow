import pytest

from headless_pr_workflow.approval_check import summarize_approval_check
from headless_pr_workflow.pre_merge import summarize_pre_merge
from headless_pr_workflow.re_review_needed import summarize_re_review_needed
from headless_pr_workflow.review_sha import summarize_review_sha

from tests.github_scenarios import (
    build_pr_context,
    scenario_changes_requested,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_solo_override,
    scenario_stale_approval,
)


@pytest.mark.parametrize(
    ("context", "expected_hard_gate", "expected_approval_status", "expected_override_status", "expected_blocking_reason"),
    [
        (scenario_current_approval(head_sha="head123"), True, "current", "missing", None),
        (
            scenario_stale_approval(head_sha="new-head", approval_sha="old-head"),
            False,
            "stale",
            "missing",
            "formal approval is stale for the current PR head SHA",
        ),
        (
            scenario_changes_requested(head_sha="head123"),
            False,
            "current",
            "missing",
            "GitHub review decision is CHANGES_REQUESTED for the current PR head.",
        ),
        (
            scenario_solo_override(head_sha="head123"),
            True,
            "missing",
            "accepted",
            None,
        ),
        (
            scenario_comment_only_review(head_sha="head123"),
            False,
            "missing",
            "missing",
            "comment-only review exists without formal approval or an accepted solo-maintainer override",
        ),
        (
            build_pr_context(head_ref_oid=""),
            False,
            "unknown-head",
            "missing",
            "Current PR head SHA is unknown, so approval cannot be verified.",
        ),
    ],
)
def test_review_gate_truth_stays_aligned_across_commands(
    context,
    expected_hard_gate,
    expected_approval_status,
    expected_override_status,
    expected_blocking_reason,
):
    review_sha = summarize_review_sha(context)
    approval = summarize_approval_check(context)
    re_review = summarize_re_review_needed(context)
    pre_merge = summarize_pre_merge(context, expected_base_ref_name="main", required_check_names=())
    approval_gate = next(check for check in pre_merge.checks if check.code == "approval-current-head")

    assert review_sha.approval_status == expected_approval_status
    assert approval.approval_status == expected_approval_status
    assert re_review.approval_status == expected_approval_status
    assert approval.solo_override.status == expected_override_status
    assert re_review.solo_override.status == expected_override_status

    assert review_sha.hard_gate_passed is expected_hard_gate
    assert approval.hard_gate_passed is expected_hard_gate
    assert re_review.hard_gate_passed is expected_hard_gate
    assert re_review.re_review_needed is (not expected_hard_gate)
    assert pre_merge.approval.hard_gate_passed is expected_hard_gate
    assert approval_gate.ok is expected_hard_gate

    if expected_blocking_reason is None:
        assert approval.blocking_reason is None
        assert approval.blocking_reasons == ()
        assert re_review.blocking_reason is None
        assert re_review.blocking_reasons == ()
        assert approval_gate.details == ()
    else:
        assert approval.blocking_reason == expected_blocking_reason
        assert approval.blocking_reasons == (expected_blocking_reason,)
        assert re_review.blocking_reason == expected_blocking_reason
        assert re_review.blocking_reasons == (expected_blocking_reason,)
        assert expected_blocking_reason in pre_merge.blocking_reasons
        assert approval_gate.details == (expected_blocking_reason,)
