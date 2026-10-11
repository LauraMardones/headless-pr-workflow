import json

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


def test_parse_pr_context_merges_review_surfaces_and_preserves_source_metadata():
    context = parse_pr_context(
        {
            "baseRefName": "main",
            "headRefName": "feature",
            "headRefOid": "approved-sha",
            "latestReviews": [
                {
                    "author": {"login": "reviewer"},
                    "state": "APPROVED",
                    "submittedAt": "2026-04-21T11:00:00Z",
                    "commit": {"oid": ""},
                    "body": "",
                }
            ],
            "number": 8,
            "reviews": [
                {
                    "author": {"login": "reviewer"},
                    "state": "APPROVED",
                    "submittedAt": "2026-04-21T11:00:00Z",
                    "commit": {"oid": "approved-sha"},
                    "body": "ship it",
                }
            ],
            "state": "OPEN",
            "title": "Merge review surfaces",
            "url": "https://github.com/owner/repo/pull/8",
        }
    )

    assert len(context.latest_reviews) == 1
    assert context.latest_reviews[0].commit_oid == "approved-sha"
    assert context.latest_reviews[0].body == "ship it"
    assert context.latest_reviews[0].source_surface == "reviews"
    assert context.latest_reviews[0].source_surfaces == ("latestReviews", "reviews")


def test_parse_pr_context_keeps_review_order_by_submission_time_across_surfaces():
    context = parse_pr_context(
        {
            "baseRefName": "main",
            "headRefName": "feature",
            "headRefOid": "head-two",
            "latestReviews": [
                {
                    "author": {"login": "reviewer-a"},
                    "state": "COMMENTED",
                    "submittedAt": "2026-04-21T11:00:00Z",
                    "commit": {"oid": "head-two"},
                    "body": "latest surface review",
                }
            ],
            "number": 9,
            "reviews": [
                {
                    "author": {"login": "reviewer-b"},
                    "state": "APPROVED",
                    "submittedAt": "2026-04-21T10:00:00Z",
                    "commit": {"oid": "head-one"},
                    "body": "older full review",
                }
            ],
            "state": "OPEN",
            "title": "Chronological review order",
            "url": "https://github.com/owner/repo/pull/9",
        }
    )

    assert [review.commit_oid for review in context.latest_reviews] == ["head-one", "head-two"]

def test_parse_pr_context_merges_complementary_reviews_when_one_surface_lacks_submitted_at():
    context = parse_pr_context(
        {
            "baseRefName": "main",
            "headRefName": "feature",
            "headRefOid": "head",
            "latestReviews": [
                {
                    "author": {"login": "maintainer"},
                    "state": "COMMENTED",
                    "commit": {"oid": "head"},
                    "body": "",
                }
            ],
            "number": 10,
            "reviews": [
                {
                    "author": {"login": "maintainer"},
                    "state": "COMMENTED",
                    "submittedAt": "2026-04-21T10:00:00Z",
                    "commit": {"oid": ""},
                    "body": "override body",
                }
            ],
            "state": "OPEN",
            "title": "Complementary reviews",
            "url": "https://github.com/owner/repo/pull/10",
        }
    )

    assert len(context.latest_reviews) == 1
    assert context.latest_reviews[0].submitted_at == "2026-04-21T10:00:00Z"
    assert context.latest_reviews[0].commit_oid == "head"
    assert context.latest_reviews[0].body == "override body"
    assert context.latest_reviews[0].source_surfaces == ("latestReviews", "reviews")
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


def test_fetch_required_status_check_context_reports_configured_checks(monkeypatch):
    class Result:
        returncode = 0
        stdout = '{"required_status_checks": {"checks": [{"context": "unit"}, {"context": "lint"}]}}'
        stderr = ""

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    required = pr_context.fetch_required_status_check_context("owner/repo", "main")

    assert required.names == ("unit", "lint")
    assert required.status == "configured"
    assert required.available is True


def test_fetch_required_status_check_context_reports_absent_protection(monkeypatch):
    class Result:
        returncode = 1
        stdout = '{"message":"Branch not protected","status":"404"}'
        stderr = "gh: Branch not protected (HTTP 404)"

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    required = pr_context.fetch_required_status_check_context("owner/repo", "main")

    assert required.names == ()
    assert required.status == "not_configured"
    assert required.message == "Branch not protected"


