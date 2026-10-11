"""Approval evaluation helpers for PR review state."""

from __future__ import annotations

from .github import PullRequestContext
from .review_policy import (
    SEPARATE_SESSION_REVIEW_MARKER,
    CodexReviewSummary,
    ReviewPolicySummary,
    SeparateSessionReviewSummary,
    summarize_review_policy,
)


ApprovalCheckSummary = ReviewPolicySummary


def summarize_approval_check(context: PullRequestContext) -> ApprovalCheckSummary:
    return summarize_review_policy(context)
