from headless_pr_workflow.approval_check import summarize_approval_check
from headless_pr_workflow.github.pr_context import parse_pr_context

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


def test_approval_check_passes_for_current_formal_approval():
    summary = summarize_approval_check(scenario_current_approval(head_sha="head"))

    assert summary.approval_status == "current"
    assert summary.solo_override.status == "missing"
    assert summary.approval_source == "formal"
    assert summary.satisfied_by == "formal-approval"
    assert summary.hard_gate_passed is True
    assert summary.blocking_reason is None
    assert summary.blocking_reasons == ()


def test_approval_check_fails_for_stale_formal_approval():
    summary = summarize_approval_check(scenario_stale_approval(head_sha="new-head", approval_sha="old-head"))

    assert summary.approval_status == "stale"
    assert summary.solo_override.status == "missing"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "formal approval is stale for the current PR head SHA"
    assert summary.blocking_reasons == ("formal approval is stale for the current PR head SHA",)


def test_approval_check_fails_for_missing_approval():
    summary = summarize_approval_check(build_pr_context(head_ref_oid="head"))

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "missing"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "no formal approval or accepted solo-maintainer override exists for the current PR head SHA"


def test_approval_check_rejects_unknown_head_sha():
    summary = summarize_approval_check(build_pr_context(head_ref_oid=""))

    assert summary.approval_status == "unknown-head"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "Current PR head SHA is unknown, so approval cannot be verified."


def test_approval_check_rejects_active_change_request_decision():
    summary = summarize_approval_check(scenario_changes_requested(head_sha="head"))

    assert summary.blocking_reason == "GitHub review decision is CHANGES_REQUESTED for the current PR head."
    assert summary.blocking_reasons == ("GitHub review decision is CHANGES_REQUESTED for the current PR head.",)
    assert summary.hard_gate_passed is False


def test_approval_check_fails_for_comment_only_review_without_override():
    summary = summarize_approval_check(scenario_comment_only_review(head_sha="head"))

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "missing"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "comment-only review exists without formal approval or an accepted solo-maintainer override"


def test_approval_check_passes_for_valid_solo_maintainer_override():
    summary = summarize_approval_check(scenario_solo_override(head_sha="head"))

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "accepted"
    assert summary.approval_source == "solo-maintainer-override"
    assert summary.satisfied_by == "solo-maintainer-override"
    assert summary.hard_gate_passed is True
    assert summary.blocking_reason is None


def test_approval_check_keeps_valid_override_when_newer_comment_is_not_an_override():
    head_sha = "head"
    summary = summarize_approval_check(
        build_pr_context(
            head_ref_oid=head_sha,
            latest_reviews=(
                build_review(state="COMMENTED", commit_oid=head_sha, body=solo_override_body(head_sha=head_sha)),
                build_review(
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:05:00Z",
                    commit_oid=head_sha,
                    body="Follow-up note: verify changelog wording.",
                ),
            ),
        )
    )

    assert summary.solo_override.status == "accepted"
    assert summary.satisfied_by == "solo-maintainer-override"
    assert summary.hard_gate_passed is True


def test_approval_check_rejects_older_override_after_newer_same_head_revocation():
    head_sha = "head"
    summary = summarize_approval_check(
        build_pr_context(
            head_ref_oid=head_sha,
            latest_reviews=(
                build_review(state="COMMENTED", commit_oid=head_sha, body=solo_override_body(head_sha=head_sha)),
                build_review(
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:05:00Z",
                    commit_oid=head_sha,
                    body="Solo-maintainer override is no longer accepted; blocker found.",
                ),
            ),
        )
    )

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "invalid"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "current-head review comment does not contain an accepted solo-maintainer override"


def test_approval_check_ignores_old_override_review_on_previous_head():
    summary = summarize_approval_check(
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

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "stale"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "solo-maintainer override was recorded on a previous PR head SHA"


def test_approval_check_uses_reviews_surface_for_current_formal_approval():
    summary = summarize_approval_check(
        parse_pr_context(
            {
                "baseRefName": "main",
                "headRefName": "feature",
                "headRefOid": "head",
                "latestReviews": [
                    {
                        "author": {"login": "reviewer"},
                        "state": "APPROVED",
                        "submittedAt": "2026-04-21T10:00:00Z",
                        "commit": {"oid": ""},
                        "body": "",
                    }
                ],
                "number": 27,
                "reviews": [
                    {
                        "author": {"login": "reviewer"},
                        "state": "APPROVED",
                        "submittedAt": "2026-04-21T10:00:00Z",
                        "commit": {"oid": "head"},
                        "body": "approved from reviews",
                    }
                ],
                "state": "OPEN",
                "title": "Formal approval from reviews",
                "url": "https://github.com/owner/repo/pull/27",
            }
        )
    )

    assert summary.approval_status == "current"
    assert summary.approval_source == "formal"
    assert summary.hard_gate_passed is True


def test_approval_check_reports_comment_only_review_from_reviews_surface():
    summary = summarize_approval_check(
        parse_pr_context(
            {
                "baseRefName": "main",
                "headRefName": "feature",
                "headRefOid": "head",
                "number": 28,
                "reviews": [
                    {
                        "author": {"login": "reviewer"},
                        "state": "COMMENTED",
                        "submittedAt": "2026-04-21T10:00:00Z",
                        "commit": {"oid": "head"},
                        "body": "Please double-check the docs.",
                    }
                ],
                "state": "OPEN",
                "title": "Comment only from reviews",
                "url": "https://github.com/owner/repo/pull/28",
            }
        )
    )

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "missing"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "comment-only review exists without formal approval or an accepted solo-maintainer override"


def test_approval_check_accepts_solo_override_from_reviews_surface():
    summary = summarize_approval_check(
        parse_pr_context(
            {
                "baseRefName": "main",
                "headRefName": "feature",
                "headRefOid": "head",
                "number": 29,
                "reviews": [
                    {
                        "author": {"login": "maintainer"},
                        "state": "COMMENTED",
                        "submittedAt": "2026-04-21T10:00:00Z",
                        "commit": {"oid": "head"},
                        "body": solo_override_body(head_sha="head"),
                    }
                ],
                "state": "OPEN",
                "title": "Override from reviews",
                "url": "https://github.com/owner/repo/pull/29",
            }
        )
    )

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "accepted"
    assert summary.approval_source == "solo-maintainer-override"
    assert summary.hard_gate_passed is True
