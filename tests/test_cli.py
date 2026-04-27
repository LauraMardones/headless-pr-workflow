from headless_pr_workflow import cli
from headless_pr_workflow.github import GHCommandError
from headless_pr_workflow.github.pr_context import CheckSummary, PullRequestContext, ReviewSummary


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


def _approved_context() -> PullRequestContext:
    context = _context()
    return PullRequestContext(
        number=context.number,
        title=context.title,
        state=context.state,
        url=context.url,
        base_ref_name=context.base_ref_name,
        base_ref_oid=context.base_ref_oid,
        head_ref_name=context.head_ref_name,
        head_ref_oid=context.head_ref_oid,
        head_repository=context.head_repository,
        head_repository_owner=context.head_repository_owner,
        is_cross_repository=context.is_cross_repository,
        is_draft=context.is_draft,
        merge_state_status=context.merge_state_status,
        mergeable=context.mergeable,
        review_decision=context.review_decision,
        changed_files=context.changed_files,
        additions=context.additions,
        deletions=context.deletions,
        labels=context.labels,
        latest_reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid=context.head_ref_oid),),
        review_requests=context.review_requests,
        status_checks=context.status_checks,
        raw=context.raw,
    )


def _commented_context() -> PullRequestContext:
    context = _context()
    return PullRequestContext(
        number=context.number,
        title=context.title,
        state=context.state,
        url=context.url,
        base_ref_name=context.base_ref_name,
        base_ref_oid=context.base_ref_oid,
        head_ref_name=context.head_ref_name,
        head_ref_oid=context.head_ref_oid,
        head_repository=context.head_repository,
        head_repository_owner=context.head_repository_owner,
        is_cross_repository=context.is_cross_repository,
        is_draft=context.is_draft,
        merge_state_status=context.merge_state_status,
        mergeable=context.mergeable,
        review_decision=context.review_decision,
        changed_files=context.changed_files,
        additions=context.additions,
        deletions=context.deletions,
        labels=context.labels,
        latest_reviews=(ReviewSummary(author="reviewer", state="COMMENTED", submitted_at="2026-04-21T10:00:00Z", commit_oid=context.head_ref_oid),),
        review_requests=context.review_requests,
        status_checks=context.status_checks,
        raw=context.raw,
    )


def _stale_approved_context() -> PullRequestContext:
    context = _context()
    return PullRequestContext(
        number=context.number,
        title=context.title,
        state=context.state,
        url=context.url,
        base_ref_name=context.base_ref_name,
        base_ref_oid=context.base_ref_oid,
        head_ref_name=context.head_ref_name,
        head_ref_oid=context.head_ref_oid,
        head_repository=context.head_repository,
        head_repository_owner=context.head_repository_owner,
        is_cross_repository=context.is_cross_repository,
        is_draft=context.is_draft,
        merge_state_status=context.merge_state_status,
        mergeable=context.mergeable,
        review_decision=context.review_decision,
        changed_files=context.changed_files,
        additions=context.additions,
        deletions=context.deletions,
        labels=context.labels,
        latest_reviews=(ReviewSummary(author="reviewer", state="APPROVED", submitted_at="2026-04-21T10:00:00Z", commit_oid="old-head"),),
        review_requests=context.review_requests,
        status_checks=context.status_checks,
        raw=context.raw,
    )


def _ready_context() -> PullRequestContext:
    context = _approved_context()
    return PullRequestContext(
        number=context.number,
        title=context.title,
        state=context.state,
        url=context.url,
        base_ref_name=context.base_ref_name,
        base_ref_oid=context.base_ref_oid,
        head_ref_name=context.head_ref_name,
        head_ref_oid=context.head_ref_oid,
        head_repository=context.head_repository,
        head_repository_owner=context.head_repository_owner,
        is_cross_repository=context.is_cross_repository,
        is_draft=context.is_draft,
        merge_state_status=context.merge_state_status,
        mergeable=context.mergeable,
        review_decision=context.review_decision,
        changed_files=context.changed_files,
        additions=context.additions,
        deletions=context.deletions,
        labels=context.labels,
        latest_reviews=context.latest_reviews,
        review_requests=context.review_requests,
        status_checks=(
            CheckSummary(
                name="unit",
                workflow=None,
                status="COMPLETED",
                conclusion="SUCCESS",
                state=None,
                bucket="success",
                url="https://checks/unit",
            ),
        ),
        raw=context.raw,
    )


