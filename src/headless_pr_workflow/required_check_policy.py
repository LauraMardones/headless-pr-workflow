"""Repository policy support for required status checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .github import CheckSummary, RequiredStatusChecks


DEFAULT_POLICY_PATH = Path("docs/required-check-policy.json")
POLICY_ABSENT_STATUS = "policy_absent"
POLICY_REQUIRED_UNCONFIGURED_STATUS = "policy_required_unconfigured"
POLICY_INCONSISTENT_STATUS = "policy_inconsistent"

REQUIRED_STATUS_CHECKS_REQUIRED = "required"
GITHUB_REPORTED_STATUSES = ("configured", "not_configured")

CI_WORKFLOWS_ABSENT = "absent"
CI_WORKFLOWS_PRESENT_NON_REQUIRED = "present_non_required"
CI_WORKFLOWS_PRESENT_REQUIRED = "present_required"


@dataclass(frozen=True)
class RequiredCheckPolicy:
    branch: str
    required_status_checks: str
    ci_workflows: str
    source: str
    rationale: str | None = None

    @property
    def declares_no_ci_required_checks(self) -> bool:
        return self.required_status_checks == "absent" and self.ci_workflows in (
            CI_WORKFLOWS_ABSENT,
            CI_WORKFLOWS_PRESENT_NON_REQUIRED,
        )

    @property
    def declares_required_checks(self) -> bool:
        return self.required_status_checks == REQUIRED_STATUS_CHECKS_REQUIRED

    @property
    def is_consistent(self) -> bool:
        """Mirror scripts/merge-gate-summary: known values, and "required" pairs only with present_required."""
        if self.required_status_checks not in ("absent", REQUIRED_STATUS_CHECKS_REQUIRED):
            return False
        if self.ci_workflows not in (CI_WORKFLOWS_ABSENT, CI_WORKFLOWS_PRESENT_NON_REQUIRED, CI_WORKFLOWS_PRESENT_REQUIRED):
            return False
        return self.declares_required_checks == (self.ci_workflows == CI_WORKFLOWS_PRESENT_REQUIRED)


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
        if required_checks.names or required_checks.status not in GITHUB_REPORTED_STATUSES:
            return required_checks
        # GitHub reports no required checks: a policy that declares them required must not pass.
        policy = load_required_check_policy(repo_root=repo_root).get(branch)
        if policy is None:
            return required_checks
        if not policy.is_consistent:
            return _inconsistent_policy(policy, branch)
        if not policy.declares_required_checks:
            return required_checks
        return RequiredStatusChecks(
            names=(),
            status=POLICY_REQUIRED_UNCONFIGURED_STATUS,
            source=policy.source or "repository-policy",
            message=f"Repository policy requires status checks for {branch}, but GitHub reports none configured.",
        )

    policy = load_required_check_policy(repo_root=repo_root).get(branch)
    if policy is None:
        return required_checks
    if not policy.is_consistent:
        return _inconsistent_policy(policy, branch)
    if not policy.declares_no_ci_required_checks:
        return required_checks

    root = Path.cwd() if repo_root is None else repo_root
    # When policy declares ci_workflows absent, block if workflows actually exist (consistency guard).
    # When policy declares present_non_required, workflows are expected and do not block.
    if policy.ci_workflows == CI_WORKFLOWS_ABSENT and _workflow_files_present(root):
        return required_checks
    if _has_blocking_reported_checks(status_checks):
        return required_checks

    return RequiredStatusChecks(
        names=(),
        status=POLICY_ABSENT_STATUS,
        source=policy.source or "repository-policy",
        message=f"Required checks are absent by repository policy for {branch}.",
    )


def _inconsistent_policy(policy: RequiredCheckPolicy, branch: str) -> RequiredStatusChecks:
    return RequiredStatusChecks(
        names=(),
        status=POLICY_INCONSISTENT_STATUS,
        source=policy.source or "repository-policy",
        message=(
            f"Repository policy for {branch} is inconsistent: "
            f"required_status_checks={policy.required_status_checks!r}, ci_workflows={policy.ci_workflows!r}."
        ),
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
