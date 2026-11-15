"""Tests for `hpw resolve-catch-all-thread` (issue #517), against a fake `gh` subprocess boundary."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from headless_pr_workflow import catch_all, catch_all_issue, catch_all_thread
from headless_pr_workflow.cli import main
from tests.dispatcher_fakes import REPO_ROOT

REPO = "owner/repo"
PR = 7
HEAD = "a" * 40
THREAD = "PRRT_thread1"
ISSUE = 33
THREAD_URL = f"https://github.com/{REPO}/pull/{PR}#discussion_r1"
REPLY = catch_all_thread.reply_lines(ISSUE) + "\n\nThe defect is in a file this PR did not change."


def issue_body(*, epic: object = catch_all.CATCH_ALL_EPIC, feature: object = catch_all.CATCH_ALL_FEATURE, drop: str = "") -> str:
    lines = {
        "Parent epic": f"Parent epic: #{epic}",
        "Feature group": f"Feature group: #{feature}",
        "Found under": "Found under: #504",
        "Origin": f"Origin: {THREAD_URL}",
    }
    return "\n".join(v for k, v in lines.items() if k != drop) + "\n\nreason\n"


class FakeGh:
    """Stands in for `gh`: PR view, thread read, resolve mutation and issue view, each from a state file."""

    def __init__(self, state: Path) -> None:
        self.state = state
        self.pr: dict[str, Any] = {"number": PR, "state": "OPEN", "headRefOid": HEAD}
        self.thread: dict[str, Any] = {
            "id": THREAD,
            "isResolved": False,
            "pullRequest": {"number": PR, "repository": {"nameWithOwner": REPO}},
            "comments": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {"url": THREAD_URL, "body": "Finding: a defect in scripts/other.sh:12."},
                    {"url": THREAD_URL + "-2", "body": REPLY},
                ],
            },
        }
        self.issue: dict[str, Any] = {
            "number": ISSUE,
            "state": "OPEN",
            "body": issue_body(),
            "url": f"https://github.com/{REPO}/issues/{ISSUE}",
            "labels": [],
        }
        self.fail: dict[str, str] = {}  # call kind -> "error" | "timeout" | "garbage"
        self.mutation_response: dict[str, Any] | None = None
        self.mutation_resolves = True
        self.calls: list[str] = []
        self.timeouts: list[object] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "gh"
        args = argv[1:]
        self.timeouts.append(kwargs.get("timeout"))
        if args[:2] == ["pr", "view"]:
            kind = "pr"
        elif args[:2] == ["issue", "view"]:
            kind = "issue"
        elif args[:2] == ["api", "graphql"] and "resolveReviewThread" in " ".join(args):
            kind = "mutation"
        elif args[:2] == ["api", "graphql"]:
            kind = "thread"
        else:
            return subprocess.CompletedProcess(argv, 9, "", f"unexpected gh call: {args}\n")
        self.calls.append(kind)
        mode = self.fail.get(kind)
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, 60)
        if mode == "error":
            return subprocess.CompletedProcess(argv, 1, "", "HTTP 502\n")
        if mode == "garbage":
            return subprocess.CompletedProcess(argv, 0, "not json", "")
        if kind == "pr":
            out: Any = self.pr
        elif kind == "issue":
            out = self.issue
        elif kind == "thread":
            out = {"data": {"node": self.thread}}
        else:
            if self.mutation_resolves:
                self.thread["isResolved"] = True
            out = self.mutation_response or {"data": {"resolveReviewThread": {"thread": {"id": THREAD, "isResolved": True}}}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeGh:
    fake = FakeGh(tmp_path)
    monkeypatch.setattr(catch_all_issue.subprocess, "run", fake)
    return fake


def run(*extra: str, issue: int = ISSUE, head: str = HEAD) -> int:
    return main(
        ["resolve-catch-all-thread", str(PR), "--thread", THREAD, "--issue", str(issue), "--head", head, "--repo", REPO, *extra]
    )


def result_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


# AC 4: the passing case.


def test_resolves_once_and_confirms_by_a_re_read(gh: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("--json") == 0

    assert gh.calls == ["pr", "thread", "issue", "mutation", "thread"]
    assert gh.calls.count("mutation") == 1
    assert gh.thread["isResolved"] is True
    assert set(gh.timeouts) == {60}
    data = result_json(capsys)
    assert data == {
        "action": "resolve-catch-all-thread",
        "pr": PR,
        "thread": THREAD,
        "issue": ISSUE,
        "resolved": True,
        "checks": {
            "pr-open": "pass",
            "head-matches": "pass",
            "thread-on-pr": "pass",
            "thread-unresolved": "pass",
            "reply-lines": "pass",
            "catch-all-issue": "pass",
            "resolved-confirmed": "pass",
        },
    }


def test_human_output_is_one_line_with_the_thread_url(gh: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    assert run() == 0
    assert capsys.readouterr().out == f"catch-all thread resolved: {THREAD_URL}\n"


# AC 5: each refused condition exits 1, makes no mutation and leaves the thread unresolved.


def _other_pr(gh: FakeGh) -> None:
    gh.thread["pullRequest"] = {"number": PR + 1, "repository": {"nameWithOwner": REPO}}


def _other_repo(gh: FakeGh) -> None:
    gh.thread["pullRequest"] = {"number": PR, "repository": {"nameWithOwner": "someone/else"}}


def _resolved(gh: FakeGh) -> None:
    gh.thread["isResolved"] = True


def _closed_pr(gh: FakeGh) -> None:
    gh.pr["state"] = "CLOSED"


def _merged_pr(gh: FakeGh) -> None:
    gh.pr["state"] = "MERGED"


def _other_head(gh: FakeGh) -> None:
    gh.pr["headRefOid"] = "b" * 40


def _no_reply(gh: FakeGh) -> None:
    gh.thread["comments"]["nodes"] = gh.thread["comments"]["nodes"][:1]


def _reply(body: str):
    def set_reply(gh: FakeGh) -> None:
        gh.thread["comments"]["nodes"][1]["body"] = body

    return set_reply


def _lines_only_in_first_comment(gh: FakeGh) -> None:
    gh.thread["comments"]["nodes"][0]["body"] = REPLY
    gh.thread["comments"]["nodes"][1]["body"] = "Thanks, noted."


def _lines_only_in_first_comment_no_reply(gh: FakeGh) -> None:
    gh.thread["comments"]["nodes"] = [{"url": THREAD_URL, "body": REPLY}]


def _closed_issue(gh: FakeGh) -> None:
    gh.issue["state"] = "CLOSED"


def _issue(**kwargs: object):
    def set_issue(gh: FakeGh) -> None:
        gh.issue["body"] = issue_body(**kwargs)  # type: ignore[arg-type]

    return set_issue


def _not_a_thread(gh: FakeGh) -> None:
    gh.thread = None  # type: ignore[assignment]


REFUSED = [
    ("thread-of-another-pr", _other_pr, "thread-on-pr"),
    ("thread-of-another-repo", _other_repo, "thread-on-pr"),
    ("node-is-not-a-thread", _not_a_thread, "thread-on-pr"),
    ("already-resolved-thread", _resolved, "thread-unresolved"),
    ("closed-pr", _closed_pr, "pr-open"),
    ("merged-pr", _merged_pr, "pr-open"),
    ("head-differs", _other_head, "head-matches"),
    ("no-reply", _no_reply, "reply-lines"),
    ("reply-without-classification-line", _reply(f"{catch_all_thread.ISSUE_LINE_PREFIX}{ISSUE}"), "reply-lines"),
    ("reply-without-issue-line", _reply(catch_all_thread.CLASSIFICATION_LINE + "\nsee the issue"), "reply-lines"),
    ("reply-lines-in-wrong-order", _reply(f"{catch_all_thread.ISSUE_LINE_PREFIX}{ISSUE}\n{catch_all_thread.CLASSIFICATION_LINE}"), "reply-lines"),
    ("reply-paraphrased", _reply(f"Classification: out of scope (unchanged code)\n{catch_all_thread.ISSUE_LINE_PREFIX}{ISSUE}"), "reply-lines"),
    ("reply-after-free-text", _reply(f"See below.\n{catch_all_thread.reply_lines(ISSUE)}"), "reply-lines"),
    ("reply-names-another-issue", _reply(catch_all_thread.reply_lines(ISSUE + 1)), "reply-lines"),
    ("lines-only-in-first-comment", _lines_only_in_first_comment, "reply-lines"),
    ("lines-only-in-first-comment-no-reply", _lines_only_in_first_comment_no_reply, "reply-lines"),
    ("closed-issue", _closed_issue, "catch-all-issue"),
    ("issue-without-parent-epic", _issue(drop="Parent epic"), "catch-all-issue"),
    ("issue-without-feature-group", _issue(drop="Feature group"), "catch-all-issue"),
    ("issue-without-found-under", _issue(drop="Found under"), "catch-all-issue"),
    ("issue-without-origin", _issue(drop="Origin"), "catch-all-issue"),
    ("issue-parent-epic-not-catch-all", _issue(epic=504), "catch-all-issue"),
    ("issue-feature-group-not-catch-all", _issue(feature=513), "catch-all-issue"),
]


@pytest.mark.parametrize("name,setup,check", REFUSED, ids=[r[0] for r in REFUSED])
def test_refused_condition_exits_1_without_a_mutation(
    gh: FakeGh, name: str, setup, check: str, capsys: pytest.CaptureFixture[str]
) -> None:
    setup(gh)
    was_resolved = bool(gh.thread and gh.thread["isResolved"])

    assert run("--json") == 1

    assert "mutation" not in gh.calls
    if gh.thread is not None:
        assert gh.thread["isResolved"] is was_resolved
    data = result_json(capsys)
    assert data["resolved"] is False
    assert data["checks"][check] == "fail"
    assert data["error"].startswith(f"{check}:")


def test_a_refusal_prints_one_reason_line_on_stderr(gh: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    _other_head(gh)
    assert run() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "head-matches" in captured.err


def test_a_re_run_after_success_finds_the_thread_resolved_and_writes_nothing(gh: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    assert run() == 0
    gh.calls.clear()
    capsys.readouterr()

    assert run("--json") == 1
    assert "mutation" not in gh.calls
    assert result_json(capsys)["checks"]["thread-unresolved"] == "fail"


# AC 6: unreadable reads, a timeout and an unconfirmed mutation exit 2 and never retry.


@pytest.mark.parametrize("kind", ["pr", "thread", "issue"])
@pytest.mark.parametrize("mode", ["error", "garbage", "timeout"])
def test_an_unreadable_or_timed_out_read_exits_2_without_a_mutation(
    gh: FakeGh, kind: str, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.fail[kind] = mode

    assert run("--json") == 2

    assert "mutation" not in gh.calls
    assert gh.thread["isResolved"] is False
    data = result_json(capsys)
    assert data["resolved"] is False
    assert "gh " in data["error"]
    if mode == "timeout":
        assert "timed out after 60 seconds" in data["error"]


def test_a_thread_with_more_comments_than_one_page_is_unreadable(gh: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    gh.thread["comments"]["pageInfo"]["hasNextPage"] = True
    assert run("--json") == 2
    assert "mutation" not in gh.calls


@pytest.mark.parametrize("mode", ["error", "timeout", "garbage"])
def test_a_failed_mutation_exits_2_and_is_not_retried(gh: FakeGh, mode: str, capsys: pytest.CaptureFixture[str]) -> None:
    gh.fail["mutation"] = mode

    assert run("--json") == 2

    assert gh.calls.count("mutation") == 1
    assert gh.calls[-1] == "mutation"
    data = result_json(capsys)
    assert data["resolved"] is False
    assert data["checks"]["resolved-confirmed"] == "fail"


@pytest.mark.parametrize(
    "response",
    [
        {"data": {"resolveReviewThread": {"thread": {"id": THREAD, "isResolved": False}}}},
        {"data": {"resolveReviewThread": {"thread": {"id": "PRRT_other", "isResolved": True}}}},
        {"errors": [{"message": "forbidden"}], "data": None},
    ],
    ids=["not-resolved", "other-thread", "errors"],
)
def test_an_unconfirmed_mutation_exits_2_and_is_not_retried(
    gh: FakeGh, response: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    gh.mutation_response = response
    gh.mutation_resolves = False

    assert run("--json") == 2

    assert gh.calls == ["pr", "thread", "issue", "mutation"]
    assert result_json(capsys)["checks"]["resolved-confirmed"] == "fail"


def test_a_re_read_that_shows_the_thread_unresolved_exits_2(gh: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    gh.mutation_resolves = False

    assert run("--json") == 2

    assert gh.calls == ["pr", "thread", "issue", "mutation", "thread"]
    data = result_json(capsys)
    assert data["resolved"] is False
    assert "re-read" in data["error"]


def test_an_unreadable_re_read_exits_2_and_is_not_retried(gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    original = gh.__call__

    def fail_after_mutation(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "mutation" in gh.calls:
            gh.fail["thread"] = "error"
        return original(argv, **kwargs)

    monkeypatch.setattr(catch_all_issue.subprocess, "run", fail_after_mutation)

    assert run("--json") == 2
    assert gh.calls.count("mutation") == 1
    assert result_json(capsys)["checks"]["resolved-confirmed"] == "fail"


def test_a_re_read_that_returns_no_thread_exits_2(gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    original = gh.__call__

    def lose_thread_after_mutation(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        out = original(argv, **kwargs)
        if gh.calls[-1] == "mutation":
            gh.thread = None  # type: ignore[assignment]
        return out

    monkeypatch.setattr(catch_all_issue.subprocess, "run", lose_thread_after_mutation)

    assert run("--json") == 2
    assert gh.calls.count("mutation") == 1
    assert result_json(capsys)["checks"]["resolved-confirmed"] == "fail"


@pytest.mark.parametrize(
    "argv",
    [
        ["resolve-catch-all-thread", str(PR), "--thread", THREAD, "--issue", str(ISSUE), "--head", HEAD],
        ["resolve-catch-all-thread", "--thread", THREAD, "--issue", str(ISSUE), "--head", HEAD, "--repo", REPO],
        ["resolve-catch-all-thread", str(PR), "--issue", str(ISSUE), "--head", HEAD, "--repo", REPO],
        ["resolve-catch-all-thread", str(PR), "--thread", THREAD, "--head", HEAD, "--repo", REPO],
        ["resolve-catch-all-thread", str(PR), "--thread", THREAD, "--issue", str(ISSUE), "--repo", REPO],
    ],
    ids=["no-repo", "no-pr", "no-thread", "no-issue", "no-head"],
)
def test_missing_input_exits_1_before_any_github_call(gh: FakeGh, argv: list[str]) -> None:
    assert main(argv) == 1
    assert gh.calls == []


# AC 7: one place each for the reply lines, the numbers and the issue check.


def test_the_module_reuses_the_catch_all_numbers_check_and_mutation() -> None:
    source = (REPO_ROOT / "src/headless_pr_workflow/catch_all_thread.py").read_text(encoding="utf-8")
    assert "from .catch_all_issue import CatchAllIssueError, link_catch_all_issue" in source
    assert "from .github.review_threads import RESOLVE_THREAD_MUTATION" in source
    assert "resolveReviewThread(input" not in source
    for number in (catch_all.CATCH_ALL_EPIC, catch_all.CATCH_ALL_FEATURE):
        assert not re.search(rf"(?<![0-9]){number}(?![0-9])", source)
    bookkeeping = (REPO_ROOT / "src/headless_pr_workflow/codex_review_bookkeeping.py").read_text(encoding="utf-8")
    assert "resolveReviewThread(input" not in bookkeeping


def _command_and_script_files() -> list[Path]:
    files = sorted((REPO_ROOT / ".claude/commands").rglob("*.md"))
    files += sorted(p for p in (REPO_ROOT / "scripts").rglob("*") if p.is_file())
    return files


@pytest.mark.parametrize("number", [catch_all.CATCH_ALL_EPIC, catch_all.CATCH_ALL_FEATURE])
def test_no_command_file_or_script_holds_a_catch_all_number(number: int) -> None:
    # A citation of the spec the Epic was written from (`spec #<n>`) names the spec, not the issue the helpers check.
    pattern = re.compile(rf"(?<!spec #)(?<![0-9]){number}(?![0-9])")
    offenders = []
    for path in _command_and_script_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


@pytest.mark.parametrize("name", [".claude/commands/review.md", ".claude/commands/implement.md"])
def test_command_files_copy_the_reply_lines_verbatim(name: str) -> None:
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    assert catch_all_thread.reply_lines(0).replace("#0", "#<n>").replace("\n", "\n     ") in text
    assert "hpw resolve-catch-all-thread" in text
