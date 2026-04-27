"""SHA relationship helpers for PR review state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .github import PullRequestContext, ReviewSummary


@dataclass(frozen=True)
class ReviewShaSummary:
    number: int
    title: str
    url: str
    head_ref_oid: str
    latest_review_sha: str | None
    latest_review_state: str | None
    latest_review_author: str | None
    latest_approval_sha: str | None
    approval_status: str
    hard_gate_passed: bool
    reviews: tuple[ReviewSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reviews"] = [review.to_dict() for review in self.reviews]
        return payload


def summarize_review_sha(context: PullRequestContext) -> ReviewShaSummary:
    latest_review = _latest_review_with_sha(context)
    latest_approval_sha = context.latest_approval_sha

    return ReviewShaSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        head_ref_oid=context.head_ref_oid,
        latest_review_sha=latest_review.commit_oid if latest_review else None,
        latest_review_state=latest_review.state if latest_review else None,
        latest_review_author=latest_review.author if latest_review else None,
        latest_approval_sha=latest_approval_sha,
        approval_status=_approval_status(context.head_ref_oid, latest_approval_sha),
        hard_gate_passed=latest_approval_sha == context.head_ref_oid,
        reviews=context.latest_reviews,
    )


def _latest_review_with_sha(context: PullRequestContext) -> ReviewSummary | None:
    for review in reversed(context.latest_reviews):
        if review.commit_oid:
            return review
    return None


def _approval_status(head_sha: str, approval_sha: str | None) -> str:
    if not approval_sha:
        return "missing"
    if approval_sha == head_sha:
        return "current"
    return "stale"
