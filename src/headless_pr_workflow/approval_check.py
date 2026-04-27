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
    blocking_reasons = _blocking_reasons(context)
    approval_source = "formal" if review_sha_summary.hard_gate_passed else None

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


def _blocking_reasons(context: PullRequestContext) -> tuple[str, ...]:
    if not context.head_ref_oid:
        return ("Current PR head SHA is unknown, so approval cannot be verified.",)

    latest_approval_sha = context.latest_approval_sha
    if not latest_approval_sha:
        return ("No formal approval applies to the current head SHA.",)
    if latest_approval_sha != context.head_ref_oid:
        return (
            "Latest formal approval applies to "
            f"{latest_approval_sha}, not current head {context.head_ref_oid}.",
        )
    return ()
