"""Resolve the review thread of a finding in code the PR did not change (issue #517).

The session that classified the finding replies in its thread with the two
reply lines below and calls this helper. It reads the PR, the thread and the
catch-all issue fresh, makes one `resolveReviewThread` mutation and re-reads the
thread. It decides nothing about the finding itself: the classification is the
session's (Feature #513 → Assumptions).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import catch_all_issue
from .catch_all_issue import CatchAllIssueError, link_catch_all_issue
from .github.review_threads import RESOLVE_THREAD_MUTATION

EXIT_OK = catch_all_issue.EXIT_OK
EXIT_CHECK_FAILED = catch_all_issue.EXIT_CHECK_FAILED
EXIT_GITHUB_FAILED = catch_all_issue.EXIT_GITHUB_FAILED

# The two reply lines, in this order, before any free text of the reply.
CLASSIFICATION_LINE = "Classification: Out of scope — code the PR did not add or change"
ISSUE_LINE_PREFIX = "Catch-all issue: #"
_ISSUE_LINE = re.compile(r"Catch-all issue: #(\d+)")


def reply_lines(issue: int) -> str:
    """The exact reply lines a session posts in the finding's thread."""

    return f"{CLASSIFICATION_LINE}\n{ISSUE_LINE_PREFIX}{issue}"


THREAD_QUERY = """
query($threadId: ID!) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      id
      isResolved
      pullRequest { number repository { nameWithOwner } }
      comments(first: 100) {
        pageInfo { hasNextPage }
        nodes { url body }
      }
    }
  }
}
"""

class CatchAllThreadError(Exception):
    def __init__(self, check: str, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.check = check
        self.exit_code = exit_code


@dataclass
class CatchAllThreadResult:
    pr: int
    thread: str
    issue: int
    resolved: bool = False
    url: str | None = None
    checks: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    exit_code: int = EXIT_OK

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": "resolve-catch-all-thread",
            "pr": self.pr,
            "thread": self.thread,
            "issue": self.issue,
            "resolved": self.resolved,
            "checks": dict(self.checks),
        }
        if self.error is not None:
            data["error"] = self.error
        return data

    def human(self) -> str:
        return f"catch-all thread resolved: {self.url or self.thread}"


def _gh_json(args: list[str], *, check: str, call: str) -> Any:
    try:
        out = catch_all_issue._gh(args, call=call)
    except CatchAllIssueError as exc:
        raise CatchAllThreadError(check, str(exc), EXIT_GITHUB_FAILED) from exc
    try:
        return json.loads(out)
    except ValueError as exc:
        raise CatchAllThreadError(check, f"{call} returned unreadable data", EXIT_GITHUB_FAILED) from exc


def _read_pr(repo: str, pr: int) -> dict[str, Any]:
    call = f"gh pr view {pr}"
    data = _gh_json(["pr", "view", str(pr), "--repo", repo, "--json", "number,state,headRefOid"], check="pr-open", call=call)
    if not (isinstance(data, dict) and data.get("number") == pr and isinstance(data.get("state"), str) and isinstance(data.get("headRefOid"), str)):
        raise CatchAllThreadError("pr-open", f"{call} returned unreadable data", EXIT_GITHUB_FAILED)
    return data


def _read_thread(thread: str, *, check: str) -> dict[str, Any]:
    call = "gh api graphql (review thread)"
    payload = _gh_json(["api", "graphql", "-f", f"query={THREAD_QUERY}", "-f", f"threadId={thread}"], check=check, call=call)
    node = ((payload.get("data") or {}).get("node") if isinstance(payload, dict) else None) or {}
    if not isinstance(payload, dict) or payload.get("errors") or not isinstance(node, dict):
        raise CatchAllThreadError(check, f"{call} could not read thread {thread}", EXIT_GITHUB_FAILED)
    if node.get("id") != thread:
        # Before the mutation an unknown id is a refused check; after it, the resolution is unconfirmed.
        code = EXIT_CHECK_FAILED if check == "thread-on-pr" else EXIT_GITHUB_FAILED
        raise CatchAllThreadError(check, f"{thread} is not a review thread", code)
    comments = node.get("comments") or {}
    nodes = comments.get("nodes") if isinstance(comments, dict) else None
    pr_node = node.get("pullRequest") or {}
    if (
        not isinstance(node.get("isResolved"), bool)
        or not isinstance(nodes, list)
        or not all(isinstance(c, dict) and isinstance(c.get("body"), str) for c in nodes)
        or not isinstance(pr_node, dict)
    ):
        raise CatchAllThreadError(check, f"{call} returned unreadable data for thread {thread}", EXIT_GITHUB_FAILED)
    if (comments.get("pageInfo") or {}).get("hasNextPage"):
        raise CatchAllThreadError(check, f"thread {thread} has more than 100 comments; not all could be read", EXIT_GITHUB_FAILED)
    return node


