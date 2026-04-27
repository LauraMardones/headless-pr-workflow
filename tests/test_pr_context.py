from headless_pr_workflow.github import pr_context
from headless_pr_workflow.github.pr_context import parse_pr_context


def test_parse_pr_context_normalizes_core_fields():
    context = parse_pr_context(
        {
            "additions": 12,
            "baseRefName": "main",
            "baseRefOid": "base123",
            "changedFiles": 3,
            "closed": False,
            "createdAt": "2026-04-21T10:00:00Z",
            "deletions": 4,
            "headRefName": "feature/hpw",
            "headRefOid": "head456",
            "headRepository": {"nameWithOwner": "owner/repo"},
            "headRepositoryOwner": {"login": "owner"},
            "isCrossRepository": False,
            "isDraft": False,
            "labels": [{"name": "workflow"}],
            "latestReviews": [
                {
                    "author": {"login": "reviewer-a"},
                    "state": "COMMENTED",
                    "submittedAt": "2026-04-21T10:30:00Z",
                    "commit": {"oid": "old111"},
                    "body": "needs follow-up",
                },
                {
                    "author": {"login": "reviewer-b"},
                    "state": "APPROVED",
                    "submittedAt": "2026-04-21T11:00:00Z",
                    "commit": {"oid": "head456"},
                    "body": "",
                },
            ],
            "maintainerCanModify": True,
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "number": 42,
            "reviewDecision": "APPROVED",
            "reviewRequests": [{"login": "reviewer-c"}],
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS", "detailsUrl": "https://checks/unit"},
                {"context": "lint", "state": "PENDING", "targetUrl": "https://checks/lint"},
                {"name": "e2e", "status": "COMPLETED", "conclusion": "FAILURE", "detailsUrl": "https://checks/e2e"},
            ],
            "title": "Add workflow",
            "updatedAt": "2026-04-21T11:01:00Z",
            "url": "https://github.com/owner/repo/pull/42",
        }
    )

    assert context.number == 42
    assert context.title == "Add workflow"
    assert context.base_ref_name == "main"
    assert context.head_ref_oid == "head456"
    assert context.head_repository == "owner/repo"
    assert context.labels == ("workflow",)
    assert context.latest_reviews[0].body == "needs follow-up"
    assert context.latest_approval_sha == "head456"
    assert context.review_requests == ("reviewer-c",)
    assert context.check_counts == {"success": 1, "failure": 1, "pending": 1, "skipped": 0, "unknown": 0}


def test_parse_pr_context_handles_nested_status_rollup_nodes():
    context = parse_pr_context(
        {
            "baseRefName": "main",
            "headRefName": "feature",
            "headRefOid": "head789",
            "number": 7,
            "state": "OPEN",
            "statusCheckRollup": {
                "contexts": {
                    "nodes": [
                        {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"context": "legacy", "state": "ERROR"},
                    ]
                }
            },
            "title": "Nested rollup",
            "url": "https://github.com/owner/repo/pull/7",
        }
    )

    assert [check.name for check in context.status_checks] == ["build", "legacy"]
    assert context.check_counts["success"] == 1
    assert context.check_counts["failure"] == 1


def test_completed_without_success_conclusion_is_unknown():
    context = parse_pr_context(
        {
            "baseRefName": "main",
            "headRefName": "feature",
            "headRefOid": "head789",
            "number": 7,
            "state": "OPEN",
            "statusCheckRollup": [{"name": "build", "status": "COMPLETED"}],
            "title": "Ambiguous completed check",
            "url": "https://github.com/owner/repo/pull/7",
        }
    )

    assert context.check_counts["success"] == 0
    assert context.check_counts["unknown"] == 1


def test_head_repository_falls_back_to_owner_qualified_name():
    context = parse_pr_context(
        {
            "baseRefName": "main",
            "headRefName": "feature",
            "headRefOid": "head789",
            "headRepository": {"name": "repo", "nameWithOwner": ""},
            "headRepositoryOwner": {"login": "owner"},
            "number": 7,
            "state": "OPEN",
            "title": "Owner fallback",
            "url": "https://github.com/owner/repo/pull/7",
        }
    )

    assert context.head_repository == "owner/repo"


def test_parse_pr_context_prefers_reviews_for_commit_oid():
    context = parse_pr_context(
        {
            "baseRefName": "main",
            "headRefName": "feature",
            "headRefOid": "approved-sha",
            "latestReviews": [{"author": {"login": "reviewer"}, "state": "APPROVED", "commit": {"oid": ""}}],
            "number": 8,
            "reviews": [{"author": {"login": "reviewer"}, "state": "APPROVED", "commit": {"oid": "approved-sha"}}],
            "state": "OPEN",
            "title": "Prefer reviews",
            "url": "https://github.com/owner/repo/pull/8",
        }
    )

    assert context.latest_approval_sha == "approved-sha"


def test_fetch_pr_context_passes_per_process_safe_directory(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = '{"number": 1, "title": "PR", "state": "OPEN", "url": "https://example.test/pr/1", "baseRefName": "main", "headRefName": "feature", "headRefOid": "abc"}'
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr(pr_context.subprocess, "run", fake_run)

    context = pr_context.fetch_pr_context("1", repo="owner/repo")

    assert context.number == 1
    command, kwargs = calls[0]
    assert command[:4] == ["gh", "pr", "view", "1"]
    assert kwargs["env"]["GIT_CONFIG_COUNT"] >= "1"
    assert "safe.directory" in kwargs["env"].values()


def test_fetch_pr_context_reports_missing_gh(monkeypatch):
    def fake_run(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(pr_context.subprocess, "run", fake_run)

    try:
        pr_context.fetch_pr_context("1")
    except pr_context.GHCommandError as error:
        assert error.error == "gh-not-found"
        assert error.returncode is None
        assert "not found" in error.stderr
    else:
        raise AssertionError("expected GHCommandError")


def test_fetch_pr_context_reports_invalid_json(monkeypatch):
    class Result:
        returncode = 0
        stdout = "not json"
        stderr = ""

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    try:
        pr_context.fetch_pr_context("1")
    except pr_context.GHCommandError as error:
        assert error.error == "gh-invalid-json"
        assert error.returncode == 0
    else:
        raise AssertionError("expected GHCommandError")


def test_fetch_pr_context_reports_parse_failures(monkeypatch):
    class Result:
        returncode = 0
        stdout = '{"title": "missing number"}'
        stderr = ""

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    try:
        pr_context.fetch_pr_context("1")
    except pr_context.GHCommandError as error:
        assert error.error == "gh-parse-failed"
        assert error.returncode == 0
    else:
        raise AssertionError("expected GHCommandError")


def test_fetch_required_status_checks_returns_context_names(monkeypatch):
    class Result:
        returncode = 0
        stdout = '{"required_status_checks": {"contexts": ["unit", "lint"]}}'
        stderr = ""

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    required = pr_context.fetch_required_status_checks("owner/repo", "main")

    assert required == ("unit", "lint")


def test_fetch_required_status_checks_treats_unavailable_protection_as_no_checks(monkeypatch):
    class Result:
        returncode = 1
        stdout = '{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":"403"}'
        stderr = "gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)"

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    required = pr_context.fetch_required_status_checks("owner/repo", "main")

    assert required == ()
