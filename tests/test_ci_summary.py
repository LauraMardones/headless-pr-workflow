from headless_pr_workflow.ci_summary import summarize_ci
from headless_pr_workflow.github import RequiredStatusChecks

from tests.github_scenarios import build_check, scenario_current_approval, scenario_empty_status_rollup, with_context


def test_ci_summary_reports_required_checks_satisfied_with_skipped_checks():
    summary = summarize_ci(
        scenario_current_approval(
            head_sha="head123",
            status_checks=(
                build_check(name="unit", bucket="success", status="COMPLETED", conclusion="SUCCESS"),
                build_check(name="docs", bucket="skipped", status="COMPLETED", conclusion="SKIPPED"),
            ),
        ),
        required_checks=RequiredStatusChecks(names=("unit", "docs"), status="configured"),
    )

    assert summary.required_check_status == "satisfied"
    assert summary.required_checks_satisfied is True
    assert summary.check_buckets["passing"] == ("unit",)
    assert summary.check_buckets["skipped"] == ("docs",)
    assert summary.check_buckets["missing"] == ()


def test_ci_summary_reports_absent_required_checks_and_empty_rollup():
    summary = summarize_ci(
        scenario_empty_status_rollup(head_sha="head123"),
        required_checks=RequiredStatusChecks(names=(), status="not_configured"),
    )

    assert summary.status_rollup == "empty"
    assert summary.required_check_status == "not_configured"
    assert summary.required_checks_satisfied is None
    assert "No required status checks are configured for the target branch." in summary.messages
    assert "GitHub reported an empty status check rollup for the current head SHA." in summary.messages


def test_ci_summary_reports_missing_required_checks():
    summary = summarize_ci(
        scenario_empty_status_rollup(head_sha="head123"),
        required_checks=RequiredStatusChecks(names=("unit",), status="configured"),
    )

    assert summary.required_check_status == "missing"
    assert summary.required_checks_satisfied is False
    assert summary.check_buckets["missing"] == ("unit",)
    assert "Required status checks are missing from the current head SHA: unit." in summary.messages


def test_ci_summary_reports_failing_pending_and_unknown_checks():
    summary = summarize_ci(
        with_context(
            scenario_current_approval(head_sha="head123"),
            status_checks=(
                build_check(name="unit", bucket="failure", status="COMPLETED", conclusion="FAILURE"),
                build_check(name="lint", bucket="pending", state="PENDING"),
                build_check(name="security", bucket="unknown"),
            ),
        ),
        required_checks=RequiredStatusChecks(names=("unit", "lint", "security"), status="configured"),
    )

    assert summary.required_check_status == "failing"
    assert summary.required_checks_satisfied is False
    assert summary.check_buckets["failing"] == ("unit",)
    assert summary.check_buckets["pending"] == ("lint",)
    assert summary.check_buckets["unknown"] == ("security",)


def test_ci_summary_reports_pending_required_checks():
    summary = summarize_ci(
        scenario_current_approval(
            head_sha="head123",
            status_checks=(build_check(name="lint", bucket="pending", state="PENDING"),),
        ),
        required_checks=RequiredStatusChecks(names=("lint",), status="configured"),
    )

    assert summary.required_check_status == "pending"
    assert summary.required_checks_satisfied is False


def test_ci_summary_reports_unknown_required_checks():
    summary = summarize_ci(
        scenario_current_approval(
            head_sha="head123",
            status_checks=(build_check(name="security", bucket="unknown"),),
        ),
        required_checks=RequiredStatusChecks(names=("security",), status="configured"),
    )

    assert summary.required_check_status == "unknown"
    assert summary.required_checks_satisfied is False


def test_ci_summary_reports_unavailable_required_check_data():
    summary = summarize_ci(
        scenario_empty_status_rollup(head_sha="head123", base_ref_name="release"),
        required_checks=RequiredStatusChecks(names=(), status="unavailable", message="branch protection unavailable"),
    )

    assert summary.required_check_status == "unavailable"
    assert summary.required_checks_satisfied is None
    assert "Required status check data is unavailable from branch protection: branch protection unavailable." in summary.messages


def test_ci_summary_reports_policy_absent_required_checks():
    summary = summarize_ci(
        scenario_empty_status_rollup(head_sha="head123"),
        required_checks=RequiredStatusChecks(
            names=(),
            status="policy_absent",
            source="docs/MERGE-POLICY.md#main-required-check-policy",
            message="Required checks are absent by repository policy for main.",
        ),
    )

    assert summary.status_rollup == "empty"
    assert summary.required_check_status == "policy_absent"
    assert summary.required_checks_satisfied is True
    assert summary.required_checks.to_dict()["source"] == "docs/MERGE-POLICY.md#main-required-check-policy"
    assert "Required status checks are absent by explicit repository policy. Source: docs/MERGE-POLICY.md#main-required-check-policy." in summary.messages
