import json

from headless_pr_workflow import cli
from headless_pr_workflow.github import GHCommandError
from headless_pr_workflow.github.review_threads import fetch_review_threads_for_context, summarize_review_threads

from tests.github_scenarios import build_pr_context


def review_thread(
    *,
    thread_id: str = "thread-1",
    path: str = "src/app.py",
    line: int = 10,
    is_resolved: bool = False,
    is_outdated: bool = False,
    comment_outdated: bool = False,
    commit_oid: str = "head123",
) -> dict:
    return {
        "id": thread_id,
        "path": path,
        "line": line,
        "startLine": None,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "comments": {
            "totalCount": 1,
            "nodes": [
                {
                    "id": f"{thread_id}-comment",
                    "body": "please adjust this",
                    "createdAt": "2026-04-21T10:00:00Z",
                    "updatedAt": "2026-04-21T10:00:00Z",
                    "path": path,
                    "line": line,
                    "originalLine": line,
                    "outdated": comment_outdated,
                    "author": {"login": "reviewer"},
                    "pullRequestReview": {
                        "state": "COMMENTED",
                        "author": {"login": "reviewer"},
                        "commit": {"oid": commit_oid},
                    },
                }
            ],
        },
    }


def test_active_unresolved_thread_blocks_current_head():
    context = build_pr_context(head_ref_oid="head123")
    summary = summarize_review_threads(context, (review_thread(commit_oid="head123"),))

    assert summary.hard_gate_passed is False
    assert summary.thread_counts == {"total": 1, "unresolved_blocking": 1, "resolved": 0, "outdated_or_superseded": 0}
    assert summary.unresolved_blocking_threads[0].classification == "unresolved_blocking"
    assert summary.unresolved_blocking_threads[0].blocking is True


def test_resolved_thread_is_reported_and_non_blocking():
    context = build_pr_context(head_ref_oid="head123")
    summary = summarize_review_threads(context, (review_thread(is_resolved=True),))

    assert summary.hard_gate_passed is True
    assert summary.resolved_threads[0].classification == "resolved"
    assert summary.resolved_threads[0].blocking is False


def test_outdated_thread_is_reported_and_non_blocking():
    context = build_pr_context(head_ref_oid="head123")
    summary = summarize_review_threads(context, (review_thread(is_outdated=True, commit_oid="old123"),))

    assert summary.hard_gate_passed is True
    assert summary.outdated_or_superseded_threads[0].classification == "outdated"
    assert summary.outdated_or_superseded_threads[0].blocking is False


def test_superseded_thread_is_reported_and_non_blocking():
    context = build_pr_context(head_ref_oid="head123")
    summary = summarize_review_threads(context, (review_thread(commit_oid="old123"),))

    assert summary.hard_gate_passed is True
    assert summary.outdated_or_superseded_threads[0].classification == "superseded"
    assert summary.outdated_or_superseded_threads[0].review_commit_oids == ("old123",)


def test_mixed_thread_state_keeps_only_current_unresolved_threads_blocking():
    context = build_pr_context(head_ref_oid="head123")
    summary = summarize_review_threads(
        context,
        (
            review_thread(thread_id="active", commit_oid="head123"),
            review_thread(thread_id="resolved", is_resolved=True, commit_oid="head123"),
            review_thread(thread_id="outdated", is_outdated=True, commit_oid="old123"),
            review_thread(thread_id="superseded", commit_oid="old456"),
        ),
    )

    assert summary.hard_gate_passed is False
    assert [thread.id for thread in summary.unresolved_blocking_threads] == ["active"]
    assert [thread.id for thread in summary.resolved_threads] == ["resolved"]
    assert [thread.id for thread in summary.outdated_or_superseded_threads] == ["outdated", "superseded"]


def test_no_thread_state_passes():
    context = build_pr_context(head_ref_oid="head123")
    summary = summarize_review_threads(context, ())

    assert summary.hard_gate_passed is True
    assert summary.thread_counts == {"total": 0, "unresolved_blocking": 0, "resolved": 0, "outdated_or_superseded": 0}


def test_unresolved_review_threads_json_cli_fails_for_active_blocker(monkeypatch, capsys):
    summary = summarize_review_threads(build_pr_context(head_ref_oid="head123"), (review_thread(commit_oid="head123"),))
    monkeypatch.setattr(cli, "fetch_review_thread_summary", lambda target, repo=None: summary)

    exit_code = cli.main(["unresolved-review-threads", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["hard_gate_passed"] is False
    assert output["unresolved_blocking_threads"][0]["classification"] == "unresolved_blocking"


def test_unresolved_review_threads_human_cli_passes_for_no_threads(monkeypatch, capsys):
    summary = summarize_review_threads(build_pr_context(head_ref_oid="head123"), ())
    monkeypatch.setattr(cli, "fetch_review_thread_summary", lambda target, repo=None: summary)

    exit_code = cli.main(["unresolved-review-threads", "123", "--repo", "owner/repo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "unresolved review threads: pass (no review threads found)" in output
    assert "hard gate passed: true" in output


def test_fetch_review_threads_uses_graphql_review_threads_surface(monkeypatch):
    context = build_pr_context(number=7, head_repository="owner/repo")
    calls = []

    class Result:
        returncode = 0
        stdout = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [review_thread()],
                            }
                        }
                    }
                }
            }
        )
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr("headless_pr_workflow.github.review_threads.subprocess.run", fake_run)

    raw_threads = fetch_review_threads_for_context(context)

    assert len(raw_threads) == 1
    command = calls[0][0]
    assert command[:3] == ["gh", "api", "graphql"]
    assert any("reviewThreads" in part for part in command)


def test_unresolved_review_threads_json_error_output(monkeypatch, capsys):
    def fail(target, repo=None):
        raise GHCommandError(["gh", "api", "graphql"], 1, "not found")

    monkeypatch.setattr(cli, "fetch_review_thread_summary", fail)

    exit_code = cli.main(["unresolved-review-threads", "999", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"error": "gh-command-failed"' in output
    assert '"stderr": "not found"' in output