def _reply_names(body: str) -> int | None:
    """The issue number the reply lines name, None without both lines in order."""

    lines = [line.strip() for line in body.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if len(lines) < 2 or lines[0] != CLASSIFICATION_LINE:
        return None
    match = _ISSUE_LINE.fullmatch(lines[1])
    return int(match.group(1)) if match else None


def _check_reply(comments: list[dict[str, Any]], issue: int) -> None:
    replies = comments[1:]
    if not replies:
        if comments and _reply_names(comments[0]["body"]) is not None:
            raise CatchAllThreadError("reply-lines", "the reply lines are only in the thread's first comment", EXIT_CHECK_FAILED)
        raise CatchAllThreadError("reply-lines", "the thread has no reply", EXIT_CHECK_FAILED)
    named = [_reply_names(c["body"]) for c in replies]
    if issue in named:
        return
    others = sorted({n for n in named if n is not None})
    if others:
        listed = ", ".join(f"#{n}" for n in others)
        raise CatchAllThreadError("reply-lines", f"the reply names catch-all issue {listed}, not #{issue}", EXIT_CHECK_FAILED)
    if _reply_names(comments[0]["body"]) == issue:
        raise CatchAllThreadError("reply-lines", "the reply lines are only in the thread's first comment", EXIT_CHECK_FAILED)
    raise CatchAllThreadError("reply-lines", "no reply carries both reply lines", EXIT_CHECK_FAILED)


def resolve_catch_all_thread(*, repo: str, pr: int, thread: str, issue: int, head: str) -> CatchAllThreadResult:
    """Resolve the thread only when every check passes on fresh reads; never raises."""

    result = CatchAllThreadResult(pr=pr, thread=thread, issue=issue)
    try:
        _resolve(result, repo=repo, head=head)
    except CatchAllThreadError as exc:
        result.checks[exc.check] = "fail"
        result.error = f"{exc.check}: {exc}"
        result.exit_code = exc.exit_code
    return result


def _resolve(result: CatchAllThreadResult, *, repo: str, head: str) -> None:
    pr, thread, issue = result.pr, result.thread, result.issue
    try:
        catch_all_issue._validate_repo(repo)
    except CatchAllIssueError as exc:
        raise CatchAllThreadError("pr-open", str(exc), EXIT_CHECK_FAILED) from exc
    if not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
        raise CatchAllThreadError("pr-open", "the PR must be a positive number", EXIT_CHECK_FAILED)
    if not thread or not head:
        raise CatchAllThreadError("thread-on-pr" if not thread else "head-matches", "--thread and --head are required", EXIT_CHECK_FAILED)

    data = _read_pr(repo, pr)
    if data["state"] != "OPEN":
        raise CatchAllThreadError("pr-open", f"PR #{pr} is not open ({data['state']})", EXIT_CHECK_FAILED)
    result.checks["pr-open"] = "pass"
    if data["headRefOid"] != head:
        raise CatchAllThreadError("head-matches", f"PR #{pr} head is {data['headRefOid']}, not {head}", EXIT_CHECK_FAILED)
    result.checks["head-matches"] = "pass"

    node = _read_thread(thread, check="thread-on-pr")
    owner = node["pullRequest"]
    if owner.get("number") != pr or ((owner.get("repository") or {}).get("nameWithOwner") or "").lower() != repo.lower():
        raise CatchAllThreadError("thread-on-pr", f"thread {thread} does not belong to {repo}#{pr}", EXIT_CHECK_FAILED)
    result.checks["thread-on-pr"] = "pass"
    if node["isResolved"]:
        raise CatchAllThreadError("thread-unresolved", f"thread {thread} is already resolved", EXIT_CHECK_FAILED)
    result.checks["thread-unresolved"] = "pass"
    comments = node["comments"]["nodes"]
    result.url = comments[0].get("url") if comments else None
    _check_reply(comments, issue)
    result.checks["reply-lines"] = "pass"

    try:
        link_catch_all_issue(repo=repo, issue=issue)
    except CatchAllIssueError as exc:
        raise CatchAllThreadError("catch-all-issue", str(exc), exc.exit_code) from exc
    result.checks["catch-all-issue"] = "pass"

    # The one write. It is never retried: a re-run re-reads and finds the thread resolved.
    call = "gh api graphql (resolveReviewThread)"
    payload = _gh_json(
        ["api", "graphql", "-f", f"query={RESOLVE_THREAD_MUTATION}", "-f", f"threadId={thread}"],
        check="resolved-confirmed",
        call=call,
    )
    confirmed = ((payload.get("data") or {}).get("resolveReviewThread") or {}).get("thread") or {} if isinstance(payload, dict) else {}
    if not isinstance(payload, dict) or payload.get("errors") or confirmed.get("id") != thread or confirmed.get("isResolved") is not True:
        raise CatchAllThreadError("resolved-confirmed", f"GitHub did not confirm that thread {thread} is resolved", EXIT_GITHUB_FAILED)
    reread = _read_thread(thread, check="resolved-confirmed")
    if reread["isResolved"] is not True:
        raise CatchAllThreadError("resolved-confirmed", f"the re-read shows thread {thread} still unresolved", EXIT_GITHUB_FAILED)
    result.checks["resolved-confirmed"] = "pass"
    result.resolved = True
