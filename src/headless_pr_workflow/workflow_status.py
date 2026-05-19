"""Workflow status summary command."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .approval_check import ApprovalCheckSummary, summarize_approval_check
from .ci_summary import CiSummary, summarize_ci
from .github import PullRequestContext, ReviewThreadGateSummary
from .pre_merge import PreMergeSummary, summarize_pre_merge
from .re_review_needed import ReReviewNeededSummary, summarize_re_review_needed
from .worktree_status import WorktreeStatusSummary


@dataclass(frozen=True)
class WorkflowPosture:
    status: str
    summary: str
    reasons: tuple[str, ...]
    source_commands: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "source_commands": list(self.source_commands),
        }


@dataclass(frozen=True)
class WorkflowStatusSummary:
    ok: bool
    repository: str | None
    context: PullRequestContext
    approval: ApprovalCheckSummary
    re_review: ReReviewNeededSummary
    ci: CiSummary
    review_threads: ReviewThreadGateSummary
    merge_readiness: PreMergeSummary
    local_state: WorktreeStatusSummary
    workflow_posture: WorkflowPosture
    warnings: tuple[str, ...]
    errors: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        ctx = self.context
        return {
            "command": "workflow-status",
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
            "github_truth": {
                "state": ctx.state,
                "is_draft": ctx.is_draft,
                "base_ref_name": ctx.base_ref_name,
                "head_ref_name": ctx.head_ref_name,
                "head_ref_oid": ctx.head_ref_oid,
                "mergeable": ctx.mergeable,
                "merge_state_status": ctx.merge_state_status,
                "review_decision": ctx.review_decision,
            },
            "local_state": self.local_state.to_dict(),
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
                "re_review_needed": self.re_review.re_review_needed,
                "hard_gate_passed": self.re_review.hard_gate_passed,
                "blocking_reasons": list(self.re_review.blocking_reasons),
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
                "unresolved_blocking_threads": [t.to_dict() for t in self.review_threads.unresolved_blocking_threads],
            },
            "merge_readiness": {
                "hard_gate_passed": self.merge_readiness.hard_gate_passed,
                "mergeable": ctx.mergeable,
                "merge_state_status": ctx.merge_state_status,
                "blocking_reasons": list(self.merge_readiness.blocking_reasons),
                "checks": [check.to_dict() for check in self.merge_readiness.checks],
            },
            "workflow_posture": self.workflow_posture.to_dict(),
            "warnings": list(self.warnings),
            "errors": self.errors,
        }


def summarize_workflow_status(
    context: PullRequestContext,
    *,
    repository: str | None,
    approval: ApprovalCheckSummary,
    re_review: ReReviewNeededSummary,
    ci: CiSummary,
    review_threads: ReviewThreadGateSummary,
    merge_readiness: PreMergeSummary,
    local_state: WorktreeStatusSummary,
) -> WorkflowStatusSummary:
    if _is_merged_pr(context):
        merge_readiness = _terminal_merged_merge_readiness(merge_readiness)
    workflow_posture = _determine_workflow_posture(context, approval, ci, review_threads, merge_readiness)
    warnings = _compute_warnings(context, ci, local_state)
    return WorkflowStatusSummary(
        ok=True,
        repository=repository,
        context=context,
        approval=approval,
        re_review=re_review,
        ci=ci,
        review_threads=review_threads,
        merge_readiness=merge_readiness,
        local_state=local_state,
        workflow_posture=workflow_posture,
        warnings=warnings,
        errors=None,
    )


def _determine_workflow_posture(
    context: PullRequestContext,
    approval: ApprovalCheckSummary,
    ci: CiSummary,
    review_threads: ReviewThreadGateSummary,
    merge_readiness: PreMergeSummary,
) -> WorkflowPosture:
    if _is_merged_pr(context):
        return WorkflowPosture(
            status="merged",
            summary="PR is merged. No further merge action required. Run post-merge-sync to update local state.",
            reasons=(),
            source_commands=("workflow-status",),
        )

    implementation_reasons: list[str] = []

    if context.is_draft:
        implementation_reasons.append("PR is in draft state and must be marked ready before advancing.")

    if context.review_decision == "CHANGES_REQUESTED":
        implementation_reasons.append("GitHub review decision is CHANGES_REQUESTED for the current PR head.")

    unresolved = len(review_threads.unresolved_blocking_threads)
    if unresolved:
        implementation_reasons.append(
            f"{unresolved} active unresolved review thread(s) must be resolved before advancing."
        )

    failing = list(ci.check_buckets.get("failing", ()))
    if failing:
        implementation_reasons.append(f"Failing status check(s): {', '.join(failing)}.")

    if implementation_reasons:
        return WorkflowPosture(
            status="implementation_required",
            summary="Implementation work is needed before this PR can advance.",
            reasons=tuple(implementation_reasons),
            source_commands=("ci-summary", "unresolved-review-threads", "approval-check"),
        )

    if not approval.hard_gate_passed:
        return WorkflowPosture(
            status="review_required",
            summary="The current head SHA requires review or re-review before this PR can merge.",
            reasons=tuple(approval.blocking_reasons),
            source_commands=("approval-check", "re-review-needed"),
        )

    pending = list(ci.check_buckets.get("pending", ())) + list(ci.check_buckets.get("unknown", ()))
    if pending and not merge_readiness.hard_gate_passed:
        return WorkflowPosture(
            status="waiting",
            summary="Status checks are still pending or in an unknown state.",
            reasons=(f"Pending/unknown check(s): {', '.join(pending)}.",),
            source_commands=("ci-summary",),
        )

    if merge_readiness.hard_gate_passed:
        return WorkflowPosture(
            status="merge_validation_required",
            summary="All available merge-readiness gates are passing for the current head SHA.",
            reasons=_merge_passing_reasons(approval, ci, review_threads),
            source_commands=("pre-merge", "approval-check", "ci-summary", "unresolved-review-threads"),
        )

    return WorkflowPosture(
        status="human_decision_required",
        summary="The safe next path is ambiguous or requires human judgment.",
        reasons=tuple(merge_readiness.blocking_reasons)
        or ("Merge-readiness gates did not pass; manual evaluation is required.",),
        source_commands=("pre-merge",),
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


def _is_merged_pr(context: PullRequestContext) -> bool:
    return context.state == "MERGED"


def _terminal_merged_merge_readiness(merge_readiness: PreMergeSummary) -> PreMergeSummary:
    return replace(
        merge_readiness,
        checks=tuple(check for check in merge_readiness.checks if check.code != "mergeability"),
        blocking_reasons=(),
        hard_gate_passed=True,
    )


def _compute_warnings(
    context: PullRequestContext,
    ci: CiSummary,
    local_state: WorktreeStatusSummary,
) -> tuple[str, ...]:
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
    if context.mergeable == "UNKNOWN" and not _is_merged_pr(context):
        warnings.append(
            "GitHub reports PR mergeability as UNKNOWN; mergeability cannot be confirmed without a fresh fetch."
        )

    if local_state.ok:
        if not local_state.status.clean:
            staged = len(local_state.status.staged)
            unstaged = len(local_state.status.unstaged)
            warnings.append(
                f"Local worktree has uncommitted changes: {staged} staged, {unstaged} unstaged tracked."
            )
        if local_state.status.conflicted:
            count = len(local_state.status.conflicted)
            warnings.append(f"Local worktree has {count} conflicted path(s).")
        if local_state.unpushed_commits:
            count = len(local_state.unpushed_commits)
            warnings.append(f"Local worktree has {count} unpushed commit(s).")
        if local_state.branch_in_use_by_other_worktree is True:
            warnings.append("Current branch is checked out by another linked worktree.")
        warnings.extend(local_state.warnings)
    else:
        warnings.append("Local worktree state could not be fully inspected.")

    return tuple(warnings)
