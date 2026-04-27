"""Composed merge-readiness evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .approval_check import ApprovalCheckSummary, summarize_approval_check
from .github import CheckSummary, PullRequestContext


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
            "approval": self.approval.to_dict(),
            "status_checks": [check.to_dict() for check in self.status_checks],
            "check_counts": _check_counts(self.status_checks),
            "checks": [check.to_dict() for check in self.checks],
            "blocking_reasons": list(self.blocking_reasons),
            "hard_gate_passed": self.hard_gate_passed,
        }


def summarize_pre_merge(context: PullRequestContext, *, expected_base_ref_name: str | None) -> PreMergeSummary:
    approval = summarize_approval_check(context)
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

    target_branch_blockers = _target_branch_blockers(context, expected_base_ref_name=expected_base_ref_name)
    checks.append(
        PreMergeCheck(
            code="target-branch-expected",
            ok=not target_branch_blockers,
            message=_target_branch_message(context, expected_base_ref_name=expected_base_ref_name, blockers=target_branch_blockers),
            details=tuple(target_branch_blockers),
        )
    )
    blocking_reasons.extend(target_branch_blockers)

    check_blockers = _status_check_blockers(context.status_checks)
    checks.append(
        PreMergeCheck(
            code="required-checks-passing",
            ok=not check_blockers,
            message=_status_check_message(context.status_checks, check_blockers),
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


def _status_check_message(status_checks: tuple[CheckSummary, ...], blockers: list[str]) -> str:
    if blockers:
        return "Required status checks are not yet merge-ready."
    return "All reported status checks are passing or skipped."


def _status_check_blockers(status_checks: tuple[CheckSummary, ...]) -> list[str]:
    if not status_checks:
        return ["GitHub reported no status checks for the current head SHA."]
    blockers: list[str] = []
    for check in status_checks:
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


def _mergeability_message(context: PullRequestContext, blockers: list[str]) -> str:
    if blockers:
        return "PR mergeability is not acceptable."
    mergeable = context.mergeable or "unknown"
    merge_state_status = context.merge_state_status or "unknown"
    return f"Mergeability is acceptable (mergeable={mergeable}, merge_state_status={merge_state_status})."


def _target_branch_message(
    context: PullRequestContext,
    *,
    expected_base_ref_name: str | None,
    blockers: list[str],
) -> str:
    if blockers:
        return "Target base branch is not acceptable."
    return f"Target base branch matches expected {expected_base_ref_name}."


def _target_branch_blockers(
    context: PullRequestContext,
    *,
    expected_base_ref_name: str | None,
) -> list[str]:
    if not expected_base_ref_name:
        return ["Expected target base branch is unknown."]
    if not context.base_ref_name:
        return ["Target base branch is unknown."]
    if context.base_ref_name != expected_base_ref_name:
        return [
            f"PR targets base branch {context.base_ref_name}, expected {expected_base_ref_name}.",
        ]
    return []


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
