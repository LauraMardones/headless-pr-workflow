"""PR takeover context summary command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .approval_check import ApprovalCheckSummary, summarize_approval_check
from .ci_summary import CiSummary, summarize_ci
from .github import PullRequestContext, ReviewThreadGateSummary
from .pre_merge import PreMergeSummary, summarize_pre_merge


_IMPLEMENTATION_FOLLOW_UPS = (
    "hpw ci-summary",
    "hpw unresolved-review-threads",
    "hpw approval-check",
    "hpw re-review-needed",
)

_REVIEW_FOLLOW_UPS = (
    "hpw re-review-needed",
    "hpw review-delta",
    "hpw approval-check",
)

_MERGE_FOLLOW_UPS = (
    "hpw pre-merge",
    "hpw merge-owner",
    "hpw merge-pr",
)

_HUMAN_DECISION_FOLLOW_UPS = (
    "hpw approval-check",
    "hpw ci-summary",
    "hpw unresolved-review-threads",
    "hpw pre-merge",
)


@dataclass(frozen=True)
class TakeoverNextAction:
    action_class: str
    summary: str
    reasons: tuple[str, ...]
    follow_up_commands: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.action_class,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "follow_up_commands": list(self.follow_up_commands),
        }


@dataclass(frozen=True)
class PrTakeoverSummary:
    ok: bool
    repository: str | None
    context: PullRequestContext
    approval: ApprovalCheckSummary
    ci: CiSummary
    review_threads: ReviewThreadGateSummary
    merge_readiness: PreMergeSummary
    next_action: TakeoverNextAction
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        ctx = self.context
        re_review_needed = not self.approval.hard_gate_passed
        return {
            "command": "pr-takeover",
            "ok": self.ok,
            "repository": self.repository,
            "pr": {
                "number": ctx.number,
                "title": ctx.title,
                "url": ctx.url,
                "state": ctx.state,
                "is_draft": ctx.is_draft,
                "base_ref_name": ctx.base_ref_name,
                "head_ref_name": ctx.head_ref_name,
                "head_ref_oid": ctx.head_ref_oid,
                "labels": list(ctx.labels),
                "review_requests": list(ctx.review_requests),
            },
            "approval": {
                "approval_status": self.approval.approval_status,
                "latest_review_sha": self.approval.latest_review_sha,
                "latest_approval_sha": self.approval.latest_approval_sha,
                "solo_override": self.approval.solo_override.to_dict(),
                "approval_source": self.approval.approval_source,
                "satisfied_by": self.approval.satisfied_by,
                "blocking_reasons": list(self.approval.blocking_reasons),
                "hard_gate_passed": self.approval.hard_gate_passed,
            },
            "re_review": {
                "re_review_needed": re_review_needed,
                "hard_gate_passed": self.approval.hard_gate_passed,
                "blocking_reasons": list(self.approval.blocking_reasons),
            },
            "checks": {
                "status_rollup": self.ci.status_rollup,
                "required_check_status": self.ci.required_check_status,
                "check_buckets": {state: list(names) for state, names in self.ci.check_buckets.items()},
                "messages": list(self.ci.messages),
            },
            "review_threads": {
                "thread_counts": self.review_threads.thread_counts,
                "hard_gate_passed": self.review_threads.hard_gate_passed,
            },
            "merge_readiness": {
                "hard_gate_passed": self.merge_readiness.hard_gate_passed,
                "blocking_reasons": list(self.merge_readiness.blocking_reasons),
                "checks": [check.to_dict() for check in self.merge_readiness.checks],
            },
            "next_action": self.next_action.to_dict(),
            "warnings": list(self.warnings),
        }


def summarize_pr_takeover(
    context: PullRequestContext,
    *,
    repository: str | None,
    approval: ApprovalCheckSummary,
    ci: CiSummary,
    review_threads: ReviewThreadGateSummary,
    merge_readiness: PreMergeSummary,
) -> PrTakeoverSummary:
    next_action = _determine_next_action(context, approval, ci, review_threads, merge_readiness)
    warnings = _compute_warnings(context, ci)
    return PrTakeoverSummary(
        ok=True,
        repository=repository,
        context=context,
        approval=approval,
        ci=ci,
        review_threads=review_threads,
        merge_readiness=merge_readiness,
        next_action=next_action,
        warnings=warnings,
    )


def _determine_next_action(
    context: PullRequestContext,
    approval: ApprovalCheckSummary,
    ci: CiSummary,
    review_threads: ReviewThreadGateSummary,
    merge_readiness: PreMergeSummary,
) -> TakeoverNextAction:
    implementation_reasons: list[str] = []

    if context.is_draft:
        implementation_reasons.append("PR is in draft state and must be marked ready before advancing.")

    if context.review_decision == "CHANGES_REQUESTED":
        implementation_reasons.append("GitHub review decision is CHANGES_REQUESTED for the current PR head.")

    unresolved_count = len(review_threads.unresolved_blocking_threads)
    if unresolved_count:
        implementation_reasons.append(
            f"{unresolved_count} active unresolved review thread(s) must be resolved before advancing."
        )

    failing_checks = list(ci.check_buckets.get("failing", ()))
    if failing_checks:
        implementation_reasons.append(f"Failing status check(s): {', '.join(failing_checks)}.")

    if implementation_reasons:
        return TakeoverNextAction(
            action_class="implementation",
            summary="Implementation work is needed before this PR can advance.",
            reasons=tuple(implementation_reasons),
            follow_up_commands=_IMPLEMENTATION_FOLLOW_UPS,
        )

    review_reasons = list(approval.blocking_reasons)
    if review_reasons:
        return TakeoverNextAction(
            action_class="review",
            summary="The current head SHA needs review or re-review before this PR can merge.",
            reasons=tuple(review_reasons),
            follow_up_commands=_REVIEW_FOLLOW_UPS,
        )

    if merge_readiness.hard_gate_passed:
        return TakeoverNextAction(
            action_class="merge",
            summary="All available merge-readiness gates are passing for the current head SHA.",
            reasons=_merge_passing_reasons(approval, ci, review_threads),
            follow_up_commands=_MERGE_FOLLOW_UPS,
        )

    return TakeoverNextAction(
        action_class="human_decision",
        summary="The safe next path is ambiguous or requires human judgment.",
        reasons=tuple(merge_readiness.blocking_reasons),
        follow_up_commands=_HUMAN_DECISION_FOLLOW_UPS,
    )


def _merge_passing_reasons(
    approval: ApprovalCheckSummary,
    ci: CiSummary,
    review_threads: ReviewThreadGateSummary,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if approval.satisfied_by:
        reasons.append(f"Approval is satisfied by {approval.satisfied_by}.")
    else:
        reasons.append("Approval gate is satisfied.")
    if ci.required_checks_satisfied:
        reasons.append("All required status checks are passing.")
    elif ci.required_check_status == "not_configured":
        reasons.append("No required status checks are configured for the target branch.")
    elif ci.required_check_status == "policy_absent":
        reasons.append("Required status checks are absent by repository policy.")
    if not review_threads.unresolved_blocking_threads:
        reasons.append("No active unresolved review threads.")
    return tuple(reasons)


def _compute_warnings(context: PullRequestContext, ci: CiSummary) -> tuple[str, ...]:
    warnings: list[str] = []
    if ci.status_rollup == "empty":
        warnings.append(
            "No status checks were reported for the current head SHA; CI state cannot be confirmed."
        )
    if ci.required_check_status == "unavailable":
        warnings.append(
            "Required status check data is unavailable from branch protection; check coverage is unconfirmed."
        )
    if ci.required_check_status == "not_configured":
        warnings.append("No required status checks are configured for the target branch; CI gate is absent.")
    if context.mergeable == "UNKNOWN":
        warnings.append(
            "GitHub reports PR mergeability as UNKNOWN; mergeability cannot be confirmed without a fresh fetch."
        )
    return tuple(warnings)
