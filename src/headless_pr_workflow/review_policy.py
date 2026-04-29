"""Shared hard-gate policy for PR review and approval semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .github import PullRequestContext, ReviewSummary


SOLO_OVERRIDE_MARKER = "solo-maintainer override accepted"


@dataclass(frozen=True)
class SoloMaintainerOverrideSummary:
    status: str
    review_author: str | None
    review_state: str | None
    review_commit_oid: str | None
    review_submitted_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "review_author": self.review_author,
            "review_state": self.review_state,
            "review_commit_oid": self.review_commit_oid,
            "review_submitted_at": self.review_submitted_at,
        }


@dataclass(frozen=True)
class ReviewPolicySummary:
    number: int
    title: str
    url: str
    head_ref_oid: str
    latest_review_sha: str | None
    latest_review_state: str | None
    latest_review_author: str | None
    latest_approval_sha: str | None
    approval_status: str
    solo_override: SoloMaintainerOverrideSummary
    approval_source: str | None
    satisfied_by: str | None
    hard_gate_passed: bool
    blocking_reason: str | None
    blocking_reasons: tuple[str, ...]
    reviews: tuple[ReviewSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "head_ref_oid": self.head_ref_oid,
            "latest_review_sha": self.latest_review_sha,
            "latest_review_state": self.latest_review_state,
            "latest_review_author": self.latest_review_author,
            "latest_approval_sha": self.latest_approval_sha,
            "approval_status": self.approval_status,
            "solo_override": self.solo_override.to_dict(),
            "approval_source": self.approval_source,
            "satisfied_by": self.satisfied_by,
            "hard_gate_passed": self.hard_gate_passed,
            "blocking_reason": self.blocking_reason,
            "blocking_reasons": list(self.blocking_reasons),
            "reviews": [review.to_dict() for review in self.reviews],
        }


def summarize_review_policy(context: PullRequestContext) -> ReviewPolicySummary:
    latest_review = _latest_review_with_sha(context)
    latest_approval_sha = context.latest_approval_sha
    approval_status = _approval_status(context.head_ref_oid, latest_approval_sha)
    solo_override = evaluate_solo_maintainer_override(context)

    approval_source: str | None = None
    satisfied_by: str | None = None
    blocking_reason: str | None = None

    if context.review_decision == "CHANGES_REQUESTED":
        blocking_reason = "GitHub review decision is CHANGES_REQUESTED for the current PR head."
    elif not context.head_ref_oid:
        blocking_reason = "Current PR head SHA is unknown, so approval cannot be verified."
    elif approval_status == "current":
        approval_source = "formal"
        satisfied_by = "formal-approval"
    elif solo_override.status == "accepted":
        approval_source = "solo-maintainer-override"
        satisfied_by = "solo-maintainer-override"
    else:
        blocking_reason = _blocking_reason(
            approval_status,
            solo_override.status,
            latest_review.state if latest_review else None,
        )

    blocking_reasons = () if blocking_reason is None else (blocking_reason,)

    return ReviewPolicySummary(
        number=context.number,
        title=context.title,
        url=context.url,
        head_ref_oid=context.head_ref_oid,
        latest_review_sha=latest_review.commit_oid if latest_review else None,
        latest_review_state=latest_review.state if latest_review else None,
        latest_review_author=latest_review.author if latest_review else None,
        latest_approval_sha=latest_approval_sha,
        approval_status=approval_status,
        solo_override=solo_override,
        approval_source=approval_source,
        satisfied_by=satisfied_by,
        hard_gate_passed=blocking_reason is None,
        blocking_reason=blocking_reason,
        blocking_reasons=blocking_reasons,
        reviews=context.latest_reviews,
    )


def evaluate_solo_maintainer_override(context: PullRequestContext) -> SoloMaintainerOverrideSummary:
    stale_review: ReviewSummary | None = None

    for review in reversed(context.latest_reviews):
        if review.state != "COMMENTED":
            continue

        body = (review.body or "").strip()
        if not body:
            continue

        if review.commit_oid == context.head_ref_oid and _looks_like_override(body):
            return SoloMaintainerOverrideSummary(
                status="accepted" if _is_accepted_override(review, head_sha=context.head_ref_oid) else "invalid",
                review_author=review.author,
                review_state=review.state,
                review_commit_oid=review.commit_oid,
                review_submitted_at=review.submitted_at,
            )

        if review.commit_oid == context.head_ref_oid:
            continue

        if stale_review is None and _looks_like_override(body):
            stale_review = review

    if stale_review is not None:
        return SoloMaintainerOverrideSummary(
            status="stale",
            review_author=stale_review.author,
            review_state=stale_review.state,
            review_commit_oid=stale_review.commit_oid,
            review_submitted_at=stale_review.submitted_at,
        )

    return SoloMaintainerOverrideSummary(
        status="missing",
        review_author=None,
        review_state=None,
        review_commit_oid=None,
        review_submitted_at=None,
    )


def _latest_review_with_sha(context: PullRequestContext) -> ReviewSummary | None:
    for review in reversed(context.latest_reviews):
        if review.commit_oid:
            return review
    return None


def _approval_status(head_sha: str, approval_sha: str | None) -> str:
    if not head_sha:
        return "unknown-head"
    if not approval_sha:
        return "missing"
    if approval_sha == head_sha:
        return "current"
    return "stale"


def _looks_like_override(body: str) -> bool:
    return "solo-maintainer override" in body.lower()


def _is_accepted_override(review: ReviewSummary, *, head_sha: str) -> bool:
    if review.state != "COMMENTED" or review.commit_oid != head_sha:
        return False

    body = (review.body or "").lower()
    return (
        SOLO_OVERRIDE_MARKER in body
        and f"no blockers remain for {head_sha.lower()}" in body
        and "approval to rely on for the current head sha" in body
        and "no independent github approver is available" in body
    )


def _blocking_reason(approval_status: str, override_status: str, latest_review_state: str | None) -> str:
    if override_status == "invalid":
        return "current-head review comment does not contain an accepted solo-maintainer override"
    if override_status == "stale":
        return "solo-maintainer override was recorded on a previous PR head SHA"
    if approval_status == "stale":
        return "formal approval is stale for the current PR head SHA"
    if latest_review_state == "COMMENTED":
        return "comment-only review exists without formal approval or an accepted solo-maintainer override"
    return "no formal approval or accepted solo-maintainer override exists for the current PR head SHA"
