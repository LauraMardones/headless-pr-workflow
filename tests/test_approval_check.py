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


def test_approval_check_accepts_current_formal_approval():
    summary = summarize_approval_check(
        _context(
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head"),),
        )
    )

    assert summary.approval_status == "current"
    assert summary.approval_source == "formal"
    assert summary.blocking_reasons == ()
    assert summary.hard_gate_passed is True


def test_approval_check_rejects_stale_approval():
    summary = summarize_approval_check(
        _context(
            head_sha="new-head",
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="old-head"),),
        )
    )

    assert summary.approval_status == "stale"
    assert summary.approval_source is None
    assert summary.blocking_reasons == ("Latest formal approval applies to old-head, not current head new-head.",)
    assert summary.hard_gate_passed is False


def test_approval_check_rejects_unknown_head_sha():
    summary = summarize_approval_check(_context(head_sha="", reviews=()))

    assert summary.approval_status == "unknown-head"
    assert summary.blocking_reasons == ("Current PR head SHA is unknown, so approval cannot be verified.",)
    assert summary.hard_gate_passed is False
