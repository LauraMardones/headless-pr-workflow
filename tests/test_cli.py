from headless_pr_workflow import cli
from headless_pr_workflow.github import GHCommandError
from headless_pr_workflow.github.pr_context import PullRequestContext


def _context() -> PullRequestContext:
    return PullRequestContext(
        number=123,
        title="Implement PR context",
        state="OPEN",
        url="https://github.com/owner/repo/pull/123",
        base_ref_name="main",
        base_ref_oid="base123",
        head_ref_name="feature/pr-context",
        head_ref_oid="head123",
        head_repository="owner/repo",
        head_repository_owner="owner",
        is_cross_repository=False,
        is_draft=False,
        merge_state_status="CLEAN",
        mergeable="MERGEABLE",
        review_decision="REVIEW_REQUIRED",
        changed_files=2,
        additions=10,
        deletions=1,
        labels=("workflow",),
        latest_reviews=(),
        review_requests=(),
        status_checks=(),
        raw={},
    )


def test_pr_context_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: _context())

    exit_code = cli.main(["pr-context", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"number": 123' in output
    assert '"head_ref_oid": "head123"' in output


def test_pr_context_json_error_output(monkeypatch, capsys):
    def fail(target, repo=None):
        raise GHCommandError(["gh", "pr", "view"], None, "GitHub CLI executable not found: gh", error="gh-not-found")

    monkeypatch.setattr(cli, "fetch_pr_context", fail)

    exit_code = cli.main(["pr-context", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"error": "gh-not-found"' in output
    assert '"returncode": null' in output


def test_catalog_marks_pr_context_implemented(capsys):
    exit_code = cli.main(["catalog"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "pr-context\tP1-high\tC-session\treport\tcore\timplemented" in output