def test_fetch_required_status_check_context_reports_generic_404_as_unavailable(monkeypatch):
    class Result:
        returncode = 1
        stdout = '{"message":"Not Found","status":"404"}'
        stderr = "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    required = pr_context.fetch_required_status_check_context("owner/repo", "missing")

    assert required.names == ()
    assert required.status == "unavailable"
    assert required.available is False
    assert required.message == "Not Found"


def test_fetch_required_status_checks_treats_unavailable_protection_as_no_checks(monkeypatch):
    class Result:
        returncode = 1
        stdout = '{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":"403"}'
        stderr = "gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)"

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    required = pr_context.fetch_required_status_checks("owner/repo", "main")

    assert required == ()


def test_fetch_required_status_check_context_reports_unavailable_protection(monkeypatch):
    class Result:
        returncode = 1
        stdout = '{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":"403"}'
        stderr = "gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)"

    monkeypatch.setattr(pr_context.subprocess, "run", lambda command, **kwargs: Result())

    required = pr_context.fetch_required_status_check_context("owner/repo", "main")

    assert required.names == ()
    assert required.status == "unavailable"
    assert required.available is False


HEAD = "8e771923416dbcc1820f2dd359a262e8b0fc4a9d"
OLD = "a56a59201772551b814c656020cdbaef98b08bb0"
RAW_PR = {
    "number": 298,
    "title": "PR",
    "state": "OPEN",
    "url": "https://github.com/owner/repo/pull/298",
    "baseRefName": "main",
    "headRefName": "feature",
    "headRefOid": HEAD,
}
BOT = {"__typename": "Bot", "login": "chatgpt-codex-connector"}
COMMIT_NODE = {"__typename": "PullRequestCommit", "commit": {"oid": HEAD}}
FORCE_PUSH_NODE = {
    "__typename": "HeadRefForcePushedEvent",
    "createdAt": "2026-09-28T07:00:00Z",
    "beforeCommit": {"oid": OLD},
    "afterCommit": {"oid": HEAD},
}
COMMENT_NODE = {
    "__typename": "IssueComment",
    "url": "https://github.com/owner/repo/pull/298#issuecomment-1",
    "body": "Codex Review: Didn't find any major issues.",
    "createdAt": "2026-09-28T09:00:00Z",
    "author": BOT,
    "editor": {"__typename": "User", "login": "someone"},
}
REVIEW_NODE = {
    "__typename": "PullRequestReview",
    "url": "https://github.com/owner/repo/pull/298#pullrequestreview-1",
    "body": "### Codex Review",
    "state": "COMMENTED",
    "submittedAt": "2026-09-28T08:00:00Z",
    "author": BOT,
    "commit": {"oid": OLD},
}


def linked_issue_node(number=290, *, repository="owner/repo", labels=("type:task", "executor:claude-code-sonnet"), more_labels=False):
    return {
        "number": number,
        "repository": {"nameWithOwner": repository},
        "labels": {"pageInfo": {"hasNextPage": more_labels}, "nodes": [{"name": label} for label in labels]},
    }


def review_evidence(*, timeline=(), issues=(), more_issues=False, error=None):
    return {
        "error": error,
        "repository": "owner/repo",
        "timelineItems": list(timeline),
        "closingIssuesReferences": {"pageInfo": {"hasNextPage": more_issues}, "nodes": list(issues)},
    }


def graphql_page(*, timeline=(), issues=(), has_next_page=False, end_cursor=None, head=HEAD):
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "headRefOid": head,
                        "closingIssuesReferences": {"pageInfo": {"hasNextPage": False}, "nodes": list(issues)},
                        "timelineItems": {
                            "pageInfo": {"hasNextPage": has_next_page, "endCursor": end_cursor},
                            "nodes": list(timeline),
                        },
                    }
                }
            }
        }
    )