def _blocked_pre_merge_context() -> PullRequestContext:
    context = _stale_approved_context()
    return PullRequestContext(
        number=context.number,
        title=context.title,
        state="OPEN",
        url=context.url,
        base_ref_name=context.base_ref_name,
        base_ref_oid=context.base_ref_oid,
        head_ref_name=context.head_ref_name,
        head_ref_oid=context.head_ref_oid,
        head_repository=context.head_repository,
        head_repository_owner=context.head_repository_owner,
        is_cross_repository=context.is_cross_repository,
        is_draft=True,
        merge_state_status="DIRTY",
        mergeable="CONFLICTING",
        review_decision=context.review_decision,
        changed_files=context.changed_files,
        additions=context.additions,
        deletions=context.deletions,
        labels=context.labels,
        latest_reviews=context.latest_reviews,
        review_requests=context.review_requests,
        status_checks=(
            CheckSummary(
                name="unit",
                workflow=None,
                status="COMPLETED",
                conclusion="FAILURE",
                state=None,
                bucket="failure",
                url="https://checks/unit",
            ),
            CheckSummary(
                name="lint",
                workflow=None,
                status=None,
                conclusion=None,
                state="PENDING",
                bucket="pending",
                url="https://checks/lint",
            ),
        ),
        raw=context.raw,
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
    assert "approval-check\tP0-blocking\tF-review\thard-gate\tcore\timplemented" in output
    assert "review-sha\tP0-blocking\tF-review\thard-gate\tcore\timplemented" in output
    assert "pre-merge\tP0-blocking\tH-merge\thard-gate\tcore\timplemented" in output


def test_review_sha_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: _approved_context())

    exit_code = cli.main(["review-sha", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"head_ref_oid": "head123"' in output
    assert '"latest_approval_sha": "head123"' in output
    assert '"approval_status": "current"' in output
    assert '"hard_gate_passed": true' in output


def test_review_sha_json_output_fails_for_missing_approval(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: _commented_context())

    exit_code = cli.main(["review-sha", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"approval_status": "missing"' in output
    assert '"hard_gate_passed": false' in output


def test_review_sha_json_output_fails_for_stale_approval(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: _stale_approved_context())

    exit_code = cli.main(["review-sha", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"approval_status": "stale"' in output
    assert '"hard_gate_passed": false' in output


def test_review_sha_json_error_output(monkeypatch, capsys):
    def fail(target, repo=None):
        raise GHCommandError(["gh", "pr", "view"], 1, "not found")

    monkeypatch.setattr(cli, "fetch_pr_context", fail)

    exit_code = cli.main(["review-sha", "999", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"error": "gh-command-failed"' in output
    assert '"stderr": "not found"' in output


def test_approval_check_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: _approved_context())

    exit_code = cli.main(["approval-check", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"approval_status": "current"' in output
    assert '"approval_source": "formal"' in output
    assert '"hard_gate_passed": true' in output


def test_pre_merge_json_output_ready(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: _ready_context())
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")

    exit_code = cli.main(["pre-merge", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"hard_gate_passed": true' in output
    assert '"code": "required-checks-passing"' in output
    assert '"blocking_reasons": []' in output


def test_pre_merge_json_output_lists_all_blockers(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: _blocked_pre_merge_context())
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")

    exit_code = cli.main(["pre-merge", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"hard_gate_passed": false' in output
    assert '"PR is draft."' in output
    assert '"Latest formal approval applies to old-head, not current head head123."' in output
    assert '"Status check unit is failing (status=COMPLETED, conclusion=FAILURE)."' in output
    assert '"Status check lint is pending (state=PENDING)."' in output
    assert '"PR mergeable state is CONFLICTING."' in output
    assert '"PR merge state status is DIRTY."' in output


def test_pre_merge_json_output_blocks_empty_status_checks(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: _approved_context())
    monkeypatch.setattr(cli, "fetch_repo_default_branch", lambda repo=None: "main")

    exit_code = cli.main(["pre-merge", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"GitHub reported no status checks for the current head SHA."' in output
