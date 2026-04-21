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


def test_review_sha_reports_no_reviews():
    summary = summarize_review_sha(_context(head_sha="head", reviews=()))

    assert summary.latest_review_sha is None
    assert summary.latest_approval_sha is None
    assert summary.approval_status == "missing"