def fake_gh(monkeypatch, *graphql_results):
    """Answer `gh pr view` with RAW_PR and each `gh api graphql` call with the next result (stdout, or Result fields)."""

    calls = []
    pending = list(graphql_results)

    class Result:
        def __init__(self, stdout, returncode=0, stderr=""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["gh", "pr", "view"]:
            return Result(json.dumps(RAW_PR))
        result = pending.pop(0)
        return Result(result) if isinstance(result, str) else Result(**result)

    monkeypatch.setattr(pr_context.subprocess, "run", fake_run)
    return calls


def test_pr_context_fields_request_commits():
    assert "commits" in pr_context.PR_CONTEXT_FIELDS


def test_parse_pr_context_reads_timeline_commits_comments_and_linked_issues():
    context = parse_pr_context(
        {
            **RAW_PR,
            "commits": [{"oid": OLD}, {"oid": HEAD}],
            "reviewEvidence": review_evidence(
                timeline=(FORCE_PUSH_NODE, COMMIT_NODE, REVIEW_NODE, COMMENT_NODE),
                issues=(linked_issue_node(),),
            ),
        }
    )

    assert context.review_evidence_error is None
    assert [event.kind for event in context.timeline] == ["force-push", "commit", "review", "comment"]
    assert context.commit_oids == (OLD, HEAD)

    force_push, commit, review, comment = context.timeline
    assert (force_push.before_oid, force_push.oid) == (OLD, HEAD)
    assert commit.oid == HEAD
    assert (review.author, review.author_type, review.state, review.oid) == ("chatgpt-codex-connector", "Bot", "COMMENTED", OLD)
    assert context.conversation_comments == (comment,)
    assert comment.to_dict() == {
        "kind": "comment",
        "oid": None,
        "before_oid": None,
        "author": "chatgpt-codex-connector",
        "author_type": "Bot",
        "editor": "someone",
        "editor_type": "User",
        "body": "Codex Review: Didn't find any major issues.",
        "state": None,
        "created_at": "2026-09-28T09:00:00Z",
        "url": "https://github.com/owner/repo/pull/298#issuecomment-1",
    }

    assert [issue.to_dict() for issue in context.linked_issues] == [
        {"number": 290, "repository": "owner/repo", "labels": ["type:task", "executor:claude-code-sonnet"]}
    ]
    payload = context.to_dict()
    assert payload["commit_oids"] == [OLD, HEAD]
    assert payload["linked_issues"][0]["number"] == 290
    assert len(payload["conversation_comments"]) == 1
    assert payload["review_evidence_error"] is None


def test_parse_pr_context_unedited_comment_has_no_editor():
    context = parse_pr_context({**RAW_PR, "reviewEvidence": review_evidence(timeline=({**COMMENT_NODE, "editor": None},))})

    assert context.conversation_comments[0].editor is None
    assert context.conversation_comments[0].editor_type is None


def test_parse_pr_context_commit_oids_include_head_and_timeline_commits_without_duplicates():
    context = parse_pr_context({**RAW_PR, "reviewEvidence": review_evidence(timeline=(FORCE_PUSH_NODE, COMMIT_NODE))})

    assert context.commit_oids == (OLD, HEAD)


def test_parse_pr_context_ignores_linked_issues_of_other_repositories():
    context = parse_pr_context(
        {
            **RAW_PR,
            "reviewEvidence": review_evidence(issues=(linked_issue_node(7, repository="other/repo"), linked_issue_node(290))),
        }
    )

    assert [issue.number for issue in context.linked_issues] == [290]


def test_parse_pr_context_without_review_evidence_fails_closed():
    context = parse_pr_context(dict(RAW_PR))

    assert context.review_evidence_error == pr_context.REVIEW_EVIDENCE_NOT_FETCHED
    assert context.timeline == ()
    assert context.linked_issues == ()


def test_parse_pr_context_reports_incomplete_or_malformed_review_evidence():
    cases = {
        "GitHub GraphQL request failed": review_evidence(error="GitHub GraphQL request failed", timeline=(COMMENT_NODE,)),
        "linked issues could not be read completely": review_evidence(issues=(linked_issue_node(),), more_issues=True),
        "labels of linked issue #290 could not be read completely": review_evidence(
            issues=(linked_issue_node(more_labels=True),)
        ),
        "PR timeline contains an item that could not be parsed": review_evidence(timeline=({"__typename": "Unknown"},)),
        "PR timeline is missing from the fetched review evidence": {"error": None, "closingIssuesReferences": {}},
        "linked issues are missing from the fetched review evidence": {"error": None, "timelineItems": []},
        "a linked issue could not be parsed": review_evidence(issues=({"number": "290"},)),
    }

    for expected_error, evidence in cases.items():
        context = parse_pr_context({**RAW_PR, "reviewEvidence": evidence})

        assert context.review_evidence_error == expected_error
        assert context.timeline == ()
        assert context.linked_issues == ()


def test_fetch_pr_context_follows_timeline_pagination_to_the_end(monkeypatch):
    calls = fake_gh(
        monkeypatch,
        graphql_page(timeline=(COMMIT_NODE,), issues=(linked_issue_node(),), has_next_page=True, end_cursor="CURSOR1"),
        graphql_page(timeline=(COMMENT_NODE,)),
    )

    context = pr_context.fetch_pr_context("298", repo="owner/repo")

    assert context.review_evidence_error is None
    assert [event.kind for event in context.timeline] == ["commit", "comment"]
    assert [issue.number for issue in context.linked_issues] == [290]
    assert len(calls) == 3
    first_page, second_page = calls[1], calls[2]
    assert first_page[:3] == ["gh", "api", "graphql"]
    assert "owner=owner" in first_page and "name=repo" in first_page and "number=298" in first_page
    assert not any(argument.startswith("cursor=") for argument in first_page)
    assert "cursor=CURSOR1" in second_page


def test_fetch_pr_context_reports_incomplete_page_instead_of_a_partial_timeline(monkeypatch):
    fake_gh(monkeypatch, graphql_page(timeline=(COMMIT_NODE, COMMENT_NODE), has_next_page=True, end_cursor=None))

    context = pr_context.fetch_pr_context("298", repo="owner/repo")

    assert context.review_evidence_error == "PR timeline pagination could not be followed to the end"
    assert context.timeline == ()
    assert context.conversation_comments == ()


def test_fetch_pr_context_reports_failed_second_page_instead_of_a_partial_timeline(monkeypatch):
    fake_gh(
        monkeypatch,
        graphql_page(timeline=(COMMIT_NODE, COMMENT_NODE), has_next_page=True, end_cursor="CURSOR1"),
        {"stdout": "", "returncode": 1, "stderr": "HTTP 403: GraphQL is not available"},
    )

    context = pr_context.fetch_pr_context("298", repo="owner/repo")

    assert context.number == 298
    assert context.head_ref_oid == HEAD
    assert context.review_evidence_error == (
        "GitHub GraphQL request for the PR timeline and linked issues failed: HTTP 403: GraphQL is not available"
    )
    assert context.timeline == ()


def test_fetch_pr_context_reports_head_change_between_reads(monkeypatch):
    fake_gh(monkeypatch, graphql_page(timeline=(COMMIT_NODE,), head=OLD))

    context = pr_context.fetch_pr_context("298", repo="owner/repo")

    assert context.review_evidence_error == "PR head changed while the PR timeline was being read"


def test_fetch_pr_context_reports_unparseable_graphql_response(monkeypatch):
    fake_gh(monkeypatch, "not json")

    context = pr_context.fetch_pr_context("298", repo="owner/repo")

    assert context.review_evidence_error == (
        "GitHub GraphQL response for the PR timeline and linked issues could not be parsed"
    )


def test_fetch_review_evidence_stops_at_the_page_limit(monkeypatch):
    monkeypatch.setattr(pr_context, "REVIEW_EVIDENCE_PAGE_LIMIT", 2)
    fake_gh(
        monkeypatch,
        graphql_page(timeline=(COMMIT_NODE,), has_next_page=True, end_cursor="C1"),
        graphql_page(timeline=(COMMIT_NODE,), has_next_page=True, end_cursor="C2"),
    )

    evidence = pr_context.fetch_review_evidence(dict(RAW_PR))

    assert evidence == {"error": "PR timeline is longer than the page limit and was not read completely"}


def test_fetch_review_evidence_needs_a_parseable_pr_url():
    evidence = pr_context.fetch_review_evidence({"url": "https://example.test/pr/1", "headRefOid": HEAD})

    assert evidence == {"error": "PR URL could not be parsed, so the PR timeline and linked issues were not read"}
