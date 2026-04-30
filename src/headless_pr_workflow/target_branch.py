"""Target branch hard-gate evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .github import PullRequestContext


@dataclass(frozen=True)
class TargetBranchSummary:
    number: int
    title: str
    url: str
    base_ref_name: str
    expected_base_ref_name: str | None
    result: str
    blocking_reasons: tuple[str, ...]
    hard_gate_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "base_ref_name": self.base_ref_name,
            "expected_base_ref_name": self.expected_base_ref_name,
            "result": self.result,
            "blocking_reasons": list(self.blocking_reasons),
            "hard_gate_passed": self.hard_gate_passed,
        }


def summarize_target_branch(
    context: PullRequestContext,
    *,
    expected_base_ref_name: str | None,
) -> TargetBranchSummary:
    blockers = tuple(target_branch_blockers(context.base_ref_name, expected_base_ref_name=expected_base_ref_name))
    hard_gate_passed = not blockers
    return TargetBranchSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        base_ref_name=context.base_ref_name,
        expected_base_ref_name=expected_base_ref_name,
        result="pass" if hard_gate_passed else "fail",
        blocking_reasons=blockers,
        hard_gate_passed=hard_gate_passed,
    )


def target_branch_message(summary: TargetBranchSummary) -> str:
    if summary.blocking_reasons:
        return "Target base branch is not acceptable."
    return f"Target base branch matches expected {summary.expected_base_ref_name}."


def target_branch_blockers(base_ref_name: str | None, *, expected_base_ref_name: str | None) -> list[str]:
    if not expected_base_ref_name:
        return ["Expected target base branch is unknown."]
    if not base_ref_name:
        return ["Target base branch is unknown."]
    if base_ref_name != expected_base_ref_name:
        return [
            f"PR targets base branch {base_ref_name}, expected {expected_base_ref_name}.",
        ]
    return []
