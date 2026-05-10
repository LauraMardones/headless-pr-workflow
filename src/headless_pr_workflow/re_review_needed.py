"""Re-review gate built on shared PR review policy semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .github import PullRequestContext
from .review_policy import ReviewPolicySummary, summarize_review_policy


@dataclass(frozen=True)
class ReReviewNeededSummary:
    review_policy: ReviewPolicySummary

    @property
    def re_review_needed(self) -> bool:
        return not self.review_policy.hard_gate_passed

    @property
    def hard_gate_passed(self) -> bool:
        return self.review_policy.hard_gate_passed

    def __getattr__(self, name: str) -> Any:
        return getattr(self.review_policy, name)

    def to_dict(self) -> dict[str, Any]:
        policy = self.review_policy
        return {
            "number": policy.number,
            "title": policy.title,
            "url": policy.url,
            "head_ref_oid": policy.head_ref_oid,
            "latest_review_sha": policy.latest_review_sha,
            "latest_review_state": policy.latest_review_state,
            "latest_review_author": policy.latest_review_author,
            "latest_approval_sha": policy.latest_approval_sha,
            "approval_status": policy.approval_status,
            "solo_override": policy.solo_override.to_dict(),
            "approval_source": policy.approval_source,
            "satisfied_by": policy.satisfied_by,
            "re_review_needed": self.re_review_needed,
            "hard_gate_passed": policy.hard_gate_passed,
            "blocking_reason": policy.blocking_reason,
            "blocking_reasons": list(policy.blocking_reasons),
            "reviews": [review.to_dict() for review in policy.reviews],
        }


def summarize_re_review_needed(context: PullRequestContext) -> ReReviewNeededSummary:
    return ReReviewNeededSummary(review_policy=summarize_review_policy(context))
