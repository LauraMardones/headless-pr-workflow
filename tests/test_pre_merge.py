from headless_pr_workflow.github.pr_context import CheckSummary, PullRequestContext, ReviewSummary
from headless_pr_workflow.pre_merge import summarize_pre_merge


def _check(*, name: str, bucket: str, status: str | None = None, conclusion: str | None = None, state: str | None = None) -> CheckSummary:
    return CheckSummary(
        name=name,
        workflow=None,
        status=status,
        conclusion=conclusion,
        state=state,
        bucket=bucket,
        url=None,
    )


def _context(
    *,
    state: str = "OPEN",
    is_draft: bool = False,
    head_sha: str = "head123",
    base_ref_name: str = "main",
    base_ref_oid: str | None = "base123",
    mergeable: str | None = "MERGEABLE",
    merge_state_status: str | None = "CLEAN",
    reviews: tuple[ReviewSummary, ...] = (),
    status_checks: tuple[CheckSummary, ...] = (),
) -> PullRequestContext:
    return PullRequestContext(
        number=7,
        title="Pre-merge",
        state=state,
        url="https://github.com/owner/repo/pull/7",
        base_ref_name=base_ref_name,
        base_ref_oid=base_ref_oid,
        head_ref_name="feature/pre-merge",
        head_ref_oid=head_sha,
        head_repository="owner/repo",
        head_repository_owner="owner",
        is_cross_repository=False,
        is_draft=is_draft,
        merge_state_status=merge_state_status,
        mergeable=mergeable,
        review_decision=None,
        changed_files=3,
        additions=20,
        deletions=4,
        labels=(),
        latest_reviews=reviews,
        review_requests=(),
        status_checks=status_checks,
        raw={},
    )


def test_pre_merge_passes_when_all_core_gates_pass():
    summary = summarize_pre_merge(
        _context(
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head123"),),
            status_checks=(_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
        ),
        expected_base_ref_name="main",
    )

    assert summary.blocking_reasons == ()
    assert summary.hard_gate_passed is True
    assert all(check.ok for check in summary.checks)


def test_pre_merge_blocks_draft_pr():
    summary = summarize_pre_merge(
        _context(
            is_draft=True,
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head123"),),
        ),
        expected_base_ref_name="main",
    )

    assert "PR is draft." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_stale_approval():
    summary = summarize_pre_merge(
        _context(
            head_sha="new-head",
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="old-head"),),
        ),
        expected_base_ref_name="main",
    )

    assert "Latest formal approval applies to old-head, not current head new-head." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_failing_and_pending_checks():
    summary = summarize_pre_merge(
        _context(
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head123"),),
            status_checks=(
                _check(name="unit", bucket="failure", status="COMPLETED", conclusion="FAILURE"),
                _check(name="lint", bucket="pending", state="PENDING"),
            ),
        ),
        expected_base_ref_name="main",
    )

    assert "Status check unit is failing (status=COMPLETED, conclusion=FAILURE)." in summary.blocking_reasons
    assert "Status check lint is pending (state=PENDING)." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_non_mergeable_pr():
    summary = summarize_pre_merge(
        _context(
            mergeable="CONFLICTING",
            merge_state_status="DIRTY",
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head123"),),
        ),
        expected_base_ref_name="main",
    )

    assert "PR mergeable state is CONFLICTING." in summary.blocking_reasons
    assert "PR merge state status is DIRTY." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_missing_head_sha():
    summary = summarize_pre_merge(_context(head_sha="", reviews=()), expected_base_ref_name="main")

    assert "Current PR head SHA is unknown." in summary.blocking_reasons
    assert "Current PR head SHA is unknown, so approval cannot be verified." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_unexpected_target_branch():
    summary = summarize_pre_merge(
        _context(
            base_ref_name="release",
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head123"),),
            status_checks=(_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),),
        ),
        expected_base_ref_name="main",
    )

    assert "PR targets base branch release, expected main." in summary.blocking_reasons
    assert summary.hard_gate_passed is False


def test_pre_merge_blocks_when_github_reports_no_status_checks():
    summary = summarize_pre_merge(
        _context(
            reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="head123"),),
        ),
        expected_base_ref_name="main",
    )

    assert "GitHub reported no status checks for the current head SHA." in summary.blocking_reasons
    assert summary.hard_gate_passed is False
