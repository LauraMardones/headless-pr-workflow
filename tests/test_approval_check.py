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
    assert summary.approval_source == "formal"
    assert summary.satisfied_by == "formal-approval"
    assert summary.hard_gate_passed is True
    assert summary.blocking_reason is None
    assert summary.blocking_reasons == ()


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
    assert summary.blocking_reasons == ("formal approval is stale for the current PR head SHA",)


def test_approval_check_fails_for_missing_approval():
    summary = summarize_approval_check(_context(reviews=()))

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "missing"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "no formal approval or accepted solo-maintainer override exists for the current PR head SHA"


def test_approval_check_rejects_unknown_head_sha():
    summary = summarize_approval_check(_context(head_sha="", reviews=()))

    assert summary.approval_status == "unknown-head"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "Current PR head SHA is unknown, so approval cannot be verified."


def test_approval_check_rejects_active_change_request_decision():
    context = _context(
        reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head"),),
    )
    context = PullRequestContext(
        number=context.number,
        title=context.title,
        state=context.state,
        url=context.url,
        base_ref_name=context.base_ref_name,
        base_ref_oid=context.base_ref_oid,
        head_ref_name=context.head_ref_name,
        head_ref_oid=context.head_ref_oid,
        head_repository=context.head_repository,
        head_repository_owner=context.head_repository_owner,
        is_cross_repository=context.is_cross_repository,
        is_draft=context.is_draft,
        merge_state_status=context.merge_state_status,
        mergeable=context.mergeable,
        review_decision="CHANGES_REQUESTED",
        changed_files=context.changed_files,
        additions=context.additions,
        deletions=context.deletions,
        labels=context.labels,
        latest_reviews=context.latest_reviews,
        review_requests=context.review_requests,
        status_checks=context.status_checks,
        raw=context.raw,
    )

    summary = summarize_approval_check(context)

    assert summary.blocking_reason == "GitHub review decision is CHANGES_REQUESTED for the current PR head."
    assert summary.blocking_reasons == ("GitHub review decision is CHANGES_REQUESTED for the current PR head.",)
    assert summary.hard_gate_passed is False


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
                    body=(
                        "Reviewed head SHA `head`.\n\n"
                        "No blockers remain for head.\n\n"
                        "solo-maintainer override accepted.\n\n"
                        "Formal GitHub approval is unavailable because no independent GitHub approver is available for this pull request.\n\n"
                        "This solo-maintainer override is the approval to rely on for the current head SHA.\n"
                    ),
                ),
            ),
        )
    )

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "accepted"
    assert summary.approval_source == "solo-maintainer-override"
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
                    body=(
                        "Reviewed head SHA `head`.\n\n"
                        "No blockers remain for head.\n\n"
                        "solo-maintainer override accepted.\n\n"
                        "Formal GitHub approval is unavailable because no independent GitHub approver is available for this pull request.\n\n"
                        "This solo-maintainer override is the approval to rely on for the current head SHA.\n"
                    ),
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


def test_approval_check_rejects_older_override_after_newer_same_head_revocation():
    summary = summarize_approval_check(
        _context(
            reviews=(
                ReviewSummary(
                    author="reviewer",
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:00:00Z",
                    commit_oid="head",
                    body=(
                        "Reviewed head SHA `head`.\n\n"
                        "No blockers remain for head.\n\n"
                        "solo-maintainer override accepted.\n\n"
                        "Formal GitHub approval is unavailable because no independent GitHub approver is available for this pull request.\n\n"
                        "This solo-maintainer override is the approval to rely on for the current head SHA.\n"
                    ),
                ),
                ReviewSummary(
                    author="reviewer",
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:05:00Z",
                    commit_oid="head",
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
        _context(
            head_sha="new-head",
            reviews=(
                ReviewSummary(
                    author="reviewer",
                    state="COMMENTED",
                    submitted_at="2026-04-21T10:00:00Z",
                    commit_oid="old-head",
                    body=(
                        "Reviewed head SHA `old-head`.\n\n"
                        "No blockers remain for old-head.\n\n"
                        "solo-maintainer override accepted.\n\n"
                        "Formal GitHub approval is unavailable because no independent GitHub approver is available for this pull request.\n\n"
                        "This solo-maintainer override is the approval to rely on for the current head SHA.\n"
                    ),
                ),
            ),
        )
    )

    assert summary.approval_status == "missing"
    assert summary.solo_override.status == "stale"
    assert summary.hard_gate_passed is False
    assert summary.blocking_reason == "solo-maintainer override was recorded on a previous PR head SHA"
