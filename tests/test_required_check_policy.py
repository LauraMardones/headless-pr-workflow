import json
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from headless_pr_workflow.github import RequiredStatusChecks
from headless_pr_workflow.required_check_policy import apply_required_check_policy, load_required_check_policy

from tests.github_scenarios import build_check


@contextmanager
def temp_repo_root():
    repo_root = Path.cwd() / ".pytest-policy-tmp" / f"hpw-required-policy-{uuid.uuid4().hex}"
    repo_root.mkdir(parents=True)
    try:
        yield repo_root
    finally:
        shutil.rmtree(repo_root.parent, ignore_errors=True)


def write_policy(repo_root):
    policy_path = repo_root / "docs" / "required-check-policy.json"
    policy_path.parent.mkdir()
    policy_path.write_text(
        json.dumps(
            {
                "schema": "headless-pr-workflow.required-check-policy.v1",
                "branches": {
                    "main": {
                        "required_status_checks": "absent",
                        "ci_workflows": "absent",
                        "source": "docs/MERGE-POLICY.md#main-required-check-policy",
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_load_required_check_policy_reads_structured_branch_policy():
    with temp_repo_root() as repo_root:
        write_policy(repo_root)

        policies = load_required_check_policy(repo_root=repo_root)

    assert policies["main"].declares_no_ci_required_checks is True
    assert policies["main"].source == "docs/MERGE-POLICY.md#main-required-check-policy"


def test_policy_absent_applies_to_unavailable_checks_with_no_workflows():
    with temp_repo_root() as repo_root:
        write_policy(repo_root)

        required = apply_required_check_policy(
            RequiredStatusChecks(names=(), status="unavailable", message="Not Found"),
            branch="main",
            status_checks=(),
            repo_root=repo_root,
        )

    assert required.status == "policy_absent"
    assert required.names == ()
    assert required.source == "docs/MERGE-POLICY.md#main-required-check-policy"


def test_policy_does_not_mask_configured_required_checks():
    with temp_repo_root() as repo_root:
        write_policy(repo_root)
        original = RequiredStatusChecks(names=("unit",), status="configured")

        required = apply_required_check_policy(original, branch="main", status_checks=(), repo_root=repo_root)

    assert required is original


def test_policy_does_not_mask_reported_failing_pending_or_unknown_checks():
    with temp_repo_root() as repo_root:
        write_policy(repo_root)
        checks = (
            build_check(name="unit", bucket="failure"),
            build_check(name="lint", bucket="pending"),
            build_check(name="security", bucket="unknown"),
        )
        original = RequiredStatusChecks(names=(), status="unavailable", message="Not Found")

        required = apply_required_check_policy(original, branch="main", status_checks=checks, repo_root=repo_root)

    assert required is original


def test_policy_does_not_apply_when_workflows_exist():
    with temp_repo_root() as repo_root:
        write_policy(repo_root)
        workflows = repo_root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        original = RequiredStatusChecks(names=(), status="unavailable", message="Not Found")

        required = apply_required_check_policy(original, branch="main", status_checks=(), repo_root=repo_root)

    assert required is original


def test_missing_policy_keeps_unavailable_required_check_data():
    with temp_repo_root() as repo_root:
        original = RequiredStatusChecks(names=(), status="unavailable", message="Not Found")

        required = apply_required_check_policy(original, branch="main", status_checks=(), repo_root=repo_root)

    assert required is original
