from headless_pr_workflow.pre_merge import summarize_pre_merge
from headless_pr_workflow.github.review_threads import summarize_review_threads

from tests.github_scenarios import (
    build_check,
    build_pr_context,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_dirty_merge_state,
    scenario_draft_pr,
    scenario_empty_status_rollup,
    scenario_failing_checks,
    scenario_mergeable_unknown,
    scenario_pending_checks,
    scenario_solo_override,
    scenario_stale_approval,
    with_context,
)


def review_thread(
    *,
    thread_id: str = "thread-1",
    path: str = "src/app.py",
    line: int = 10,
    is_resolved: bool = False,
    is_outdated: bool = False,
) -> dict:
    return {
        "id": thread_id,
        "path": path,
        "line": line,
        "startLine": None,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "comments": {"totalCount": 0, "nodes": []},
    }


def test_pre_merge_passes_when_all_core_gates_pass():
    summary = summarize_pre_merge(
        scenario_current_approval(
            head_sha="head123",
            status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
        ),
        expected_base_ref_name="main",
        required_check_names=("unit",),
    )

    assert summary.blocking_reasons == ()
    assert summary.hard_gate_passed is True
    assert all(check.ok for check in summary.checks)
    output = summary.to_dict()
    assert output["current_head_sha"] == "head123"
    assert output["pr"]["number"] == 123
    assert output["approval_review_source"]["approval_source"] == "formal"
    assert output["target_branch_comparison"]["result"] == "pass"
    assert output["required_check_summary"]["required_check_status"] == "satisfied"
    assert output["mergeability_facts"]["mergeable"] == "MERGEABLE"
    assert output["unresolved_thread_summary"]["thread_counts"]["unresolved_blocking"] == 0


def test_pre_merge_blocks_draft_pr():
    summary = summarize_pre_merge(
        with_context(
            scenario_draft_pr(head_ref_oid="head123"),
            latest_reviews=(scenario_current_approval(head_sha="head123").latest_reviews[0],),
        ),
        expected_base_ref_name="main",
        required_check_names=(),
    )

    assert "PR is draft." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_stale_approval():
    summary = summarize_pre_merge(
        scenario_stale_approval(head_sha="new-head", approval_sha="old-head"),
        expected_base_ref_name="main",
        required_check_names=(),
    )

    assert "formal approval is stale for the current PR head SHA" in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_failing_and_pending_checks():
    summary = summarize_pre_merge(
        with_context(
            scenario_failing_checks(head_sha="head123", check_name="unit"),
            status_checks=(
                build_check(name="unit", bucket="failure", status="COMPLETED", conclusion="FAILURE"),
                build_check(name="lint", bucket="pending", state="PENDING"),
            ),
        ),
        expected_base_ref_name="main",
        required_check_names=("unit", "lint"),
    )

    assert "Status check unit is failing (status=COMPLETED, conclusion=FAILURE)." in summary.blocking_reasons
    assert "Status check lint is pending (state=PENDING)." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_mergeable_unknown():
    summary = summarize_pre_merge(
        scenario_mergeable_unknown(head_sha="head123"),
        expected_base_ref_name="main",
        required_check_names=(),
    )

    assert "PR mergeable state is UNKNOWN." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_dirty_merge_state():
    summary = summarize_pre_merge(
        scenario_dirty_merge_state(head_sha="head123"),
        expected_base_ref_name="main",
        required_check_names=(),
    )

    assert "PR merge state status is DIRTY." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_missing_head_sha():
    summary = summarize_pre_merge(build_pr_context(head_ref_oid=""), expected_base_ref_name="main", required_check_names=())

    assert "Current PR head SHA is unknown." in summary.blocking_reasons
    assert "Current PR head SHA is unknown, so approval cannot be verified." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_unexpected_target_branch():
    summary = summarize_pre_merge(
        scenario_current_approval(
            head_sha="head123",
            base_ref_name="release",
            status_checks=(build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
        ),
        expected_base_ref_name="main",
        required_check_names=("unit",),
    )

    assert "PR targets base branch release, expected main." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_when_required_checks_are_not_reported():
    summary = summarize_pre_merge(
        scenario_empty_status_rollup(head_sha="head123"),
        expected_base_ref_name="main",
        required_check_names=("unit",),
    )

    assert "GitHub reported no status checks for the current head SHA." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_unresolved_review_threads():
    context = scenario_current_approval(head_sha="head123")
    review_threads = summarize_review_threads(context, (review_thread(thread_id="active-thread"),))

    summary = summarize_pre_merge(
        context,
        expected_base_ref_name="main",
        required_check_names=(),
        review_threads=review_threads,
    )

    assert "Unresolved review thread src/app.py:10 (active-thread): Thread is unresolved and still applies to the current PR head." in summary.blocking_reasons
    assert next(check for check in summary.checks if check.code == "unresolved-review-threads").ok is False
    assert summary.to_dict()["unresolved_thread_summary"]["thread_counts"]["unresolved_blocking"] == 1
    assert summary.hard_gate_passed is False


def test_pre_merge_allows_absent_checks_when_none_are_required():
    summary = summarize_pre_merge(
        scenario_solo_override(head_sha="head123"),
        expected_base_ref_name="main",
        required_check_names=(),
    )

    assert "GitHub reported no required status checks for the target branch." not in summary.blocking_reasons
    assert summary.checks[5].ok is True
