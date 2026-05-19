"""Next safe workflow action advisory command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


ACTION_BY_POSTURE = {
    "implementation_required": "implement",
    "review_required": "review",
    "merge_validation_required": "merge_validate",
    "merged": "post-merge-sync",
    "waiting": "wait",
    "human_decision_required": "escalate",
}


@dataclass(frozen=True)
class NextActionResult:
    ok: bool
    repository: str | None
    pr: int | None
    action: str | None
    rationale: str
    source_posture: str | None
    blocking_reasons: tuple[str, ...]
    source_commands: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": "next-action",
            "ok": self.ok,
            "repository": self.repository,
            "pr": self.pr,
            "action": self.action,
            "rationale": self.rationale,
            "source_posture": self.source_posture,
            "blocking_reasons": list(self.blocking_reasons),
            "source_commands": list(self.source_commands),
            "warnings": list(self.warnings),
            "errors": self.errors,
        }


def build_workflow_status_command(pr: str, *, repo: str, path: str | None = None) -> tuple[str, ...]:
    command = (
        sys.executable,
        "-m",
        "headless_pr_workflow.cli",
        "workflow-status",
        pr,
        "--repo",
        repo,
        "--json",
    )
    if path is not None:
        command += ("--path", path)
    return command


def fetch_workflow_status(pr: str, *, repo: str, path: str | None = None) -> dict[str, Any]:
    command = build_workflow_status_command(pr, repo=repo, path=path or os.getcwd())
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise WorkflowStatusError(
            "workflow-status-failed",
            {
                "command": list(command),
                "returncode": completed.returncode,
                "stderr": completed.stderr.strip(),
                "stdout": completed.stdout.strip(),
            },
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise WorkflowStatusError(
            "workflow-status-parse-failed",
            {
                "command": list(command),
                "message": str(error),
                "stdout": completed.stdout,
            },
        ) from error

    if not isinstance(payload, dict):
        raise WorkflowStatusError(
            "workflow-status-malformed",
            {
                "command": list(command),
                "message": "workflow-status JSON output must be an object.",
            },
        )
    return payload


class WorkflowStatusError(RuntimeError):
    def __init__(self, error_type: str, details: dict[str, Any]) -> None:
        super().__init__(error_type)
        self.error_type = error_type
        self.details = details


def summarize_next_action(workflow_status: dict[str, Any]) -> NextActionResult:
    repository = _string_or_none(workflow_status.get("repository"))
    pr_number = _pr_number(workflow_status.get("pr"))
    warnings = tuple(str(warning) for warning in workflow_status.get("warnings", ()) or ())

    posture = workflow_status.get("workflow_posture")
    if not isinstance(posture, dict) or not posture.get("status"):
        return _error_result(
            repository=repository,
            pr=pr_number,
            warnings=warnings,
            error_type="workflow-status-malformed",
            message="workflow-status output is missing workflow_posture.status.",
        )

    source_posture = str(posture["status"])
    source_commands = _source_commands(posture)
    posture_reasons = _string_tuple(posture.get("reasons"))

    safety_result = _safety_guard_result(
        workflow_status,
        repository=repository,
        pr=pr_number,
        source_posture=source_posture,
        source_commands=source_commands,
        posture_reasons=posture_reasons,
        warnings=warnings,
    )
    if safety_result is not None:
        return safety_result

    action = ACTION_BY_POSTURE.get(source_posture)
    if action is None:
        blocking_reasons = posture_reasons or (f"Unknown workflow posture: {source_posture}.",)
        return NextActionResult(
            ok=True,
            repository=repository,
            pr=pr_number,
            action="escalate",
            rationale=(
                f"workflow-status reported posture {source_posture}, which next-action does not map "
                "to an automated workflow action; human judgment is required."
            ),
            source_posture=source_posture,
            blocking_reasons=blocking_reasons,
            source_commands=source_commands,
            warnings=warnings,
        )

    return NextActionResult(
        ok=True,
        repository=repository,
        pr=pr_number,
        action=action,
        rationale=_rationale_for(action, source_posture, posture, posture_reasons),
        source_posture=source_posture,
        blocking_reasons=posture_reasons,
        source_commands=source_commands,
        warnings=warnings,
    )


def summarize_next_action_from_subprocess(pr: str, *, repo: str, path: str | None = None) -> NextActionResult:
    try:
        workflow_status = fetch_workflow_status(pr, repo=repo, path=path)
    except WorkflowStatusError as error:
        return _error_result(
            repository=repo,
            pr=_parse_int(pr),
            warnings=(),
            error_type=error.error_type,
            message="Unable to fetch or parse workflow-status output.",
            details=error.details,
        )

    return summarize_next_action(workflow_status)


def _safety_guard_result(
    workflow_status: dict[str, Any],
    *,
    repository: str | None,
    pr: int | None,
    source_posture: str,
    source_commands: tuple[str, ...],
    posture_reasons: tuple[str, ...],
    warnings: tuple[str, ...],
) -> NextActionResult | None:
    pr_info = workflow_status.get("pr") if isinstance(workflow_status.get("pr"), dict) else {}
    checks = workflow_status.get("checks") if isinstance(workflow_status.get("checks"), dict) else {}
    approval = workflow_status.get("approval") if isinstance(workflow_status.get("approval"), dict) else {}
    re_review = workflow_status.get("re_review") if isinstance(workflow_status.get("re_review"), dict) else {}
    review_threads = (
        workflow_status.get("review_threads") if isinstance(workflow_status.get("review_threads"), dict) else {}
    )
    merge_readiness = (
        workflow_status.get("merge_readiness") if isinstance(workflow_status.get("merge_readiness"), dict) else {}
    )

    if pr_info.get("state") == "MERGED":
        return _guarded_result(
            "post-merge-sync",
            "PR is merged. Run post-merge-sync to update local state.",
            repository,
            pr,
            source_posture,
            (),
            source_commands,
            warnings,
        )

    if pr_info.get("state") == "CLOSED":
        return _guarded_result(
            "closed",
            "PR is closed without merging. No further workflow action is required.",
            repository,
            pr,
            source_posture,
            (),
            source_commands,
            warnings,
        )

    if merge_readiness.get("mergeable") == "UNKNOWN":
        reasons = _reasons(posture_reasons, "GitHub mergeability is UNKNOWN.")
        return _guarded_result(
            "escalate",
            "workflow-status reports unknown mergeability; human judgment is required before advancing.",
            repository,
            pr,
            source_posture,
            reasons,
            source_commands,
            warnings,
        )

    if pr_info.get("is_draft") is True:
        reasons = _reasons(posture_reasons, "PR is draft.")
        return _guarded_result(
            "wait",
            "workflow-status reports a draft PR; wait until implementation is ready and the PR is marked ready.",
            repository,
            pr,
            source_posture,
            reasons,
            source_commands,
            warnings,
        )

    if approval.get("approval_status") == "stale" or re_review.get("re_review_needed") is True:
        reasons = _reasons(_string_tuple(approval.get("blocking_reasons")), *posture_reasons)
        return _guarded_result(
            "review",
            "workflow-status reports stale or missing current-head review evidence; request review.",
            repository,
            pr,
            source_posture,
            reasons,
            source_commands,
            warnings,
        )

    check_buckets = checks.get("check_buckets") if isinstance(checks.get("check_buckets"), dict) else {}
    constrained_checks = []
    for bucket in ("failing", "pending", "missing", "unknown"):
        constrained_checks.extend(str(name) for name in check_buckets.get(bucket, ()) or ())
    if constrained_checks:
        reasons = _reasons(posture_reasons, f"Constrained status check(s): {', '.join(constrained_checks)}.")
        return _guarded_result(
            "wait",
            "workflow-status reports status checks that are failing, pending, missing, or unknown; wait for the check state to settle before advancing.",
            repository,
            pr,
            source_posture,
            reasons,
            source_commands,
            warnings,
        )

    thread_counts = review_threads.get("thread_counts") if isinstance(review_threads.get("thread_counts"), dict) else {}
    unresolved_count = thread_counts.get("unresolved_blocking")
    if isinstance(unresolved_count, int) and unresolved_count > 0:
        reasons = _reasons(posture_reasons, f"{unresolved_count} unresolved review-thread blocker(s) remain.")
        return _guarded_result(
            "wait",
            "workflow-status reports unresolved review-thread blockers; wait until the blockers are resolved.",
            repository,
            pr,
            source_posture,
            reasons,
            source_commands,
            warnings,
        )

    return None


def _guarded_result(
    action: str,
    rationale: str,
    repository: str | None,
    pr: int | None,
    source_posture: str,
    blocking_reasons: tuple[str, ...],
    source_commands: tuple[str, ...],
    warnings: tuple[str, ...],
) -> NextActionResult:
    return NextActionResult(
        ok=True,
        repository=repository,
        pr=pr,
        action=action,
        rationale=rationale,
        source_posture=source_posture,
        blocking_reasons=blocking_reasons,
        source_commands=source_commands,
        warnings=warnings,
    )


def _rationale_for(
    action: str,
    source_posture: str,
    posture: dict[str, Any],
    posture_reasons: tuple[str, ...],
) -> str:
    summary = _string_or_none(posture.get("summary"))
    if summary:
        return f"workflow-status reported posture {source_posture}: {summary}"
    if posture_reasons:
        return f"workflow-status reported posture {source_posture}: {posture_reasons[0]}"
    return f"workflow-status reported posture {source_posture}; recommend {action}."


def _source_commands(posture: dict[str, Any]) -> tuple[str, ...]:
    commands = ["workflow-status"]
    for command in posture.get("source_commands", ()) or ():
        command_text = str(command)
        if command_text not in commands:
            commands.append(command_text)
    return tuple(commands)


def _error_result(
    *,
    repository: str | None,
    pr: int | None,
    warnings: tuple[str, ...],
    error_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> NextActionResult:
    error: dict[str, Any] = {"type": error_type, "message": message}
    if details is not None:
        error["details"] = details
    return NextActionResult(
        ok=False,
        repository=repository,
        pr=pr,
        action=None,
        rationale=message,
        source_posture=None,
        blocking_reasons=(),
        source_commands=("workflow-status",),
        warnings=warnings,
        errors=error,
    )


def _pr_number(pr_payload: object) -> int | None:
    if isinstance(pr_payload, dict):
        return _parse_int(pr_payload.get("number"))
    return _parse_int(pr_payload)


def _parse_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _reasons(*reason_groups: object) -> tuple[str, ...]:
    reasons: list[str] = []
    for group in reason_groups:
        for reason in _string_tuple(group):
            if reason and reason not in reasons:
                reasons.append(reason)
    return tuple(reasons)
