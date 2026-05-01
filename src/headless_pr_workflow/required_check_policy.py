"""Repository policy support for required status checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .github import CheckSummary, RequiredStatusChecks


DEFAULT_POLICY_PATH = Path("docs/required-check-policy.json")
POLICY_ABSENT_STATUS = "policy_absent"


@dataclass(frozen=True)
class RequiredCheckPolicy:
    branch: str
    required_status_checks: str
    ci_workflows: str
    source: str
    rationale: str | None = None

    @property
    def declares_no_ci_required_checks(self) -> bool:
        return self.required_status_checks == "absent" and self.ci_workflows == "absent"


def load_required_check_policy(
    *,
    repo_root: Path | None = None,
    policy_path: Path = DEFAULT_POLICY_PATH,
) -> dict[str, RequiredCheckPolicy]:
    root = Path.cwd() if repo_root is None else repo_root
    path = root / policy_path
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    branches = raw.get("branches")
    if not isinstance(branches, dict):
        return {}

    policies: dict[str, RequiredCheckPolicy] = {}
    for branch, branch_policy in branches.items():
        if not isinstance(branch, str) or not isinstance(branch_policy, dict):
            continue
        policies[branch] = RequiredCheckPolicy(
            branch=branch,
            required_status_checks=_string_value(branch_policy, "required_status_checks"),
            ci_workflows=_string_value(branch_policy, "ci_workflows"),
            source=_string_value(branch_policy, "source"),
            rationale=_optional_string_value(branch_policy, "rationale"),
        )
    return policies


def apply_required_check_policy(
    required_checks: RequiredStatusChecks,
    *,
    branch: str,
    status_checks: tuple[CheckSummary, ...],
    repo_root: Path | None = None,
) -> RequiredStatusChecks:
    if required_checks.status != "unavailable":
        return required_checks

    policy = load_required_check_policy(repo_root=repo_root).get(branch)
    if policy is None or not policy.declares_no_ci_required_checks:
        return required_checks

    root = Path.cwd() if repo_root is None else repo_root
    if _workflow_files_present(root) or _has_blocking_reported_checks(status_checks):
        return required_checks

    return RequiredStatusChecks(
        names=(),
        status=POLICY_ABSENT_STATUS,
        source=policy.source or "repository-policy",
        message=f"Required checks are absent by repository policy for {branch}.",
    )


def _workflow_files_present(repo_root: Path) -> bool:
    workflow_dir = repo_root / ".github" / "workflows"
    if not workflow_dir.exists():
        return False
    return any(path.is_file() and path.suffix.lower() in {".yml", ".yaml"} for path in workflow_dir.iterdir())


def _has_blocking_reported_checks(status_checks: tuple[CheckSummary, ...]) -> bool:
    return any(check.bucket not in {"success", "skipped"} for check in status_checks)


def _string_value(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""


def _optional_string_value(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) and value else None
