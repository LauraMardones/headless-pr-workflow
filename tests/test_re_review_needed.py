from headless_pr_workflow.re_review_needed import summarize_re_review_needed

from tests.github_scenarios import (
    build_pr_context,
    build_review,
    scenario_changes_requested,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_solo_override,
    scenario_stale_approval,
    solo_override_body,
)


def test_re_review_needed_passes_for_current_formal_approval():
    summary = summarize_re_review_needed(scenario_current_approval(head_sha="head"))

    assert summary.head_ref_oid == "head"
    assert summary.latest_review_sha == "head"
    assert summary.latest_review_state == "APPROVED"
    assert summary.latest_review_author == "reviewer"
    assert summary.latest_approval_sha == "head"
    assert summary.approval_status == "current"
    assert summary.approval_source == "formal"
    assert summary.satisfied_by == "formal-approval"
    assert summary.re_review_needed is False
    assert summary.hard_gate_passed is True
    assert summary.blocking_reasons == ()


def test_re_review_needed_passes_for_current_accepted_solo_override():
    summary = summarize_re_review_needed(scenario_solo_override(head_sha="head"))

    assert summary.latest_review_sha == "head"
    assert summary.latest_review_state == "COMMENTED"
    assert summary.latest_approval_sha is None
    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "accepted"
    assert summary.approval_source == "solo-maintainer-override"
    assert summary.satisfied_by == "solo-maintainer-override"
    assert summary.re_review_needed is False
    assert summary.hard_gate_passed is True


def test_re_review_needed_fails_for_stale_formal_approval():
    summary = summarize_re_review_needed(scenario_stale_approval(head_sha="new-head", approval_sha="old-head"))

    assert summary.latest_review_sha == "old-head"
    assert summary.latest_approval_sha == "old-head"
    assert summary.approval_status == "stale"
    assert summary.re_review_needed is True
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "formal approval is stale for the current PR head SHA"


def test_re_review_needed_fails_for_stale_solo_override():
    summary = summarize_re_review_needed(
        build_pr_context(
            head_ref_oid="new-head",
            latest_reviews=(
                build_review(
                    state="COMMENTED",
                    commit_oid="old-head",
                    body=solo_override_body(head_sha="old-head"),
                ),
            ),
        )
    )

    assert summary.solo_override.status == "stale"
    assert summary.re_review_needed is True
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "solo-maintainer override was recorded on a previous PR head SHA"


def test_re_review_needed_fails_for_missing_review_evidence():
    summary = summarize_re_review_needed(build_pr_context(head_ref_oid="head"))

    assert summary.latest_review_sha is None
    assert summary.latest_approval_sha is None
    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "missing"
    assert summary.re_review_needed is True
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "no formal approval or accepted solo-maintainer override exists for the current PR head SHA"


def test_re_review_needed_fails_for_comment_only_review_without_override():
    summary = summarize_re_review_needed(scenario_comment_only_review(head_sha="head"))

    assert summary.latest_review_state == "COMMENTED"
    assert summary.approval_status == "missing"
    assert summary.re_review_needed is True
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "comment-only review exists without formal approval or an accepted solo-maintainer override"


def test_re_review_needed_fails_for_changes_requested():
    summary = summarize_re_review_needed(scenario_changes_requested(head_sha="head"))

    assert summary.latest_approval_sha == "head"
    assert summary.approval_status == "current"
    assert summary.re_review_needed is True
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "GitHub review decision is CHANGES_REQUESTED for the current PR head."


def test_re_review_needed_fails_for_unknown_head_sha():
    summary = summarize_re_review_needed(build_pr_context(head_ref_oid=""))

    assert summary.head_ref_oid == ""
    assert summary.approval_status == "unknown-head"
    assert summary.re_review_needed is True
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "Current PR head SHA is unknown, so approval cannot be verified."


def test_re_review_needed_json_contract_includes_stable_fields():
    summary = summarize_re_review_needed(scenario_current_approval(head_sha="head"))

    payload = summary.to_dict()

    assert payload["number"] == 123
    assert payload["title"] == "Scenario PR"
    assert payload["url"] == "https://github.com/owner/repo/pull/123"
    assert payload["head_ref_oid"] == "head"
    assert payload["latest_review_sha"] == "head"
    assert payload["latest_review_state"] == "APPROVED"
    assert payload["latest_review_author"] == "reviewer"
    assert payload["latest_approval_sha"] == "head"
    assert payload["approval_status"] == "current"
    assert payload["solo_override"]["status"] == "missing"
    assert payload["approval_source"] == "formal"
    assert payload["satisfied_by"] == "formal-approval"
    assert payload["re_review_needed"] is False
    assert payload["hard_gate_passed"] is True
    assert payload["blocking_reason"] is None
    assert payload["blocking_reasons"] == []
    assert payload["reviews"][0]["commit_oid"] == "head"
