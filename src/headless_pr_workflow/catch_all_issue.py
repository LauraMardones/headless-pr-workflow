"""Create or check a catch-all issue for work found outside a session's scope (issue #516)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .catch_all import CATCH_ALL_EPIC, CATCH_ALL_FEATURE
from .github.pr_context import _gh_env

GH_TIMEOUT_SECONDS = 60
KINDS = ("story", "bug")

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_GITHUB_FAILED = 2

_ISSUE_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/issues/(\d+)")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class CatchAllIssueError(Exception):
    """A check failed (exit 1) or GitHub could not be read or written (exit 2)."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class CatchAllIssueResult:
    action: str
    issue: int
    url: str
    kind: str
    checks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"action": self.action, "issue": self.issue, "url": self.url, "kind": self.kind}
        if self.action == "link":
            data["checks"] = self.checks
        return data

    def human(self) -> str:
        verb = "created" if self.action == "create" else "linked"
        return f"catch-all issue #{self.issue} {verb}: {self.url}"


def build_body(*, found_under: int, origin: str, reason: str) -> str:
    lines = [
        f"Parent epic: #{CATCH_ALL_EPIC}",
        f"Feature group: #{CATCH_ALL_FEATURE}",
        f"Found under: #{found_under}",
        f"Origin: {origin}",
        "",
        reason.strip(),
    ]
    return "\n".join(lines) + "\n"


def _validate_repo(repo: str) -> None:
    if not _REPO.fullmatch(repo or ""):
        raise CatchAllIssueError(f"invalid --repo {repo!r}: expected OWNER/REPO", EXIT_CHECK_FAILED)


def _validate_create_input(*, repo: str, found_under: object, origin: str, title: str, reason: str, kind: str) -> None:
    _validate_repo(repo)
    if not isinstance(found_under, int) or isinstance(found_under, bool) or found_under <= 0:
        raise CatchAllIssueError("--found-under must be an Epic issue number", EXIT_CHECK_FAILED)
    prefix = f"https://github.com/{repo}/"
    if not origin.startswith(prefix) or len(origin) == len(prefix) or re.search(r"\s", origin):
        raise CatchAllIssueError(f"--origin must be one link starting with {prefix}", EXIT_CHECK_FAILED)
    if not title.strip() or "\n" in title or "\r" in title:
        raise CatchAllIssueError("--title must be one non-empty line", EXIT_CHECK_FAILED)
    if not reason.strip():
        raise CatchAllIssueError("--reason must not be empty", EXIT_CHECK_FAILED)
    if kind not in KINDS:
        raise CatchAllIssueError(f"--kind must be one of {', '.join(KINDS)}", EXIT_CHECK_FAILED)


def _gh(args: list[str], *, call: str) -> str:
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            encoding="utf-8",
            check=False,
            env=_gh_env(),
            timeout=GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise CatchAllIssueError(f"{call} timed out after {GH_TIMEOUT_SECONDS} seconds", EXIT_GITHUB_FAILED) from exc
    except OSError as exc:
        raise CatchAllIssueError(f"{call} could not run: {exc}", EXIT_GITHUB_FAILED) from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        raise CatchAllIssueError(f"{call} failed with exit {result.returncode}{suffix}", EXIT_GITHUB_FAILED)
    return result.stdout or ""


def create_catch_all_issue(*, repo: str, found_under: int, origin: str, title: str, reason: str, kind: str = "story") -> CatchAllIssueResult:
    """Make exactly one issue; a failure is never retried, so it cannot leave a second issue."""

    _validate_create_input(repo=repo, found_under=found_under, origin=origin, title=title, reason=reason, kind=kind)
    body = build_body(found_under=found_under, origin=origin, reason=reason)
    out = _gh(
        ["issue", "create", "--repo", repo, "--title", title.strip(), "--body", body, "--label", f"type:{kind}"],
        call="gh issue create",
    )
    match = _ISSUE_URL.search(out)
    if match is None:
        raise CatchAllIssueError("gh issue create printed no issue URL; the issue number is unknown", EXIT_GITHUB_FAILED)
    return CatchAllIssueResult("create", int(match.group(0).rsplit("/", 1)[1]), match.group(0), kind)


def _header_value(body: str, key: str) -> str | None:
    for line in body.splitlines():
        if line.startswith(f"{key}:"):
            return line[len(key) + 1 :].strip()
    return None


def _line_number(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.match(r"#(\d+)(?!\d)", value)
    return int(match.group(1)) if match else None


def link_catch_all_issue(*, repo: str, issue: int) -> CatchAllIssueResult:
    """Check an existing issue as a catch-all issue; writes nothing."""

    _validate_repo(repo)
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise CatchAllIssueError("the issue must be a positive number", EXIT_CHECK_FAILED)
    out = _gh(["issue", "view", str(issue), "--repo", repo, "--json", "number,state,body,url,labels"], call="gh issue view")
    try:
        data = json.loads(out)
        state, body, url = data["state"], data["body"] or "", data["url"]
        if not (isinstance(state, str) and isinstance(body, str) and isinstance(url, str)):
            raise TypeError
    except (ValueError, KeyError, TypeError) as exc:
        raise CatchAllIssueError(f"gh issue view returned unreadable data for #{issue}", EXIT_GITHUB_FAILED) from exc

    if state != "OPEN":
        raise CatchAllIssueError(f"issue #{issue} is not open", EXIT_CHECK_FAILED)
    checks: list[str] = []
    for key, expected in (("Parent epic", CATCH_ALL_EPIC), ("Feature group", CATCH_ALL_FEATURE)):
        value = _header_value(body, key)
        if value is None:
            raise CatchAllIssueError(f"issue #{issue} has no `{key}:` line", EXIT_CHECK_FAILED)
        if _line_number(value) != expected:
            raise CatchAllIssueError(f"issue #{issue} `{key}:` line is not the catch-all issue", EXIT_CHECK_FAILED)
        checks.append(f"{key}-is-catch-all")
    for key in ("Found under", "Origin"):
        if not _header_value(body, key):
            raise CatchAllIssueError(f"issue #{issue} has no `{key}:` line", EXIT_CHECK_FAILED)
        checks.append(f"{key}-present")
    kind = "bug" if any(isinstance(l, dict) and l.get("name") == "type:bug" for l in data.get("labels") or []) else "story"
    return CatchAllIssueResult("link", issue, url, kind, ["open", *checks])
