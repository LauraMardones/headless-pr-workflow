"""Conservative branch cleanup: identify and optionally delete stale merged branches."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .github.pr_context import GHCommandError, _gh_env
from .post_merge_sync import PRMergeState, fetch_post_merge_pr_state
from .worktree_status import GitCommandError, LinkedWorktree, _file_status, _git, _git_optional, _linked_worktrees


@dataclass(frozen=True)
class CleanupCandidate:
    branch: str
    type: str
    disposition: str
    reason: str | None
    worktree: str | None
    content_verified: bool
    ahead_by: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "type": self.type,
            "disposition": self.disposition,
            "reason": self.reason,
            "worktree": self.worktree,
            "content_verified": self.content_verified,
            "ahead_by": self.ahead_by,
        }


@dataclass(frozen=True)
class WorktreeInfo:
    path: str
    branch: str | None
    dirty: bool

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "branch": self.branch, "dirty": self.dirty}


@dataclass(frozen=True)
class BranchCleanupSummary:
    command: str
    mode: str
    ok: bool
    target_type: str
    number: int | None
    title: str | None
    url: str | None
    state: str | None
    merged: bool | None
    base_branch: str | None
    head_branch: str | None
    candidates: tuple[CleanupCandidate, ...]
    worktrees_checked: tuple[WorktreeInfo, ...]
    deleted: tuple[str, ...]
    kept: tuple[dict[str, str], ...]
    manual_commands: tuple[str, ...]
    warnings: tuple[str, ...]
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "mode": self.mode,
            "ok": self.ok,
            "target_type": self.target_type,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "state": self.state,
            "merged": self.merged,
            "base_branch": self.base_branch,
            "head_branch": self.head_branch,
            "candidates": [c.to_dict() for c in self.candidates],
            "worktrees_checked": [w.to_dict() for w in self.worktrees_checked],
            "deleted": list(self.deleted),
            "kept": list(self.kept),
            "manual_commands": list(self.manual_commands),
            "warnings": list(self.warnings),
            "blocking_reasons": list(self.blocking_reasons),
        }


def fetch_github_compare_ahead_by(repo: str, base: str, head: str) -> int | None:
    """Return ahead_by count from GitHub compare API, or None if unavailable."""
    command = ["gh", "api", f"repos/{repo}/compare/{base}...{head}"]
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        ahead = data.get("ahead_by")
        return int(ahead) if ahead is not None else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def find_local_branch(repo_path: str, branch: str) -> bool:
    """Return True if a local branch with this name exists."""
    output = _git_optional(repo_path, "branch", "--list", branch)
    return bool(output and output.strip())


def find_remote_tracking_branch(repo_path: str, branch: str, remote: str = "origin") -> bool:
    """Return True if a remote tracking ref origin/<branch> exists locally."""
    ref = f"{remote}/{branch}"
    output = _git_optional(repo_path, "branch", "-r", "--list", ref)
    return bool(output and output.strip())


def is_ancestry_merged(repo_path: str, branch: str, base_ref: str) -> bool:
    """Return True if branch appears in `git branch --merged <base_ref>`."""
    output = _git_optional(repo_path, "branch", "--merged", base_ref)
    if not output:
        return False
    for line in output.splitlines():
        if line.strip().lstrip("* ") == branch:
            return True
    return False


def has_unique_content(repo_path: str, branch: str, base_ref: str) -> bool:
    """Return True if branch has unique content not present in base_ref (unsafe to delete)."""
    try:
        diff = _git(repo_path, "diff", f"{base_ref}...{branch}")
        return bool(diff.strip())
    except GitCommandError:
        return True


def get_worktrees_for_branch(
    branch: str,
    linked_worktrees: tuple[LinkedWorktree, ...],
) -> list[LinkedWorktree]:
    """Return linked worktrees that have this branch checked out."""
    return [w for w in linked_worktrees if w.branch == branch]


def inspect_worktree_dirty(worktree_path: str) -> bool:
    """Return True if the worktree at path is dirty; True (conservative) on error."""
    try:
        file_status = _file_status(worktree_path)
        return not file_status.clean
    except GitCommandError:
        return True


def classify_local_candidate(
    repo_path: str,
    branch: str,
    base_ref: str,
    linked_worktrees: tuple[LinkedWorktree, ...],
) -> CleanupCandidate:
    """Classify a local branch as a cleanup candidate."""
    branch_worktrees = get_worktrees_for_branch(branch, linked_worktrees)
    if branch_worktrees:
        worktree_path = branch_worktrees[0].path
        dirty = inspect_worktree_dirty(worktree_path)
        if dirty:
            return CleanupCandidate(
                branch=branch,
                type="local",
                disposition="skipped",
                reason="branch checked out in dirty worktree",
                worktree=worktree_path,
                content_verified=False,
                ahead_by=None,
            )
        return CleanupCandidate(
            branch=branch,
            type="local",
            disposition="kept",
            reason="branch currently checked out in a worktree",
            worktree=worktree_path,
            content_verified=False,
            ahead_by=None,
        )

    if is_ancestry_merged(repo_path, branch, base_ref):
        return CleanupCandidate(
            branch=branch,
            type="local",
            disposition="safe_to_delete",
            reason=None,
            worktree=None,
            content_verified=False,
            ahead_by=None,
        )

    unique = has_unique_content(repo_path, branch, base_ref)
    if not unique:
        return CleanupCandidate(
            branch=branch,
            type="local",
            disposition="safe_to_delete",
            reason=None,
            worktree=None,
            content_verified=True,
            ahead_by=None,
        )
    return CleanupCandidate(
        branch=branch,
        type="local",
        disposition="kept",
        reason="branch has unique content not present in base branch",
        worktree=None,
        content_verified=True,
        ahead_by=None,
    )


def classify_remote_candidate(
    branch: str,
    base_branch: str,
    repo: str,
    linked_worktrees: tuple[LinkedWorktree, ...],
) -> CleanupCandidate:
    """Classify a remote tracking branch as a cleanup candidate."""
    branch_worktrees = get_worktrees_for_branch(branch, linked_worktrees)
    if branch_worktrees:
        worktree_path = branch_worktrees[0].path
        return CleanupCandidate(
            branch=branch,
            type="remote",
            disposition="kept",
            reason="branch currently checked out in a worktree",
            worktree=worktree_path,
            content_verified=False,
            ahead_by=None,
        )

    ahead_by = fetch_github_compare_ahead_by(repo, base_branch, branch)
    if ahead_by is None:
        return CleanupCandidate(
            branch=branch,
            type="remote",
            disposition="skipped",
            reason="GitHub compare API unavailable; cannot verify remote branch safety",
            worktree=None,
            content_verified=False,
            ahead_by=None,
        )
    if ahead_by == 0:
        return CleanupCandidate(
            branch=branch,
            type="remote",
            disposition="safe_to_delete",
            reason=None,
            worktree=None,
            content_verified=True,
            ahead_by=ahead_by,
        )
    return CleanupCandidate(
        branch=branch,
        type="remote",
        disposition="kept",
        reason=f"remote branch is {ahead_by} commit(s) ahead of base branch",
        worktree=None,
        content_verified=True,
        ahead_by=ahead_by,
    )


def delete_local_branch(repo_path: str, branch: str, content_verified: bool) -> tuple[bool, str | None]:
    """Delete a local branch. Uses -D when content_verified (squash/rebase), -d otherwise."""
    flag = "-D" if content_verified else "-d"
    try:
        _git(repo_path, "branch", flag, branch)
        return True, None
    except GitCommandError as error:
        return False, error.stderr


def delete_remote_branch(repo_path: str, branch: str, remote: str = "origin") -> tuple[bool, str | None]:
    """Delete a remote branch via git push --delete."""
    try:
        _git(repo_path, "push", remote, "--delete", branch)
        return True, None
    except GitCommandError as error:
        return False, error.stderr


def _build_error_summary(
    *,
    target_type: str,
    error_message: str,
    mode: str,
    pr_state: PRMergeState | None = None,
) -> BranchCleanupSummary:
    return BranchCleanupSummary(
        command="branch-cleanup",
        mode=mode,
        ok=False,
        target_type=target_type,
        number=pr_state.number if pr_state else None,
        title=pr_state.title if pr_state else None,
        url=pr_state.url if pr_state else None,
        state=pr_state.state if pr_state else None,
        merged=pr_state.merged if pr_state else None,
        base_branch=pr_state.base_branch if pr_state else None,
        head_branch=pr_state.head_branch if pr_state else None,
        candidates=(),
        worktrees_checked=(),
        deleted=(),
        kept=(),
        manual_commands=(),
        warnings=(),
        blocking_reasons=(error_message,),
    )


def summarize_branch_cleanup(
    target: str,
    *,
    repo: str,
    mode: str = "dry_run",
    repo_path: str | None = None,
) -> BranchCleanupSummary:
    """Produce a branch cleanup summary.

    Dry-run (default) reports candidates without mutating state.
    Execute mode performs verified safe deletions.
    """
    working_path = repo_path or str(Path(".").resolve())

    target_type: str
    pr_state: PRMergeState | None = None
    base_branch: str
    head_branch: str
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    if target.isdigit():
        target_type = "pr"
        try:
            pr_state = fetch_post_merge_pr_state(target, repo=repo)
        except GHCommandError as error:
            return _build_error_summary(
                target_type=target_type,
                error_message=f"GitHub PR fetch failed: {error.stderr}",
                mode=mode,
            )
        if not pr_state.merged:
            return BranchCleanupSummary(
                command="branch-cleanup",
                mode=mode,
                ok=False,
                target_type=target_type,
                number=pr_state.number,
                title=pr_state.title,
                url=pr_state.url,
                state=pr_state.state,
                merged=pr_state.merged,
                base_branch=pr_state.base_branch,
                head_branch=pr_state.head_branch,
                candidates=(),
                worktrees_checked=(),
                deleted=(),
                kept=(),
                manual_commands=(),
                warnings=(),
                blocking_reasons=(
                    f"PR #{pr_state.number} is not merged on GitHub (state={pr_state.state}).",
                ),
            )
        head_branch = pr_state.head_branch
        base_branch = pr_state.base_branch
    else:
        target_type = "branch"
        head_branch = target
        warnings.append(
            "GitHub PR merged-state verification not available; "
            "using local Git ancestry and GitHub compare API only."
        )
        try:
            from .github import fetch_repo_default_branch
            base_branch = fetch_repo_default_branch(repo=repo)
        except GHCommandError:
            base_branch = "main"
            warnings.append("Could not fetch repository default branch; defaulting to 'main'.")

    # Resolve Git repository root
    try:
        repo_root = _git(working_path, "rev-parse", "--show-toplevel")
    except GitCommandError as error:
        return _build_error_summary(
            target_type=target_type,
            error_message=f"Unable to find Git repository root: {error.stderr}",
            mode=mode,
            pr_state=pr_state,
        )

    linked_worktrees = _linked_worktrees(repo_root)
    worktrees_checked: list[WorktreeInfo] = []
    for wt in linked_worktrees:
        dirty = inspect_worktree_dirty(wt.path) if wt.path else False
        worktrees_checked.append(WorktreeInfo(path=wt.path, branch=wt.branch, dirty=dirty))

    base_ref = f"origin/{base_branch}"

    # Identify candidates
    candidates: list[CleanupCandidate] = []

    if find_local_branch(repo_root, head_branch):
        candidates.append(
            classify_local_candidate(repo_root, head_branch, base_ref, tuple(linked_worktrees))
        )

    if find_remote_tracking_branch(repo_root, head_branch):
        candidates.append(
            classify_remote_candidate(head_branch, base_branch, repo, tuple(linked_worktrees))
        )

    if not candidates:
        return BranchCleanupSummary(
            command="branch-cleanup",
            mode=mode,
            ok=True,
            target_type=target_type,
            number=pr_state.number if pr_state else None,
            title=pr_state.title if pr_state else None,
            url=pr_state.url if pr_state else None,
            state=pr_state.state if pr_state else None,
            merged=pr_state.merged if pr_state else None,
            base_branch=base_branch,
            head_branch=head_branch,
            candidates=(),
            worktrees_checked=tuple(worktrees_checked),
            deleted=(),
            kept=(),
            manual_commands=(),
            warnings=tuple(warnings),
            blocking_reasons=("No local or remote branches found for head branch.",),
        )

    if mode == "dry_run":
        kept_list = [
            {"branch": c.branch, "reason": c.reason or ""}
            for c in candidates
            if c.disposition in ("kept", "skipped")
        ]
        return BranchCleanupSummary(
            command="branch-cleanup",
            mode=mode,
            ok=True,
            target_type=target_type,
            number=pr_state.number if pr_state else None,
            title=pr_state.title if pr_state else None,
            url=pr_state.url if pr_state else None,
            state=pr_state.state if pr_state else None,
            merged=pr_state.merged if pr_state else None,
            base_branch=base_branch,
            head_branch=head_branch,
            candidates=tuple(candidates),
            worktrees_checked=tuple(worktrees_checked),
            deleted=(),
            kept=tuple(kept_list),
            manual_commands=(),
            warnings=tuple(warnings),
            blocking_reasons=tuple(blocking_reasons),
        )

    # Execute mode: perform verified safe deletions
    deleted: list[str] = []
    kept_entries: list[dict[str, str]] = []
    manual_commands: list[str] = []
    final_candidates: list[CleanupCandidate] = []

    for candidate in candidates:
        if candidate.disposition != "safe_to_delete":
            final_candidates.append(candidate)
            if candidate.disposition in ("kept", "skipped"):
                kept_entries.append({"branch": candidate.branch, "reason": candidate.reason or ""})
            continue

        if candidate.type == "local":
            success, err = delete_local_branch(repo_root, candidate.branch, candidate.content_verified)
            if success:
                deleted.append(candidate.branch)
                final_candidates.append(CleanupCandidate(
                    branch=candidate.branch,
                    type=candidate.type,
                    disposition="deleted",
                    reason=None,
                    worktree=candidate.worktree,
                    content_verified=candidate.content_verified,
                    ahead_by=candidate.ahead_by,
                ))
            else:
                flag = "-D" if candidate.content_verified else "-d"
                manual_commands.append(f"git branch {flag} {candidate.branch}")
                reason = f"deletion failed: {err}"
                kept_entries.append({"branch": candidate.branch, "reason": reason})
                final_candidates.append(CleanupCandidate(
                    branch=candidate.branch,
                    type=candidate.type,
                    disposition="kept",
                    reason=reason,
                    worktree=candidate.worktree,
                    content_verified=candidate.content_verified,
                    ahead_by=candidate.ahead_by,
                ))

        elif candidate.type == "remote":
            success, err = delete_remote_branch(repo_root, candidate.branch)
            if success:
                deleted.append(f"origin/{candidate.branch}")
                final_candidates.append(CleanupCandidate(
                    branch=candidate.branch,
                    type=candidate.type,
                    disposition="deleted",
                    reason=None,
                    worktree=candidate.worktree,
                    content_verified=candidate.content_verified,
                    ahead_by=candidate.ahead_by,
                ))
            else:
                manual_commands.append(f"git push origin --delete {candidate.branch}")
                reason = f"remote deletion failed (permissions/auth): {err}"
                kept_entries.append({"branch": candidate.branch, "reason": reason})
                final_candidates.append(CleanupCandidate(
                    branch=candidate.branch,
                    type=candidate.type,
                    disposition="kept",
                    reason=reason,
                    worktree=candidate.worktree,
                    content_verified=candidate.content_verified,
                    ahead_by=candidate.ahead_by,
                ))

    had_failures = any("failed" in e["reason"] for e in kept_entries)
    ok = not had_failures

    return BranchCleanupSummary(
        command="branch-cleanup",
        mode=mode,
        ok=ok,
        target_type=target_type,
        number=pr_state.number if pr_state else None,
        title=pr_state.title if pr_state else None,
        url=pr_state.url if pr_state else None,
        state=pr_state.state if pr_state else None,
        merged=pr_state.merged if pr_state else None,
        base_branch=base_branch,
        head_branch=head_branch,
        candidates=tuple(final_candidates),
        worktrees_checked=tuple(worktrees_checked),
        deleted=tuple(deleted),
        kept=tuple(kept_entries),
        manual_commands=tuple(manual_commands),
        warnings=tuple(warnings),
        blocking_reasons=tuple(blocking_reasons),
    )
