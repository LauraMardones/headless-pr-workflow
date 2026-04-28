from __future__ import annotations

from dataclasses import replace
from typing import Any

from headless_pr_workflow.approval_check import SOLO_OVERRIDE_MARKER
from headless_pr_workflow.github.pr_context import CheckSummary, PullRequestContext, ReviewSummary


def build_review(
    *,
    author: str = "reviewer",
    state: str = "APPROVED",
    submitted_at: str = "2026-04-21T10:00:00Z",
    commit_oid: str | None = "head123",
    body: str | None = None,
) -> ReviewSummary:
    return ReviewSummary(
        author=author,
        state=state,
        submitted_at=submitted_at,
        commit_oid=commit_oid,
        body=body,
    )


def build_check(
    *,
    name: str,
    bucket: str,
    status: str | None = None,
    conclusion: str | None = None,
    state: str | None = None,
    workflow: str | None = None,
    url: str | None = None,
) -> CheckSummary:
    return CheckSummary(
        name=name,
        workflow=workflow,
        status=status,
        conclusion=conclusion,
        state=state,
        bucket=bucket,
        url=url,
    )


def build_pr_context(
    *,
    number: int = 123,
    title: str = "Scenario PR",
    state: str = "OPEN",
    url: str = "https://github.com/owner/repo/pull/123",
    base_ref_name: str = "main",
    base_ref_oid: str | None = "base123",
    head_ref_name: str = "feature/scenario",
    head_ref_oid: str = "head123",
    head_repository: str | None = "owner/repo",
    head_repository_owner: str | None = "owner",
    is_cross_repository: bool = False,
    is_draft: bool = False,
    merge_state_status: str | None = "CLEAN",
    mergeable: str | None = "MERGEABLE",
    review_decision: str | None = None,
    changed_files: int | None = 1,
    additions: int | None = 1,
    deletions: int | None = 0,
    labels: tuple[str, ...] = (),
    latest_reviews: tuple[ReviewSummary, ...] = (),
    review_requests: tuple[str, ...] = (),
    status_checks: tuple[CheckSummary, ...] = (),
    raw: dict[str, Any] | None = None,
) -> PullRequestContext:
    return PullRequestContext(
        number=number,
        title=title,
        state=state,
        url=url,
        base_ref_name=base_ref_name,
        base_ref_oid=base_ref_oid,
        head_ref_name=head_ref_name,
        head_ref_oid=head_ref_oid,
        head_repository=head_repository,
        head_repository_owner=head_repository_owner,
        is_cross_repository=is_cross_repository,
        is_draft=is_draft,
        merge_state_status=merge_state_status,
        mergeable=mergeable,
        review_decision=review_decision,
        changed_files=changed_files,
        additions=additions,
        deletions=deletions,
        labels=labels,
        latest_reviews=latest_reviews,
        review_requests=review_requests,
        status_checks=status_checks,
        raw={} if raw is None else raw,
    )


def with_context(context: PullRequestContext, /, **overrides: Any) -> PullRequestContext:
    return replace(context, **overrides)


def scenario_draft_pr(**overrides: Any) -> PullRequestContext:
    return build_pr_context(is_draft=True, **overrides)


def scenario_current_approval(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(commit_oid=head_sha),),
        **overrides,
    )


def scenario_stale_approval(
    *,
    head_sha: str = "new-head",
    approval_sha: str = "old-head",
    **overrides: Any,
) -> PullRequestContext:
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(commit_oid=approval_sha),),
        **overrides,
    )


def scenario_comment_only_review(
    *,
    head_sha: str = "head123",
    body: str | None = "Looks reasonable, but please double-check the docs.",
    **overrides: Any,
) -> PullRequestContext:
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(state="COMMENTED", commit_oid=head_sha, body=body),),
        **overrides,
    )


def scenario_solo_override(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    body = solo_override_body(head_sha=head_sha)
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(state="COMMENTED", commit_oid=head_sha, body=body),),
        raw={"reviews": [{"body": body, "commit": {"oid": head_sha}}]},
        **overrides,
    )


def scenario_changes_requested(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(head_sha=head_sha, review_decision="CHANGES_REQUESTED", **overrides)


def scenario_failing_checks(
    *,
    head_sha: str = "head123",
    check_name: str = "unit",
    **overrides: Any,
) -> PullRequestContext:
    return scenario_current_approval(
        head_sha=head_sha,
        status_checks=(build_check(name=check_name, bucket="failure", status="COMPLETED", conclusion="FAILURE"),),
        **overrides,
    )


def scenario_pending_checks(
    *,
    head_sha: str = "head123",
    check_name: str = "lint",
    **overrides: Any,
) -> PullRequestContext:
    return scenario_current_approval(
        head_sha=head_sha,
        status_checks=(build_check(name=check_name, bucket="pending", state="PENDING"),),
        **overrides,
    )


def scenario_empty_status_rollup(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(head_sha=head_sha, status_checks=(), **overrides)


def scenario_mergeable_unknown(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(head_sha=head_sha, mergeable="UNKNOWN", **overrides)


def scenario_dirty_merge_state(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(head_sha=head_sha, merge_state_status="DIRTY", **overrides)


def solo_override_body(*, head_sha: str) -> str:
    return (
        f"Reviewed head SHA `{head_sha}`.\n\n"
        f"No blockers remain for {head_sha}.\n\n"
        f"{SOLO_OVERRIDE_MARKER}.\n\n"
        "Formal GitHub approval is unavailable because no independent GitHub approver is available for this pull request.\n\n"
        "This solo-maintainer override is the approval to rely on for the current head SHA.\n"
    )
