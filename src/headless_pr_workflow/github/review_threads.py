"""GitHub review thread fetching and normalization."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from .pr_context import GHCommandError, PullRequestContext, _gh_env, fetch_pr_context


REVIEW_THREADS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$cursor:String) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          comments(first:100) {
            totalCount
            nodes {
              id
              body
              createdAt
              updatedAt
              path
              line
              originalLine
              outdated
              author {
                login
              }
              pullRequestReview {
                state
                author {
                  login
                }
                commit {
                  oid
                }
              }
            }
          }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class ReviewThreadComment:
    id: str
    author: str | None
    body: str | None
    created_at: str | None
    updated_at: str | None
    path: str | None
    line: int | None
    original_line: int | None
    outdated: bool
    review_state: str | None
    review_author: str | None
    review_commit_oid: str | None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "ReviewThreadComment":
        review = raw.get("pullRequestReview")
        return cls(
            id=str(raw.get("id") or ""),
            author=_login(raw.get("author")),
            body=raw.get("body"),
            created_at=raw.get("createdAt"),
            updated_at=raw.get("updatedAt"),
            path=raw.get("path"),
            line=_int_or_none(raw.get("line")),
            original_line=_int_or_none(raw.get("originalLine")),
            outdated=bool(raw.get("outdated")),
            review_state=_dict_get(review, "state"),
            review_author=_login(_dict_get(review, "author")),
            review_commit_oid=_dict_get(_dict_get(review, "commit"), "oid"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewThreadSummary:
    id: str
    path: str | None
    line: int | None
    start_line: int | None
    is_resolved: bool
    is_outdated: bool
    classification: str
    blocking: bool
    reason: str
    review_commit_oids: tuple[str, ...]
    comments: tuple[ReviewThreadComment, ...]
    comments_total_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "line": self.line,
            "start_line": self.start_line,
            "is_resolved": self.is_resolved,
            "is_outdated": self.is_outdated,
            "classification": self.classification,
            "blocking": self.blocking,
            "reason": self.reason,
            "review_commit_oids": list(self.review_commit_oids),
            "comments": [comment.to_dict() for comment in self.comments],
            "comments_total_count": self.comments_total_count,
        }


@dataclass(frozen=True)
class ReviewThreadGateSummary:
    number: int
    title: str
    url: str
    head_ref_oid: str
    threads: tuple[ReviewThreadSummary, ...]
    unresolved_blocking_threads: tuple[ReviewThreadSummary, ...]
    resolved_threads: tuple[ReviewThreadSummary, ...]
    outdated_or_superseded_threads: tuple[ReviewThreadSummary, ...]
    hard_gate_passed: bool

    @property
    def thread_counts(self) -> dict[str, int]:
        return {
            "total": len(self.threads),
            "unresolved_blocking": len(self.unresolved_blocking_threads),
            "resolved": len(self.resolved_threads),
            "outdated_or_superseded": len(self.outdated_or_superseded_threads),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "head_ref_oid": self.head_ref_oid,
            "threads": [thread.to_dict() for thread in self.threads],
            "unresolved_blocking_threads": [thread.to_dict() for thread in self.unresolved_blocking_threads],
            "resolved_threads": [thread.to_dict() for thread in self.resolved_threads],
            "outdated_or_superseded_threads": [thread.to_dict() for thread in self.outdated_or_superseded_threads],
            "thread_counts": self.thread_counts,
            "hard_gate_passed": self.hard_gate_passed,
        }


def fetch_review_thread_summary(target: str | None = None, *, repo: str | None = None) -> ReviewThreadGateSummary:
    """Fetch current PR context and live review-thread state from GitHub."""

    context = fetch_pr_context(target, repo=repo)
    raw_threads = fetch_review_threads_for_context(context, repo=repo)
    return summarize_review_threads(context, raw_threads)


def fetch_review_threads_for_context(context: PullRequestContext, *, repo: str | None = None) -> tuple[dict[str, Any], ...]:
    """Fetch raw GitHub review threads through GraphQL, preserving resolution state."""

    repository = repo or context.head_repository
    if not repository or "/" not in repository:
        raise GHCommandError(
            ["gh", "api", "graphql"],
            None,
            "Repository is required to fetch GitHub review threads.",
            error="repo-required",
        )

    owner, name = repository.split("/", 1)
    cursor: str | None = None
    threads: list[dict[str, Any]] = []

    while True:
        raw = _fetch_review_thread_page(owner=owner, name=name, number=context.number, cursor=cursor)
        try:
            review_threads = raw["data"]["repository"]["pullRequest"]["reviewThreads"]
        except (KeyError, TypeError) as error:
            raise GHCommandError(
                ["gh", "api", "graphql"],
                0,
                f"GitHub review-thread payload could not be parsed: {error}",
                error="gh-parse-failed",
            ) from error

        nodes = review_threads.get("nodes") or ()
        threads.extend(node for node in nodes if isinstance(node, dict))
        page_info = review_threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return tuple(threads)
        cursor = page_info.get("endCursor")
        if not cursor:
            raise GHCommandError(
                ["gh", "api", "graphql"],
                0,
                "GitHub review-thread payload reported another page without an end cursor.",
                error="gh-parse-failed",
            )


def summarize_review_threads(context: PullRequestContext, raw_threads: tuple[dict[str, Any], ...]) -> ReviewThreadGateSummary:
    """Normalize raw review threads and classify merge-blocking state."""

    threads = tuple(_summarize_thread(raw, head_ref_oid=context.head_ref_oid) for raw in raw_threads)
    unresolved_blocking = tuple(thread for thread in threads if thread.classification == "unresolved_blocking")
    resolved = tuple(thread for thread in threads if thread.classification == "resolved")
    outdated_or_superseded = tuple(thread for thread in threads if thread.classification in {"outdated", "superseded"})

    return ReviewThreadGateSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        head_ref_oid=context.head_ref_oid,
        threads=threads,
        unresolved_blocking_threads=unresolved_blocking,
        resolved_threads=resolved,
        outdated_or_superseded_threads=outdated_or_superseded,
        hard_gate_passed=not unresolved_blocking,
    )


def _fetch_review_thread_page(*, owner: str, name: str, number: int, cursor: str | None) -> dict[str, Any]:
    command = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-F",
        f"number={number}",
        "-f",
        f"query={REVIEW_THREADS_QUERY}",
    ]
    if cursor:
        command.extend(["-f", f"cursor={cursor}"])

    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError as error:
        raise GHCommandError(command, None, "GitHub CLI executable not found: gh. Install from https://cli.github.com and authenticate with 'gh auth login'.", error="gh-not-found") from error

    if result.returncode != 0:
        raise GHCommandError(command, result.returncode, result.stderr)

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GHCommandError(
            command,
            result.returncode,
            f"GitHub CLI returned invalid JSON: {error.msg}",
            error="gh-invalid-json",
        ) from error

    return raw


def _summarize_thread(raw: dict[str, Any], *, head_ref_oid: str) -> ReviewThreadSummary:
    comments_payload = raw.get("comments") if isinstance(raw.get("comments"), dict) else {}
    raw_comments = comments_payload.get("nodes") or ()
    comments = tuple(ReviewThreadComment.from_raw(comment) for comment in raw_comments if isinstance(comment, dict))
    review_commit_oids = _unique(comment.review_commit_oid for comment in comments if comment.review_commit_oid)
    is_resolved = bool(raw.get("isResolved"))
    is_outdated = bool(raw.get("isOutdated")) or any(comment.outdated for comment in comments)
    classification, blocking, reason = _classify_thread(
        is_resolved=is_resolved,
        is_outdated=is_outdated,
    )

    return ReviewThreadSummary(
        id=str(raw.get("id") or ""),
        path=raw.get("path"),
        line=_int_or_none(raw.get("line")),
        start_line=_int_or_none(raw.get("startLine")),
        is_resolved=is_resolved,
        is_outdated=is_outdated,
        classification=classification,
        blocking=blocking,
        reason=reason,
        review_commit_oids=review_commit_oids,
        comments=comments,
        comments_total_count=int(comments_payload.get("totalCount") or len(comments)),
    )


def _classify_thread(
    *,
    is_resolved: bool,
    is_outdated: bool,
) -> tuple[str, bool, str]:
    if is_resolved:
        return "resolved", False, "Thread is resolved on GitHub."
    if is_outdated:
        return "outdated", False, "Thread is outdated on GitHub and does not block the current head."
    return "unresolved_blocking", True, "Thread is unresolved and still applies to the current PR head."


def _unique(values: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _dict_get(raw: Any, key: str) -> Any:
    if not isinstance(raw, dict):
        return None
    return raw.get(key)


def _login(raw: Any) -> str | None:
    if isinstance(raw, dict):
        return raw.get("login")
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
