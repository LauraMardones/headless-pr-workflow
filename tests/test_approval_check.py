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

    assert summary.blocking_reasons == ("GitHub review decision is CHANGES_REQUESTED for the current PR head.",)
    assert summary.hard_gate_passed is False


def test_approval_check_accepts_solo_maintainer_override_on_current_head():
    summary = summarize_approval_check(
        _context(
            reviews=(ReviewSummary(author="reviewer", state="COMMENTED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head"),),
        )
    )
    context = _context(
        reviews=(ReviewSummary(author="reviewer", state="COMMENTED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head"),),
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
        review_decision=context.review_decision,
        changed_files=context.changed_files,
        additions=context.additions,
        deletions=context.deletions,
        labels=context.labels,
        latest_reviews=context.latest_reviews,
        review_requests=context.review_requests,
        status_checks=context.status_checks,
        raw={
            "reviews": [
                {
                    "body": (
                        "Reviewed head SHA `head`.\n\n"
                        "No blockers remain for head.\n\n"
                        "solo-maintainer override accepted.\n\n"
                        "Formal GitHub approval is unavailable because no independent GitHub approver is available for this pull request.\n\n"
                        "This solo-maintainer override is the approval to rely on for the current head SHA.\n"
                    ),
                    "commit": {"oid": "head"},
                }
            ]
        },
    )

    summary = summarize_approval_check(context)

    assert summary.approval_status == "missing"
    assert summary.approval_source == "solo-maintainer-override"
    assert summary.blocking_reasons == ()
    assert summary.hard_gate_passed is True
