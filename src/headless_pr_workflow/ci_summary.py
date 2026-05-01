"""CI/status-check reporting for the current PR head SHA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .github import CheckSummary, PullRequestContext, RequiredStatusChecks
from .required_check_policy import apply_required_check_policy


CHECK_STATES: tuple[str, ...] = ("passing", "failing", "pending", "skipped", "missing", "unknown")


@dataclass(frozen=True)
class CiSummary:
    number: int
    title: str
    url: str
    base_ref_name: str
    head_ref_name: str
    head_ref_oid: str
    status_rollup: str
    required_checks: RequiredStatusChecks
    required_check_status: str
    required_checks_satisfied: bool | None
    status_checks: tuple[CheckSummary, ...]
    check_buckets: dict[str, tuple[str, ...]]
    messages: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "base_ref_name": self.base_ref_name,
            "head_ref_name": self.head_ref_name,
            "head_ref_oid": self.head_ref_oid,
            "status_rollup": self.status_rollup,
            "required_checks": self.required_checks.to_dict(),
            "required_check_status": self.required_check_status,
            "required_checks_satisfied": self.required_checks_satisfied,
            "status_checks": [check.to_dict() | {"classification": _classification(check)} for check in self.status_checks],
            "check_buckets": {state: list(names) for state, names in self.check_buckets.items()},
            "messages": list(self.messages),
        }


def summarize_ci(context: PullRequestContext, *, required_checks: RequiredStatusChecks) -> CiSummary:
    required_checks = apply_required_check_policy(
        required_checks,
        branch=context.base_ref_name,
        status_checks=context.status_checks,
    )
    buckets = {state: [] for state in CHECK_STATES}
    observed_checks = {_check_name(check): check for check in context.status_checks}

    for check in context.status_checks:
        buckets[_classification(check)].append(_check_name(check))

    observed_names = set(observed_checks)
    missing_required = tuple(name for name in required_checks.names if name not in observed_names)
    buckets["missing"].extend(missing_required)

    required_check_status, required_checks_satisfied = _required_check_status(
        required_checks,
        observed_checks=observed_checks,
        missing_required=missing_required,
    )
    messages = _messages(
        context.status_checks,
        required_checks=required_checks,
        required_check_status=required_check_status,
        missing_required=missing_required,
    )

    return CiSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        base_ref_name=context.base_ref_name,
        head_ref_name=context.head_ref_name,
        head_ref_oid=context.head_ref_oid,
        status_rollup="present" if context.status_checks else "empty",
        required_checks=required_checks,
        required_check_status=required_check_status,
        required_checks_satisfied=required_checks_satisfied,
        status_checks=context.status_checks,
        check_buckets={state: tuple(names) for state, names in buckets.items()},
        messages=messages,
    )


def _required_check_status(
    required_checks: RequiredStatusChecks,
    *,
    observed_checks: dict[str, CheckSummary],
    missing_required: tuple[str, ...],
) -> tuple[str, bool | None]:
    if required_checks.status == "unavailable":
        return "unavailable", None
    if required_checks.status == "policy_absent":
        return "policy_absent", True
    if not required_checks.names:
        return "not_configured", None
    if missing_required:
        return "missing", False
    required_observed = tuple(observed_checks[name] for name in required_checks.names)
    required_buckets = {_classification(check) for check in required_observed}
    if "failing" in required_buckets:
        return "failing", False
    if "pending" in required_buckets:
        return "pending", False
    if "unknown" in required_buckets:
        return "unknown", False
    return "satisfied", True


def _messages(
    status_checks: tuple[CheckSummary, ...],
    *,
    required_checks: RequiredStatusChecks,
    required_check_status: str,
    missing_required: tuple[str, ...],
) -> tuple[str, ...]:
    messages: list[str] = []
    if not status_checks:
        messages.append("GitHub reported an empty status check rollup for the current head SHA.")
    if required_check_status == "not_configured":
        messages.append("No required status checks are configured for the target branch.")
    elif required_check_status == "policy_absent":
        detail = f" Source: {required_checks.source}." if required_checks.source else ""
        messages.append(f"Required status checks are absent by explicit repository policy.{detail}")
    elif required_check_status == "unavailable":
        detail = f": {required_checks.message.rstrip('.')}" if required_checks.message else ""
        messages.append(f"Required status check data is unavailable from branch protection{detail}.")
    elif required_check_status == "missing":
        messages.append(f"Required status checks are missing from the current head SHA: {', '.join(missing_required)}.")
    elif required_check_status == "satisfied":
        messages.append("All required status checks are passing or skipped for the current head SHA.")
    else:
        messages.append(f"Required status checks are not satisfied because at least one required check is {required_check_status}.")
    return tuple(messages)


def _classification(check: CheckSummary) -> str:
    if check.bucket == "success":
        return "passing"
    if check.bucket == "failure":
        return "failing"
    if check.bucket in {"pending", "skipped", "unknown"}:
        return check.bucket
    return "unknown"


def _check_name(check: CheckSummary) -> str:
    return check.name or check.workflow or "unnamed-check"
