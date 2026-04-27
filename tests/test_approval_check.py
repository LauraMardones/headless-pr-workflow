from headless_pr_workflow.approval_check import summarize_approval_check
from headless_pr_workflow.github.pr_context import PullRequestContext, ReviewSummary


def _context(*, head_sha: str = "head", reviews: tuple[ReviewSummary, ...] = ()) -> PullRequestContext:
    return PullRequestContext(
        number=123,
        title="Approval Check",
        state="OPEN",
        url="https://github.com/owner/repo/pull/123",
        base_ref_name="main",
        base_ref_oid="base",
        head_ref_name="feature/approval-check",
        head_ref_oid=head_sha,
        head_repository="owner/repo",
        head_repository_owner="owner",
        is_cross_repository=False,
        is_draft=False,
        merge_state_status="CLEAN",
        mergeable="MERGEABLE",
        review_decision=None,
        changed_files=1,
        additions=1,
        deletions=0,
        labels=(),
        latest_reviews=reviews,
        review_requests=(),
        status_checks=(),
        raw={},
    )


def test_approval_check_passes_for_current_formal_approval():
    summary = summarize_approval_check(
        _context(
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head"),),
        )
    )

    assert summary.approval_status == "current"
    assert summary.solo_override.status == "missing"
    assert summary.satisfied_by == "formal-approval"
    assert summary.hard_gate_passed is True
    assert summary.blocking_reason is None


def test_approval_check_fails_for_stale_formal_approval():
    summary = summarize_approval_check(
        _context(
            head_sha="new-head",
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="old-head"),),
        )
    )

    assert summary.approval_status == "stale"
    assert summary.solo_override.status == "missing"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "formal approval is stale for the current PR head SHA"


def test_approval_check_fails_for_missing_approval():
    summary = summarize_approval_check(_context(reviews=()))

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "missing"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "no formal approval or accepted solo-maintainer override exists for the current PR head SHA"


def test_approval_check_fails_for_comment_only_review_without_override():
    summary = summarize_approval_check(
        _context(
            reviews=(
                ReviewSummary(
                    author="reviewer",
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:00:00Z",
                    commit_oid="head",
                    body="Looks reasonable, but please double-check the docs.",
                ),
            ),
        )
    )

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "missing"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "comment-only review exists without formal approval or an accepted solo-maintainer override"


def test_approval_check_passes_for_valid_solo_maintainer_override():
    summary = summarize_approval_check(
        _context(
            reviews=(
                ReviewSummary(
                    author="reviewer",
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:00:00Z",
                    commit_oid="head",
                    body="Solo-maintainer override accepted. No blockers remain for head.",
                ),
            ),
        )
    )

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "accepted"
    assert summary.satisfied_by == "solo-maintainer-override"
    assert summary.hard_gate_passed is True
    assert summary.blocking_reason is None


def test_approval_check_keeps_valid_override_when_newer_comment_is_not_an_override():
    summary = summarize_approval_check(
        _context(
            reviews=(
                ReviewSummary(
                    author="reviewer",
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:00:00Z",
                    commit_oid="head",
                    body="Solo-maintainer override accepted. No blockers remain for head.",
                ),
                ReviewSummary(
                    author="reviewer",
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:05:00Z",
                    commit_oid="head",
                    body="Follow-up note: verify changelog wording.",
                ),
            ),
        )
    )

    assert summary.solo_override.status == "accepted"
    assert summary.satisfied_by == "solo-maintainer-override"
    assert summary.hard_gate_passed is True


def test_approval_check_ignores_old_override_review_on_previous_head():
    summary = summarize_approval_check(
        _context(
            head_sha="new-head",
            reviews=(
                ReviewSummary(
                    author="reviewer",
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:00:00Z",
                    commit_oid="old-head",
                    body="Solo-maintainer override accepted. No blockers remain for old-head.",
                ),
            ),
        )
    )

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "stale"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "solo-maintainer override was recorded on a previous PR head SHA"
