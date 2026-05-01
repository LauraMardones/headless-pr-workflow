"""Composed merge-readiness evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .approval_check import ApprovalCheckSummary, summarize_approval_check
from .ci_summary import CiSummary, summarize_ci
from .github import CheckSummary, PullRequestContext, RequiredStatusChecks, ReviewThreadGateSummary
from .github.review_threads import summarize_review_threads
from .target_branch import TargetBranchSummary, summarize_target_branch, target_branch_message


ACCEPTABLE_MERGEABLE = {"MERGEABLE"}
ACCEPTABLE_MERGE_STATE_STATUS = {"CLEAN", "HAS_HOOKS"}


@dataclass(frozen=True)
class PreMergeCheck:
    code: str
    ok: bool
    message: str
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "ok": self.ok,
            "message": self.message,
            "details": list(self.details),
        }


@dataclass(frozen=True)
class PreMergeSummary:
    number: int
    title: str
    url: str
    state: str
    is_draft: bool
    expected_base_ref_name: str | None
    base_ref_name: str
    base_ref_oid: str | None
    head_ref_name: str
    head_ref_oid: str
    mergeable: str | None
    merge_state_status: str | None
    approval: ApprovalCheckSummary
    target_branch: TargetBranchSummary
    ci: CiSummary
    review_threads: ReviewThreadGateSummary
    required_check_names: tuple[str, ...]
    status_checks: tuple[CheckSummary, ...]
    checks: tuple[PreMergeCheck, ...]
    blocking_reasons: tuple[str, ...]
    hard_gate_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "state": self.state,
            "is_draft": self.is_draft,
            "expected_base_ref_name": self.expected_base_ref_name,
            "base_ref_name": self.base_ref_name,
            "base_ref_oid": self.base_ref_oid,
            "head_ref_name": self.head_ref_name,
            "head_ref_oid": self.head_ref_oid,
            "mergeable": self.mergeable,
            "merge_state_status": self.merge_state_status,
            "current_head_sha": self.head_ref_oid,
            "pr": {
                "number": self.number,
                "title": self.title,
                "url": self.url,
                "state": self.state,
                "is_draft": self.is_draft,
                "base_ref_name": self.base_ref_name,
                "base_ref_oid": self.base_ref_oid,
                "head_ref_name": self.head_ref_name,
                "head_ref_oid": self.head_ref_oid,
            },
            "approval": self.approval.to_dict(),
            "approval_review_source": {
                "approval_source": self.approval.approval_source,
                "satisfied_by": self.approval.satisfied_by,
                "approval_status": self.approval.approval_status,
                "latest_review_sha": self.approval.latest_review_sha,
                "latest_review_state": self.approval.latest_review_state,
                "latest_review_author": self.approval.latest_review_author,
                "latest_approval_sha": self.approval.latest_approval_sha,
                "solo_override": self.approval.solo_override.to_dict(),
            },
            "target_branch_comparison": self.target_branch.to_dict(),
            "required_check_summary": self.ci.to_dict(),
            "mergeability_facts": {
                "mergeable": self.mergeable,
                "merge_state_status": self.merge_state_status,
                "acceptable_mergeable": sorted(ACCEPTABLE_MERGEABLE),
                "acceptable_merge_state_status": sorted(ACCEPTABLE_MERGE_STATE_STATUS),
            },
            "unresolved_thread_summary": self.review_threads.to_dict(),
            "required_check_names": list(self.required_check_names),
            "status_checks": [check.to_dict() for check in self.status_checks],
            "check_counts": _check_counts(self.status_checks),
            "checks": [check.to_dict() for check in self.checks],
            "blocking_reasons": list(self.blocking_reasons),
            "hard_gate_passed": self.hard_gate_passed,
        }


def summarize_pre_merge(
    context: PullRequestContext,
    *,
    expected_base_ref_name: str | None,
    required_check_names: tuple[str, ...] = (),
    required_checks: RequiredStatusChecks | None = None,
    review_threads: ReviewThreadGateSummary | None = None,
) -> PreMergeSummary:
    approval = summarize_approval_check(context)
    required_check_context = required_checks or _required_checks_from_names(required_check_names)
    ci = summarize_ci(context, required_checks=required_check_context)
    target_branch = summarize_target_branch(context, expected_base_ref_name=expected_base_ref_name)
    thread_summary = review_threads or summarize_review_threads(context, ())
    checks: list[PreMergeCheck] = []
    blocking_reasons: list[str] = []

    _append_simple_check(
        checks,
        blocking_reasons,
        code="pr-open",
        ok=context.state == "OPEN",
        pass_message="PR is open.",
        fail_message=f"PR is not open (state={context.state or 'unknown'}).",
    )
    _append_simple_check(
        checks,
        blocking_reasons,
        code="not-draft",
        ok=not context.is_draft,
        pass_message="PR is not draft.",
        fail_message="PR is draft.",
    )
    _append_simple_check(
        checks,
        blocking_reasons,
        code="head-sha-known",
        ok=bool(context.head_ref_oid),
        pass_message=f"Current head SHA is {context.head_ref_oid}.",
        fail_message="Current PR head SHA is unknown.",
    )

    approval_ok = approval.hard_gate_passed
    approval_message = "Approval applies to the current head SHA." if approval_ok else "Approval does not apply to the current head SHA."
    checks.append(
        PreMergeCheck(
            code="approval-current-head",
            ok=approval_ok,
            message=approval_message,
            details=approval.blocking_reasons,
        )
    )
    if not approval_ok:
        blocking_reasons.extend(approval.blocking_reasons)

    checks.append(
        PreMergeCheck(
            code="target-branch-expected",
            ok=target_branch.hard_gate_passed,
            message=target_branch_message(target_branch),
            details=target_branch.blocking_reasons,
        )
    )
    blocking_reasons.extend(target_branch.blocking_reasons)

    check_blockers = _status_check_blockers(ci)
    checks.append(
        PreMergeCheck(
            code="required-checks-passing",
            ok=not check_blockers,
            message=_status_check_message(ci, check_blockers),
            details=tuple(check_blockers),
        )
    )
    blocking_reasons.extend(check_blockers)

    mergeability_blockers = _mergeability_blockers(context)
    checks.append(
        PreMergeCheck(
            code="mergeability",
            ok=not mergeability_blockers,
            message=_mergeability_message(context, mergeability_blockers),
            details=tuple(mergeability_blockers),
        )
    )
    blocking_reasons.extend(mergeability_blockers)

    thread_blockers = _review_thread_blockers(thread_summary)
    checks.append(
        PreMergeCheck(
            code="unresolved-review-threads",
            ok=not thread_blockers,
            message=_review_thread_message(thread_summary, thread_blockers),
            details=tuple(thread_blockers),
        )
    )
    blocking_reasons.extend(thread_blockers)

    return PreMergeSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        state=context.state,
        is_draft=context.is_draft,
        expected_base_ref_name=expected_base_ref_name,
        base_ref_name=context.base_ref_name,
        base_ref_oid=context.base_ref_oid,
        head_ref_name=context.head_ref_name,
        head_ref_oid=context.head_ref_oid,
        mergeable=context.mergeable,
        merge_state_status=context.merge_state_status,
        approval=approval,
        target_branch=target_branch,
        ci=ci,
        review_threads=thread_summary,
        required_check_names=required_check_context.names,
        status_checks=context.status_checks,
        checks=tuple(checks),
        blocking_reasons=tuple(blocking_reasons),
        hard_gate_passed=not blocking_reasons,
    )


def _append_simple_check(
    checks: list[PreMergeCheck],
    blocking_reasons: list[str],
    *,
    code: str,
    ok: bool,
    pass_message: str,
    fail_message: str,
) -> None:
    message = pass_message if ok else fail_message
    checks.append(PreMergeCheck(code=code, ok=ok, message=message))
    if not ok:
        blocking_reasons.append(message)


def _status_check_message(
    ci: CiSummary,
    blockers: list[str],
) -> str:
    if blockers:
        return "Required status checks are not yet merge-ready."
    if ci.required_check_status == "policy_absent":
        detail = f" Source: {ci.required_checks.source}." if ci.required_checks.source else ""
        return f"Required status checks are absent by repository policy.{detail}"
    if not ci.status_checks and not ci.required_checks.names:
        return "GitHub reports no required status checks for the target branch."
    return "All reported status checks are passing or skipped."


def _status_check_blockers(ci: CiSummary) -> list[str]:
    if ci.required_check_status == "unavailable":
        return list(ci.messages)
    if not ci.status_checks and ci.required_checks.names:
        return ["GitHub reported no status checks for the current head SHA."]
    blockers: list[str] = []
    for required_check_name in ci.check_buckets["missing"]:
        blockers.append(f"Required status check {required_check_name} was not reported for the current head SHA.")
    for check in ci.status_checks:
        if check.bucket == "success" or check.bucket == "skipped":
            continue
        name = _check_name(check)
        facts = _check_facts(check)
        if check.bucket == "failure":
            blockers.append(f"Status check {name} is failing ({facts}).")
        elif check.bucket == "pending":
            blockers.append(f"Status check {name} is pending ({facts}).")
        else:
            blockers.append(f"Status check {name} has an unknown result ({facts}).")
    return blockers


def _review_thread_message(summary: ReviewThreadGateSummary, blockers: list[str]) -> str:
    if blockers:
        return "Active unresolved review threads block merge readiness."
    if not summary.threads:
        return "No review threads found."
    return "No active unresolved review threads remain."


def _review_thread_blockers(summary: ReviewThreadGateSummary) -> list[str]:
    return [
        f"Unresolved review thread {_thread_label(thread)}: {thread.reason}"
        for thread in summary.unresolved_blocking_threads
    ]


def _mergeability_message(context: PullRequestContext, blockers: list[str]) -> str:
    if blockers:
        return "PR mergeability is not acceptable."
    mergeable = context.mergeable or "unknown"
    merge_state_status = context.merge_state_status or "unknown"
    return f"Mergeability is acceptable (mergeable={mergeable}, merge_state_status={merge_state_status})."


def _mergeability_blockers(context: PullRequestContext) -> list[str]:
    blockers: list[str] = []
    if context.mergeable not in ACCEPTABLE_MERGEABLE:
        blockers.append(f"PR mergeable state is {context.mergeable or 'unknown'}.")
    if context.merge_state_status not in ACCEPTABLE_MERGE_STATE_STATUS:
        blockers.append(f"PR merge state status is {context.merge_state_status or 'unknown'}.")
    return blockers


def _check_counts(status_checks: tuple[CheckSummary, ...]) -> dict[str, int]:
    counts = {"success": 0, "failure": 0, "pending": 0, "skipped": 0, "unknown": 0}
    for check in status_checks:
        counts[check.bucket] = counts.get(check.bucket, 0) + 1
    return counts


def _check_name(check: CheckSummary) -> str:
    return check.name or check.workflow or "unnamed-check"


def _check_facts(check: CheckSummary) -> str:
    parts: list[str] = []
    if check.status:
        parts.append(f"status={check.status}")
    if check.conclusion:
        parts.append(f"conclusion={check.conclusion}")
    if check.state:
        parts.append(f"state={check.state}")
    return ", ".join(parts) or "state=unknown"


def _required_checks_from_names(required_check_names: tuple[str, ...]) -> RequiredStatusChecks:
    status = "configured" if required_check_names else "not_configured"
    return RequiredStatusChecks(names=required_check_names, status=status)


def _thread_label(thread: object) -> str:
    path = getattr(thread, "path", None) or "unknown-path"
    line = getattr(thread, "line", None) or getattr(thread, "start_line", None)
    location = f"{path}:{line}" if line else path
    thread_id = getattr(thread, "id", None)
    return f"{location} ({thread_id})" if thread_id else location
