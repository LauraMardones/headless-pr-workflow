"""SHA relationship helpers for PR review state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .github import PullRequestContext, ReviewSummary
from .review_policy import summarize_review_policy


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
    policy = summarize_review_policy(context)

    return ReviewShaSummary(
        number=policy.number,
        title=policy.title,
        url=policy.url,
        head_ref_oid=policy.head_ref_oid,
        latest_review_sha=policy.latest_review_sha,
        latest_review_state=policy.latest_review_state,
        latest_review_author=policy.latest_review_author,
        latest_approval_sha=policy.latest_approval_sha,
        approval_status=policy.approval_status,
        hard_gate_passed=policy.hard_gate_passed,
        reviews=policy.reviews,
    )
