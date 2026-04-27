from headless_pr_workflow.github.pr_context import PullRequestContext, ReviewSummary
from headless_pr_workflow.review_sha import summarize_review_sha


def _context(*, head_sha: str = "head", reviews: tuple[ReviewSummary, ...] = ()) -> PullRequestContext:
    return PullRequestContext(
        number=123,
        title="Review SHA",
        state="OPEN",
        url="https://github.com/owner/repo/pull/123",
        base_ref_name="main",
        base_ref_oid="base",
        head_ref_name="feature/review-sha",
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


def test_review_sha_reports_current_approval():
    summary = summarize_review_sha(
        _context(
            head_sha="head",
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head"),),
        )
    )

    assert summary.head_ref_oid == "head"
    assert summary.latest_review_sha == "head"
    assert summary.latest_approval_sha == "head"
    assert summary.approval_status == "current"
    assert summary.hard_gate_passed is True


def test_review_sha_reports_stale_approval():
    summary = summarize_review_sha(
        _context(
            head_sha="new-head",
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="old-head"),),
        )
    )

    assert summary.latest_review_sha == "old-head"
    assert summary.latest_approval_sha == "old-head"
    assert summary.approval_status == "stale"
    assert summary.hard_gate_passed is False


def test_review_sha_reports_missing_approval_with_comment_only_review():
    summary = summarize_review_sha(
        _context(
            head_sha="head",
            reviews=(ReviewSummary(author="reviewer", state="COMMENTED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head"),),
        )
    )

    assert summary.latest_review_sha == "head"
    assert summary.latest_review_state == "COMMENTED"
    assert summary.latest_approval_sha is None
    assert summary.approval_status == "missing"
    assert summary.hard_gate_passed is False


def test_review_sha_reports_no_reviews():
    summary = summarize_review_sha(_context(head_sha="head", reviews=()))

    assert summary.latest_review_sha is None
    assert summary.latest_approval_sha is None
    assert summary.approval_status == "missing"
    assert summary.hard_gate_passed is False


def test_review_sha_blocks_current_approval_when_changes_requested():
    context = _context(
        head_sha="head",
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

    summary = summarize_review_sha(context)

    assert summary.approval_status == "current"
    assert summary.hard_gate_passed is False
