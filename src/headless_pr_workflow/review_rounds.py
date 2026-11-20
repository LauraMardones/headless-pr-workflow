"""Report review requests, Codex reviews, P1 findings and fix rounds per merged PR.

Read-only: every call is a GitHub read. Counting is done by pure functions over
recorded GitHub REST payloads, so tests need no network. The definitions are in
docs/REVIEW-ROUNDS.md.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
from datetime import datetime
from typing import Any, Callable

from .github import GHCommandError
from .github.pr_context import _gh_env
from .review_policy import CODEX_BOT_LOGIN, CODEX_REVIEW_TRIGGER_PHRASE

COUNT_FIGURES = ("review_requests", "codex_reviews", "p1_findings", "fix_commits", "fix_rounds")
FLOW_FILTER = "orchestration-or-critical-path"
EXECUTABLE_ISSUE_LABELS = ("type:story", "type:task", "type:bug")
FLOW_LABELS = ("flow:orchestration", "cp:critical-path")

_P1_BADGE = re.compile(r"!\[P[01] Badge\]")
_CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+(?:[\w.-]+/[\w.-]+)?#(\d+)", re.IGNORECASE)

GhJson = Callable[[list[str]], Any]


def _is_codex(login: str | None) -> bool:
    return bool(login) and login.removesuffix("[bot]") == CODEX_BOT_LOGIN


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _commit_time(commit: dict[str, Any]) -> datetime | None:
    inner = commit.get("commit") or {}
    return _ts(((inner.get("committer") or {}).get("date")) or ((inner.get("author") or {}).get("date")))


def closing_issue_number(body: str | None) -> int | None:
    match = _CLOSING.search(body or "")
    return int(match.group(1)) if match else None


def is_p1_comment(comment: dict[str, Any]) -> bool:
    """A top-level Codex review comment carrying the P1 or P0 badge."""
    return (
        _is_codex((comment.get("user") or {}).get("login"))
        and not comment.get("in_reply_to_id")
        and bool(_P1_BADGE.search(comment.get("body") or ""))
    )


def count_pr(raw: dict[str, Any]) -> dict[str, Any]:
    """Count the figures of one merged PR from its recorded GitHub data."""
    pr = raw["pull"]
    reviews = [r for r in raw.get("reviews", []) if _is_codex((r.get("user") or {}).get("login"))]
    reviews.sort(key=lambda r: r.get("submitted_at") or "")
    requests = [
        c
        for c in raw.get("issue_comments", [])
        if (c.get("body") or "").lstrip().split("\n", 1)[0].strip().startswith(CODEX_REVIEW_TRIGGER_PHRASE)
    ]
    p1_by_review: dict[int, int] = {}
    p1_total = 0
    for comment in raw.get("review_comments", []):
        if is_p1_comment(comment):
            p1_total += 1
            key = comment.get("pull_request_review_id")
            p1_by_review[key] = p1_by_review.get(key, 0) + 1

    commits = [(t, c.get("sha")) for c in raw.get("commits", []) if (t := _commit_time(c)) is not None]
    commits.sort()
    first_review = _ts(reviews[0].get("submitted_at")) if reviews else None
    fix_commits = [c for c in commits if first_review and c[0] > first_review]

    # A fix round is one P1 review followed by new commits; the commits after a
    # review make one round, so rounds are the distinct first commits that follow
    # a P1 review.
    first_commit_after: set[str | None] = set()
    for review in reviews:
        if not p1_by_review.get(review.get("id")):
            continue
        submitted = _ts(review.get("submitted_at"))
        after = [c for c in commits if submitted and c[0] > submitted]
        if after:
            first_commit_after.add(after[0][1])

    created, merged = _ts(pr.get("created_at")), _ts(pr.get("merged_at"))
    issue = raw.get("closing_issue") or {}
    return {
        "number": pr["number"],
        "title": pr.get("title"),
        "closing_issue": issue.get("number") or closing_issue_number(pr.get("body")),
        "labels": sorted(issue.get("labels", [])),
        "review_requests": len(requests),
        "codex_reviews": len(reviews),
        "p1_findings": p1_total,
        "fix_commits": len(fix_commits),
        "fix_rounds": len(first_commit_after),
        "changed_files": pr.get("changed_files"),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "merged_at": pr.get("merged_at"),
        "time_to_merge_seconds": int((merged - created).total_seconds()) if created and merged else None,
    }


def matches_flow_filter(labels: list[str]) -> bool:
    return any(l in EXECUTABLE_ISSUE_LABELS for l in labels) and any(l in FLOW_LABELS for l in labels)


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    total = {f: sum(r[f] for r in rows) for f in COUNT_FIGURES}
    median = {f: (statistics.median([r[f] for r in rows]) if rows else 0) for f in COUNT_FIGURES}
    return {"total": total, "median": median}


def build_report(
    raws: list[dict[str, Any]],
    *,
    repo: str,
    selection: dict[str, Any],
    flow: str | None = None,
    skipped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [count_pr(raw) for raw in raws]
    if flow:
        rows = [r for r in rows if matches_flow_filter(r["labels"])]
    return {
        "repo": repo,
        "selection": selection,
        "filter": flow,
        "labels_read": sorted(set(EXECUTABLE_ISSUE_LABELS) | set(FLOW_LABELS)) if flow else [],
        "prs": rows,
        "skipped": skipped or [],
        "summary": summarize(rows),
    }


def parse_prs(value: str) -> list[int]:
    numbers: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if re.fullmatch(r"\d+-\d+", part):
            low, high = (int(x) for x in part.split("-"))
            if low > high:
                raise ValueError(f"invalid range: {part}")
            numbers.extend(range(low, high + 1))
        elif part.isdigit():
            numbers.append(int(part))
        else:
            raise ValueError(f"invalid PR selection: {part!r}")
    return list(dict.fromkeys(numbers))


def gh_json(args: list[str]) -> Any:
    command = ["gh", *args]
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError as error:
        raise GHCommandError(command, None, "GitHub CLI executable not found: gh.", error="gh-not-found") from error
    if result.returncode != 0:
        raise GHCommandError(command, result.returncode, result.stderr)
    text = result.stdout.strip() or "null"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # `gh api --paginate` prints one JSON array per page.
        decoder, pos, items = json.JSONDecoder(), 0, []
        try:
            while pos < len(text):
                value, end = decoder.raw_decode(text, pos)
                items.extend(value) if isinstance(value, list) else items.append(value)
                pos = end
                while pos < len(text) and text[pos].isspace():
                    pos += 1
        except json.JSONDecodeError as error:
            raise GHCommandError(command, result.returncode, f"unreadable gh output: {error}") from error
        return items


def last_merged_numbers(repo: str, count: int, *, fetch: GhJson = gh_json) -> list[int]:
    rows = fetch(["pr", "list", "--repo", repo, "--state", "merged", "--limit", str(count), "--json", "number"])
    return [row["number"] for row in rows]


def fetch_pr_data(repo: str, number: int, *, fetch: GhJson = gh_json, need_issue: bool = True) -> dict[str, Any] | None:
    """Read one PR's data; None when the number is not a merged PR."""
    try:
        pull = fetch(["api", f"repos/{repo}/pulls/{number}"])
    except GHCommandError as error:
        if "404" in error.stderr or "Not Found" in error.stderr:
            return None
        raise
    if not pull or not pull.get("merged_at"):
        return None
    paged = lambda path: fetch(["api", "--paginate", f"repos/{repo}/{path}"]) or []  # noqa: E731
    raw: dict[str, Any] = {
        "pull": {k: pull.get(k) for k in ("number", "title", "body", "created_at", "merged_at", "changed_files", "additions", "deletions")},
        "issue_comments": [{"body": c.get("body")} for c in paged(f"issues/{number}/comments")],
        "reviews": [
            {"id": r.get("id"), "user": {"login": (r.get("user") or {}).get("login")}, "submitted_at": r.get("submitted_at")}
            for r in paged(f"pulls/{number}/reviews")
        ],
        "review_comments": [
            {
                "user": {"login": (c.get("user") or {}).get("login")},
                "in_reply_to_id": c.get("in_reply_to_id"),
                "pull_request_review_id": c.get("pull_request_review_id"),
                "body": c.get("body") if is_p1_comment(c) else "",
            }
            for c in paged(f"pulls/{number}/comments")
        ],
        "commits": [
            {"sha": c.get("sha"), "commit": {"committer": {"date": ((c.get("commit") or {}).get("committer") or {}).get("date")}}}
            for c in paged(f"pulls/{number}/commits")
        ],
        "closing_issue": None,
    }
    issue_number = closing_issue_number(pull.get("body"))
    if issue_number and need_issue:
        try:
            issue = fetch(["api", f"repos/{repo}/issues/{issue_number}"])
            raw["closing_issue"] = {"number": issue_number, "labels": [l["name"] for l in issue.get("labels", [])]}
        except GHCommandError as error:
            if "404" not in error.stderr and "Not Found" not in error.stderr:
                raise
    return raw


