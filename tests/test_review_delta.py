from headless_pr_workflow.review_delta import (
    CommitComparison,
    CommitComparisonFile,
    select_review_delta_baseline,
    summarize_review_delta,
)

from tests.github_scenarios import (
    build_pr_context,
    build_review,
    scenario_current_approval,
    scenario_stale_approval,
    solo_override_body,
)


def test_review_delta_reports_changed_files_since_stale_formal_approval():
    comparison = CommitComparison(
        base_sha="old-head",
        head_sha="new-head",
        status="ahead",
        ahead_by=1,
        behind_by=0,
        total_commits=1,
        files=(
            CommitComparisonFile(
                path="src/example.py",
                status="modified",
                additions=4,
                deletions=2,
                changes=6,
            ),
            CommitComparisonFile(
                path="tests/test_example.py",
                status="added",
                additions=10,
                deletions=0,
                changes=10,
            ),
        ),
    )

    summary = summarize_review_delta(
        scenario_stale_approval(head_sha="new-head", approval_sha="old-head"),
        comparison,
    )

    assert summary.report_generated is True
    assert summary.baseline is not None
    assert summary.baseline.sha == "old-head"
    assert summary.baseline.source == "formal-approval"
    assert summary.current_head_sha == "new-head"
    assert summary.delta_exists is True
    assert summary.status == "delta"
    assert summary.changed_file_count == 2
    assert summary.additions == 14
    assert summary.deletions == 2
    assert [file.path for file in summary.files] == ["src/example.py", "tests/test_example.py"]


def test_review_delta_reports_unchanged_head_for_current_approval():
    summary = summarize_review_delta(scenario_current_approval(head_sha="head"))

    assert summary.report_generated is True
    assert summary.baseline is not None
    assert summary.baseline.sha == "head"
    assert summary.baseline.source == "formal-approval"
    assert summary.current_head_sha == "head"
    assert summary.delta_exists is False
    assert summary.status == "unchanged"
    assert summary.changed_file_count == 0
    assert summary.additions == 0
    assert summary.deletions == 0


def test_review_delta_missing_baseline_is_non_reportable():
    summary = summarize_review_delta(build_pr_context(head_ref_oid="head"))

    assert summary.report_generated is False
    assert summary.baseline is None
    assert summary.status == "missing-baseline"
    assert summary.error == "missing-baseline"
    assert summary.messages == ("No reviewed or approved SHA could be found for PR #123.",)


def test_review_delta_uses_latest_accepted_solo_override_as_baseline():
    old_head = "old-head"
    context = build_pr_context(
        head_ref_oid="new-head",
        latest_reviews=(
            build_review(
                state="COMMENTED",
                commit_oid=old_head,
                body=solo_override_body(head_sha=old_head),
            ),
        ),
    )

    baseline = select_review_delta_baseline(context)

    assert baseline is not None
    assert baseline.sha == old_head
    assert baseline.source == "solo-maintainer-override"


def test_review_delta_json_contract_exposes_stable_fields():
    summary = summarize_review_delta(scenario_current_approval(head_sha="head"))

    payload = summary.to_dict()

    assert payload["number"] == 123
    assert payload["url"] == "https://github.com/owner/repo/pull/123"
    assert payload["repository"] == "owner/repo"
    assert payload["baseline_sha"] == "head"
    assert payload["baseline_source"] == "formal-approval"
    assert payload["baseline_review_state"] == "APPROVED"
    assert payload["baseline_review_author"] == "reviewer"
    assert payload["current_head_sha"] == "head"
    assert payload["head_ref_name"] == "feature/scenario"
    assert payload["status"] == "unchanged"
    assert payload["delta_exists"] is False
    assert payload["unchanged_head"] is True
    assert payload["missing_baseline"] is False
    assert payload["changed_file_count"] == 0
    assert payload["additions"] == 0
    assert payload["deletions"] == 0
    assert payload["files"] == []
    assert payload["error"] is None
