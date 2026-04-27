"""Approval applicability helpers for PR review state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .github import PullRequestContext
from .review_sha import summarize_review_sha


@dataclass(frozen=True)
class ApprovalCheckSummary:
    number: int
    title: str
    url: str
    head_ref_oid: str
    latest_review_sha: str | None
    latest_review_state: str | None
    latest_review_author: str | None
    latest_approval_sha: str | None
    approval_status: str
    approval_source: str | None
    blocking_reasons: tuple[str, ...]
    hard_gate_passed: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blocking_reasons"] = list(self.blocking_reasons)
        return payload


def summarize_approval_check(context: PullRequestContext) -> ApprovalCheckSummary:
    review_sha_summary = summarize_review_sha(context)
    solo_override = _solo_maintainer_override_applies(context)
    blocking_reasons = _blocking_reasons(context, solo_override=solo_override)
    approval_source = None
    if review_sha_summary.hard_gate_passed:
        approval_source = "formal"
    elif solo_override:
        approval_source = "solo-maintainer-override"

    return ApprovalCheckSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        head_ref_oid=context.head_ref_oid,
        latest_review_sha=review_sha_summary.latest_review_sha,
        latest_review_state=review_sha_summary.latest_review_state,
        latest_review_author=review_sha_summary.latest_review_author,
        latest_approval_sha=review_sha_summary.latest_approval_sha,
        approval_status=review_sha_summary.approval_status,
        approval_source=approval_source,
        blocking_reasons=blocking_reasons,
        hard_gate_passed=not blocking_reasons,
    )


def _blocking_reasons(context: PullRequestContext, *, solo_override: bool) -> tuple[str, ...]:
    if context.review_decision == "CHANGES_REQUESTED":
        return ("GitHub review decision is CHANGES_REQUESTED for the current PR head.",)
    if not context.head_ref_oid:
        return ("Current PR head SHA is unknown, so approval cannot be verified.",)
    if solo_override:
        return ()

    latest_approval_sha = context.latest_approval_sha
    if not latest_approval_sha:
        return ("No formal approval applies to the current head SHA.",)
    if latest_approval_sha != context.head_ref_oid:
        return (
            "Latest formal approval applies to "
            f"{latest_approval_sha}, not current head {context.head_ref_oid}.",
        )
    return ()


def _solo_maintainer_override_applies(context: PullRequestContext) -> bool:
    if context.is_draft or not context.head_ref_oid:
        return False

    for review in reversed(_raw_reviews(context)):
        if _review_commit_oid(review) != context.head_ref_oid:
            continue
        body = (review.get("body") or "").lower()
        if not body:
            continue
        if "solo-maintainer override accepted" not in body:
            continue
        if "no blockers remain" not in body:
            continue
        if "approval to rely on for the current head sha" not in body:
            continue
        if "no independent github approver is available" not in body:
            continue
        return True
    return False


def _raw_reviews(context: PullRequestContext) -> tuple[dict[str, Any], ...]:
    raw_reviews = context.raw.get("reviews")
    if not isinstance(raw_reviews, list):
        return ()
    return tuple(review for review in raw_reviews if isinstance(review, dict))


def _review_commit_oid(review: dict[str, Any]) -> str | None:
    commit = review.get("commit")
    if not isinstance(commit, dict):
        return None
    oid = commit.get("oid")
    return oid if isinstance(oid, str) else None
