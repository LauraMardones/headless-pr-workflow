import json

from headless_pr_workflow import cli
from headless_pr_workflow.github import GHCommandError
from headless_pr_workflow.review_delta import CommitComparison, CommitComparisonFile

from tests.github_scenarios import build_pr_context, scenario_current_approval, scenario_stale_approval


def test_catalog_marks_review_delta_implemented(capsys):
    exit_code = cli.main(["catalog"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "review-delta\tP1-high\tF-review\treport\tcore\timplemented" in output


def test_review_delta_json_output_reports_delta(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "fetch_pr_context",
        lambda target, repo=None: scenario_stale_approval(head_sha="new-head", approval_sha="old-head"),
    )
    monkeypatch.setattr(
        cli,
        "fetch_commit_comparison",
        lambda repo, base_sha, head_sha: CommitComparison(
            base_sha=base_sha,
            head_sha=head_sha,
            status="ahead",
            ahead_by=1,
            behind_by=0,
            total_commits=1,
            files=(
                CommitComparisonFile(
                    path="src/review_delta.py",
                    status="added",
                    additions=20,
                    deletions=0,
                    changes=20,
                ),
            ),
        ),
    )

    exit_code = cli.main(["review-delta", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["baseline_sha"] == "old-head"
    assert output["baseline_source"] == "formal-approval"
    assert output["current_head_sha"] == "new-head"
    assert output["status"] == "delta"
    assert output["delta_exists"] is True
    assert output["changed_file_count"] == 1
    assert output["additions"] == 20
    assert output["deletions"] == 0
    assert output["files"][0]["path"] == "src/review_delta.py"


def test_review_delta_json_output_exits_nonzero_without_baseline(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head"))

    exit_code = cli.main(["review-delta", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "missing-baseline"
    assert output["missing_baseline"] is True
    assert output["error"] == "missing-baseline"
    assert output["messages"] == ["No reviewed or approved SHA could be found for PR #123."]


def test_review_delta_human_output_reports_unchanged_head(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: scenario_current_approval(head_sha="head"))

    exit_code = cli.main(["review-delta", "123", "--repo", "owner/repo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "PR #123: Scenario PR" in output
    assert "baseline sha: head" in output
    assert "baseline source: formal-approval" in output
    assert "current head sha: head" in output
    assert "delta exists: false" in output
    assert "status: unchanged" in output
    assert "No post-review delta exists; current head matches the review baseline." in output


def test_review_delta_json_output_reports_github_comparison_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "fetch_pr_context",
        lambda target, repo=None: scenario_stale_approval(head_sha="new-head", approval_sha="old-head"),
    )

    def fail(repo, base_sha, head_sha):
        raise GHCommandError(["gh", "api", "repos/owner/repo/compare/old-head...new-head"], 1, "compare failed")

    monkeypatch.setattr(cli, "fetch_commit_comparison", fail)

    exit_code = cli.main(["review-delta", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "comparison-failed"
    assert output["baseline_sha"] == "old-head"
    assert output["current_head_sha"] == "new-head"
    assert output["error"] == "gh-command-failed"
    assert output["stderr"] == "compare failed"