def collect_report(
    repo: str,
    *,
    last: int | None = None,
    prs: str | None = None,
    flow: str | None = None,
    fetch: GhJson = gh_json,
) -> dict[str, Any]:
    if (last is None) == (prs is None):
        raise ValueError("exactly one of --last and --prs is required")
    if last is not None:
        if last < 1:
            raise ValueError("--last must be at least 1")
        numbers = last_merged_numbers(repo, last, fetch=fetch)
        selection = {"type": "last", "value": last}
    else:
        numbers = parse_prs(prs or "")
        selection = {"type": "prs", "value": prs}
    raws, skipped = [], []
    for number in numbers:
        raw = fetch_pr_data(repo, number, fetch=fetch)
        if raw is None:
            skipped.append({"number": number, "reason": "not a merged pull request"})
        else:
            raws.append(raw)
    return build_report(raws, repo=repo, selection=selection, flow=flow, skipped=skipped)


def format_report(report: dict[str, Any]) -> str:
    cols = ("number", *COUNT_FIGURES, "changed_files", "additions", "deletions", "time_to_merge_seconds")
    header = ["PR", "requests", "codex", "P1", "fix commits", "fix rounds", "files", "+", "-", "merge secs"]
    lines = ["\t".join(header)]
    for row in report["prs"]:
        lines.append("\t".join(str(row[c]) for c in cols))
    summary = report["summary"]
    for name in ("total", "median"):
        values = [summary[name][f] for f in COUNT_FIGURES]
        lines.append("\t".join([name, *map(str, values)]))
    if report["filter"]:
        lines.append(f"Labels read: {', '.join(report['labels_read'])}")
    lines.append("Counted PRs: " + (", ".join(f"#{r['number']}" for r in report["prs"]) or "none"))
    skipped = report["skipped"]
    lines.append("Skipped: " + (", ".join(f"#{s['number']} ({s['reason']})" for s in skipped) or "none"))
    return "\n".join(lines)
