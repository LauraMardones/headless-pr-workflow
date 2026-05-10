"""Read-only local Git worktree status reporting."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STALE_TRACKING_WARNING = "ahead/behind counts use the local upstream tracking ref and may be stale until fetch"


@dataclass(frozen=True)
class GitCommandError(Exception):
    command: tuple[str, ...]
    returncode: int
    stderr: str


@dataclass(frozen=True)
class BranchStatus:
    name: str | None
    detached: bool
    upstream: str | None
    upstream_sha: str | None
    ahead: int | None
    behind: int | None
    tracking_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "detached": self.detached,
            "upstream": self.upstream,
            "upstream_sha": self.upstream_sha,
            "ahead": self.ahead,
            "behind": self.behind,
            "tracking_status": self.tracking_status,
        }


@dataclass(frozen=True)
class FileStatus:
    clean: bool
    staged: tuple[str, ...]
    unstaged: tuple[str, ...]
    untracked: tuple[str, ...]
    conflicted: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "staged": list(self.staged),
            "unstaged": list(self.unstaged),
            "untracked": list(self.untracked),
            "conflicted": list(self.conflicted),
        }


@dataclass(frozen=True)
class UnpushedCommit:
    sha: str
    subject: str

    def to_dict(self) -> dict[str, str]:
        return {"sha": self.sha, "subject": self.subject}


@dataclass(frozen=True)
class LinkedWorktree:
    path: str
    branch: str | None
    head_sha: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {"path": self.path, "branch": self.branch, "head_sha": self.head_sha}


@dataclass(frozen=True)
class WorktreeStatusSummary:
    command: str
    ok: bool
    path: str
    repository_root: str | None
    worktree_path: str | None
    head_sha: str | None
    branch: BranchStatus
    status: FileStatus
    unpushed_commits: tuple[UnpushedCommit, ...]
    linked_worktrees: tuple[LinkedWorktree, ...]
    branch_in_use_by_other_worktree: bool | None
    warnings: tuple[str, ...]
    error: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "ok": self.ok,
            "path": self.path,
            "repository_root": self.repository_root,
            "worktree_path": self.worktree_path,
            "head_sha": self.head_sha,
            "branch": self.branch.to_dict(),
            "status": self.status.to_dict(),
            "unpushed_commits": [commit.to_dict() for commit in self.unpushed_commits],
            "linked_worktrees": [worktree.to_dict() for worktree in self.linked_worktrees],
            "branch_in_use_by_other_worktree": self.branch_in_use_by_other_worktree,
            "warnings": list(self.warnings),
            "error": self.error,
        }


def summarize_worktree_status(path: str | None = None) -> WorktreeStatusSummary:
    inspected_path = str(Path(path or ".").resolve())
    try:
        repository_root = _git(inspected_path, "rev-parse", "--show-toplevel")
        worktree_path = _git(inspected_path, "rev-parse", "--show-toplevel")
    except GitCommandError as error:
        return _inspection_error(inspected_path, error)

    head_sha = _git_optional(inspected_path, "rev-parse", "--verify", "HEAD")
    branch_name = _git_optional(inspected_path, "symbolic-ref", "--quiet", "--short", "HEAD")
    detached = branch_name is None and head_sha is not None
    upstream = _git_optional(inspected_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    upstream_sha = _git_optional(inspected_path, "rev-parse", "--verify", "@{upstream}") if upstream else None
    ahead, behind = _ahead_behind(inspected_path, has_head=head_sha is not None, has_upstream=upstream is not None)

    warnings: list[str] = []
    if upstream:
        warnings.append(STALE_TRACKING_WARNING)
    elif detached:
        warnings.append("detached HEAD has no upstream tracking branch")
    else:
        warnings.append("current branch has no upstream tracking branch")
    if head_sha is None:
        warnings.append("HEAD is unborn or unavailable")

    branch = BranchStatus(
        name=branch_name,
        detached=detached,
        upstream=upstream,
        upstream_sha=upstream_sha,
        ahead=ahead,
        behind=behind,
        tracking_status=_tracking_status(head_sha=head_sha, detached=detached, upstream=upstream),
    )
    file_status = _file_status(inspected_path)
    unpushed_commits = _unpushed_commits(inspected_path, has_head=head_sha is not None, has_upstream=upstream is not None)
    linked_worktrees = _linked_worktrees(inspected_path)
    branch_in_use = _branch_in_use_by_other_worktree(
        current_worktree=worktree_path,
        branch_name=branch_name,
        linked_worktrees=linked_worktrees,
    )

    return WorktreeStatusSummary(
        command="worktree-status",
        ok=True,
        path=inspected_path,
        repository_root=repository_root,
        worktree_path=worktree_path,
        head_sha=head_sha,
        branch=branch,
        status=file_status,
        unpushed_commits=unpushed_commits,
        linked_worktrees=linked_worktrees,
        branch_in_use_by_other_worktree=branch_in_use,
        warnings=tuple(warnings),
        error=None,
    )


def _inspection_error(path: str, error: GitCommandError) -> WorktreeStatusSummary:
    return WorktreeStatusSummary(
        command="worktree-status",
        ok=False,
        path=path,
        repository_root=None,
        worktree_path=None,
        head_sha=None,
        branch=BranchStatus(
            name=None,
            detached=False,
            upstream=None,
            upstream_sha=None,
            ahead=None,
            behind=None,
            tracking_status="unknown",
        ),
        status=FileStatus(clean=False, staged=(), unstaged=(), untracked=(), conflicted=()),
        unpushed_commits=(),
        linked_worktrees=(),
        branch_in_use_by_other_worktree=None,
        warnings=(),
        error={
            "type": "git-inspection-failed",
            "message": error.stderr or "unable to inspect local Git state",
            "command": list(error.command),
            "returncode": error.returncode,
        },
    )


def _git(path: str, *args: str) -> str:
    command = ("git", "-C", path, *args)
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitCommandError(command=command, returncode=result.returncode, stderr=result.stderr.strip())
    return result.stdout.strip()


def _git_optional(path: str, *args: str) -> str | None:
    try:
        value = _git(path, *args)
    except GitCommandError:
        return None
    return value or None


def _ahead_behind(path: str, *, has_head: bool, has_upstream: bool) -> tuple[int | None, int | None]:
    if not has_head or not has_upstream:
        return None, None
    output = _git_optional(path, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
    if output is None:
        return None, None
    behind_text, ahead_text = output.split()
    return int(ahead_text), int(behind_text)


def _tracking_status(*, head_sha: str | None, detached: bool, upstream: str | None) -> str:
    if head_sha is None:
        return "unborn"
    if detached:
        return "detached"
    if upstream is None:
        return "missing_upstream"
    return "tracking"


def _file_status(path: str) -> FileStatus:
    output = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    conflicted: list[str] = []
    entries = output.split("\0") if output else []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        code = entry[:2]
        file_path = entry[3:]
        if code.startswith("R") or code.startswith("C"):
            index += 1
        if code == "??":
            untracked.append(file_path)
        elif _is_conflicted(code):
            conflicted.append(file_path)
        else:
            if code[0] not in (" ", "?", "!"):
                staged.append(file_path)
            if code[1] not in (" ", "?", "!"):
                unstaged.append(file_path)
    clean = not (staged or unstaged or untracked or conflicted)
    return FileStatus(
        clean=clean,
        staged=tuple(sorted(staged)),
        unstaged=tuple(sorted(unstaged)),
        untracked=tuple(sorted(untracked)),
        conflicted=tuple(sorted(conflicted)),
    )


def _is_conflicted(code: str) -> bool:
    return code in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _unpushed_commits(path: str, *, has_head: bool, has_upstream: bool) -> tuple[UnpushedCommit, ...]:
    if not has_head or not has_upstream:
        return ()
    output = _git_optional(path, "log", "--format=%H%x00%s", "@{upstream}..HEAD")
    if output is None:
        return ()
    parts = output.split("\0") if output else []
    commits: list[UnpushedCommit] = []
    for index in range(0, len(parts) - 1, 2):
        sha = parts[index].strip()
        subject = parts[index + 1].strip()
        if sha:
            commits.append(UnpushedCommit(sha=sha, subject=subject))
    return tuple(commits)


def _linked_worktrees(path: str) -> tuple[LinkedWorktree, ...]:
    output = _git_optional(path, "worktree", "list", "--porcelain")
    if output is None:
        return ()
    worktrees: list[LinkedWorktree] = []
    current: dict[str, str | None] = {}
    for line in output.splitlines():
        if not line:
            if current:
                worktrees.append(_linked_worktree_from_block(current))
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head_sha"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["branch"] = None
    if current:
        worktrees.append(_linked_worktree_from_block(current))
    return tuple(worktrees)


def _linked_worktree_from_block(block: dict[str, str | None]) -> LinkedWorktree:
    return LinkedWorktree(
        path=block.get("path") or "",
        branch=block.get("branch"),
        head_sha=block.get("head_sha"),
    )


def _branch_in_use_by_other_worktree(
    *,
    current_worktree: str,
    branch_name: str | None,
    linked_worktrees: tuple[LinkedWorktree, ...],
) -> bool | None:
    if branch_name is None:
        return None
    current = _normalize_path(current_worktree)
    for worktree in linked_worktrees:
        if worktree.branch == branch_name and _normalize_path(worktree.path) != current:
            return True
    return False


def _normalize_path(path: str) -> str:
    return str(Path(path).resolve()).casefold()
