"""Post-merge local sync: verify GitHub merged state and safely fast-forward local base branch."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .github.pr_context import GHCommandError, _gh_env
from .worktree_status import GitCommandError, WorktreeStatusSummary, _git, summarize_worktree_status


POST_MERGE_PR_FIELDS: tuple[str, ...] = (
    "number",
    "title",
    "url",
    "state",
    "merged",
    "mergedAt",
    "mergeCommit",
    "baseRefName",
    "baseRefOid",
    "headRefName",
    "headRefOid",
)

BACKUP_FILENAME = ".hpw-post-merge-backup.patch"

SAFE_CLASSIFICATIONS = frozenset({"already_synced", "safe_fast_forward", "verified_stale_pr_copy"})


@dataclass(frozen=True)
class PRMergeState:
    number: int
    title: str
    url: str
    state: str
    merged: bool
    base_branch: str
    head_branch: str
    head_sha: str
    merge_sha: str | None
    base_sha_after_merge: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "state": self.state,
            "merged": self.merged,
            "base_branch": self.base_branch,
            "head_branch": self.head_branch,
            "head_sha": self.head_sha,
            "merge_sha": self.merge_sha,
            "base_sha_after_merge": self.base_sha_after_merge,
        }


@dataclass(frozen=True)
class SyncPlanStep:
    description: str
    command: str | None = None
    blocked: bool = False
    block_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "command": self.command,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
        }


@dataclass(frozen=True)
class ExecutionResult:
    backup_path: str | None
    steps_executed: tuple[str, ...]
    steps_skipped: tuple[str, ...]
    failed_step: str | None
    cleanup_result: str | None
    verification_result: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_path": self.backup_path,
            "steps_executed": list(self.steps_executed),
            "steps_skipped": list(self.steps_skipped),
            "failed_step": self.failed_step,
            "cleanup_result": self.cleanup_result,
            "verification_result": self.verification_result,
        }


@dataclass(frozen=True)
class PostMergeSyncSummary:
    command: str
    mode: str
    ok: bool
    number: int
    title: str
    url: str
    state: str
    merged: bool
    base_branch: str
    head_branch: str
    head_sha: str
    merge_sha: str | None
    base_sha_after_merge: str | None
    local: dict[str, Any]
    status: dict[str, Any]
    classification: str
    verified_pr_paths: tuple[str, ...]
    blocked_paths: dict[str, list[str]]
    plan: tuple[SyncPlanStep, ...]
    execution: ExecutionResult | None
    manual_commands: tuple[str, ...]
    warnings: tuple[str, ...]
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "mode": self.mode,
            "ok": self.ok,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "state": self.state,
            "merged": self.merged,
            "base_branch": self.base_branch,
            "head_branch": self.head_branch,
            "head_sha": self.head_sha,
            "merge_sha": self.merge_sha,
            "base_sha_after_merge": self.base_sha_after_merge,
            "local": self.local,
            "status": self.status,
            "classification": self.classification,
            "verified_pr_paths": list(self.verified_pr_paths),
            "blocked_paths": self.blocked_paths,
            "plan": [step.to_dict() for step in self.plan],
            "execution": self.execution.to_dict() if self.execution else None,
            "manual_commands": list(self.manual_commands),
            "warnings": list(self.warnings),
            "blocking_reasons": list(self.blocking_reasons),
        }


def fetch_post_merge_pr_state(target: str | None, *, repo: str | None) -> PRMergeState:
    """Fetch PR merge state from GitHub including merge metadata."""
    command = ["gh", "pr", "view"]
    if target:
        command.append(target)
    if repo:
        command.extend(["--repo", repo])
    command.extend(["--json", ",".join(POST_MERGE_PR_FIELDS)])

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
            command, result.returncode, f"GitHub CLI returned invalid JSON: {error.msg}", error="gh-invalid-json"
        ) from error

    return _parse_pr_merge_state(raw, command=command, returncode=result.returncode)


def fetch_pr_changed_paths(target: str | None, *, repo: str | None) -> tuple[str, ...]:
    """Fetch file paths changed in a PR using gh pr diff --name-only."""
    command = ["gh", "pr", "diff"]
    if target:
        command.append(target)
    if repo:
        command.extend(["--repo", repo])
    command.append("--name-only")

    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError as error:
        raise GHCommandError(command, None, "GitHub CLI executable not found: gh. Install from https://cli.github.com and authenticate with 'gh auth login'.", error="gh-not-found") from error

    if result.returncode != 0:
        raise GHCommandError(command, result.returncode, result.stderr)

    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return tuple(paths)


def check_paths_match_upstream(worktree_path: str, paths: set[str]) -> frozenset[str]:
    """Return subset of paths whose working-tree content matches @{upstream}.

    A path matches upstream when `git diff @{upstream} -- <path>` produces no output,
    meaning the working-tree content is identical to the upstream (post-merge) state.
    Such paths are verified stale PR copies, safe to restore before a fast-forward.
    """
    matched: set[str] = set()
    for path in paths:
        try:
            diff = _git(worktree_path, "diff", "@{upstream}", "--", path)
            if not diff.strip():
                matched.add(path)
        except GitCommandError:
            pass
    return frozenset(matched)


def classify_sync_state(
    pr_state: PRMergeState,
    worktree_status: WorktreeStatusSummary,
    pr_changed_paths: tuple[str, ...],
    upstream_matched_paths: frozenset[str],
) -> tuple[str, tuple[str, ...], dict[str, list[str]], list[str]]:
    """Classify local sync state relative to a merged PR.

    Returns: (classification, verified_pr_paths, blocked_paths, blocking_reasons)
    - classification: one of the SAFE_CLASSIFICATIONS or a blocked_* variant
    - verified_pr_paths: paths safe to restore (stale PR copy confirmed against upstream)
    - blocked_paths: paths grouped by blocking reason
    - blocking_reasons: human-readable reasons the sync is blocked
    """
    if not pr_state.merged:
        reason = f"PR #{pr_state.number} is not merged on GitHub (state={pr_state.state})."
        return "blocked_not_merged", (), {}, [reason]

    if not worktree_status.ok:
        msg = (
            worktree_status.error.get("message", "unable to inspect local Git state")
            if worktree_status.error
            else "unable to inspect local Git state"
        )
        return "blocked_missing_facts", (), {}, [f"Local Git state could not be inspected: {msg}"]

    branch = worktree_status.branch
    file_status = worktree_status.status

    if file_status.conflicted:
        blocked: dict[str, list[str]] = {"conflicted": list(file_status.conflicted)}
        return "blocked_conflicts", (), blocked, ["Conflicted files prevent safe sync. Resolve conflicts first."]

    if branch.detached:
        return "blocked_not_base_branch", (), {}, [
            f"Current HEAD is detached. Checkout {pr_state.base_branch!r} first."
        ]

    if branch.name != pr_state.base_branch:
        actual = branch.name or "unknown"
        return "blocked_not_base_branch", (), {}, [
            f"Current branch is {actual!r}, not the PR base branch {pr_state.base_branch!r}."
        ]

    if branch.upstream is None:
        return "blocked_missing_facts", (), {}, [
            f"Branch {pr_state.base_branch!r} has no upstream tracking ref. "
            f"Set upstream first, e.g.: "
            f"git branch --set-upstream-to=origin/{pr_state.base_branch} {pr_state.base_branch}"
        ]

    if file_status.clean:
        head_sha = worktree_status.head_sha
        upstream_sha = branch.upstream_sha
        if head_sha and upstream_sha and head_sha == upstream_sha:
            return "already_synced", (), {}, []
        if head_sha and pr_state.merge_sha and head_sha == pr_state.merge_sha:
            return "already_synced", (), {}, []
        if head_sha and pr_state.base_sha_after_merge and head_sha == pr_state.base_sha_after_merge:
            return "already_synced", (), {}, []
        return "safe_fast_forward", (), {}, []

    # Dirty worktree — classify dirty work
    blocked_paths: dict[str, list[str]] = {}
    blocking_reasons: list[str] = []

    if file_status.staged:
        blocked_paths["staged"] = list(file_status.staged)
        blocking_reasons.append(
            f"{len(file_status.staged)} staged file(s) present. Commit or stash staged changes first."
        )

    pr_path_set = set(pr_changed_paths)
    unstaged_set = set(file_status.unstaged)
    pr_related_dirty = unstaged_set & pr_path_set
    unrelated_dirty = unstaged_set - pr_path_set

    if unrelated_dirty:
        blocked_paths["unrelated_dirty"] = sorted(unrelated_dirty)
        blocking_reasons.append(
            f"{len(unrelated_dirty)} dirty file(s) are not part of the merged PR changes."
        )

    untracked_at_risk = set(file_status.untracked) & pr_path_set
    if untracked_at_risk:
        blocked_paths["untracked_would_be_overwritten"] = sorted(untracked_at_risk)
        blocking_reasons.append(
            f"{len(untracked_at_risk)} untracked file(s) match PR paths and could be overwritten by sync."
        )

    if blocking_reasons:
        if pr_related_dirty and (unrelated_dirty or file_status.staged or untracked_at_risk):
            classification = "blocked_ambiguous_dirty_work"
        else:
            classification = "blocked_unrelated_dirty_work"
        return classification, (), blocked_paths, blocking_reasons

    # All dirty unstaged files are PR-related — verify content matches upstream
    if pr_related_dirty:
        verified = pr_related_dirty & upstream_matched_paths
        unverified = pr_related_dirty - upstream_matched_paths
        if unverified:
            blocked_paths["content_mismatch"] = sorted(unverified)
            reasons = [
                f"{len(unverified)} dirty PR-related file(s) have content that differs from the merged upstream. "
                "They may contain unique local work."
            ]
            return "blocked_ambiguous_dirty_work", tuple(sorted(verified)), blocked_paths, reasons
        return "verified_stale_pr_copy", tuple(sorted(verified)), {}, []

    return "safe_fast_forward", (), {}, []


def summarize_post_merge_sync(
    pr_state: PRMergeState,
    worktree_status: WorktreeStatusSummary,
    pr_changed_paths: tuple[str, ...],
    mode: str = "dry_run",
) -> PostMergeSyncSummary:
    """Produce a post-merge sync summary.

    In dry_run mode (default), produces a safe plan without mutating local state.
    In execute mode, performs the verified safe sequence.
    """
    worktree_path = worktree_status.worktree_path or worktree_status.path

    # Compute which dirty PR-related paths match upstream content (stale PR copy detection)
    upstream_matched: frozenset[str] = frozenset()
    if worktree_status.ok and worktree_status.status.unstaged and pr_changed_paths and worktree_status.branch.upstream:
        dirty_pr_candidates = set(worktree_status.status.unstaged) & set(pr_changed_paths)
        if dirty_pr_candidates:
            upstream_matched = check_paths_match_upstream(worktree_path, dirty_pr_candidates)

    classification, verified_pr_paths, blocked_paths, blocking_reasons = classify_sync_state(
        pr_state, worktree_status, pr_changed_paths, upstream_matched
    )

    branch_upstream = worktree_status.branch.upstream if worktree_status.ok else None
    plan, manual_commands = _build_plan(classification, pr_state, branch_upstream, verified_pr_paths, worktree_path)

    execution: ExecutionResult | None = None
    if mode == "execute":
        if classification in SAFE_CLASSIFICATIONS:
            execution = _execute_sync_plan(classification, pr_state, worktree_status, verified_pr_paths, worktree_path)
        else:
            execution = ExecutionResult(
                backup_path=None,
                steps_executed=(),
                steps_skipped=("execution blocked by classification",),
                failed_step=None,
                cleanup_result=None,
                verification_result=None,
            )

    ok = _compute_ok(classification, mode, execution)

    if worktree_status.ok:
        local_dict = worktree_status.to_dict()
        status_dict = worktree_status.status.to_dict()
        warnings = list(worktree_status.warnings)
    else:
        local_dict = {"error": worktree_status.error}
        status_dict = {"clean": False, "staged": [], "unstaged": [], "untracked": [], "conflicted": []}
        warnings = []

    if execution and execution.failed_step:
        blocking_reasons = list(blocking_reasons) + [f"Execute step failed: {execution.failed_step}"]

    return PostMergeSyncSummary(
        command="post-merge-sync",
        mode=mode,
        ok=ok,
        number=pr_state.number,
        title=pr_state.title,
        url=pr_state.url,
        state=pr_state.state,
        merged=pr_state.merged,
        base_branch=pr_state.base_branch,
        head_branch=pr_state.head_branch,
        head_sha=pr_state.head_sha,
        merge_sha=pr_state.merge_sha,
        base_sha_after_merge=pr_state.base_sha_after_merge,
        local=local_dict,
        status=status_dict,
        classification=classification,
        verified_pr_paths=verified_pr_paths,
        blocked_paths=blocked_paths,
        plan=plan,
        execution=execution,
        manual_commands=manual_commands,
        warnings=tuple(warnings),
        blocking_reasons=tuple(blocking_reasons),
    )


def _compute_ok(classification: str, mode: str, execution: ExecutionResult | None) -> bool:
    if classification not in SAFE_CLASSIFICATIONS:
        return False
    if mode == "dry_run":
        return True
    if execution is None:
        return True
    return execution.failed_step is None


def _remote_from_upstream(upstream: str | None) -> str | None:
    if not upstream:
        return None
    parts = upstream.split("/", 1)
    return parts[0] if len(parts) == 2 else None


def _build_plan(
    classification: str,
    pr_state: PRMergeState,
    branch_upstream: str | None,
    verified_pr_paths: tuple[str, ...],
    worktree_root: str,
) -> tuple[tuple[SyncPlanStep, ...], tuple[str, ...]]:
    remote = _remote_from_upstream(branch_upstream) or "origin"
    base_branch = pr_state.base_branch
    backup_path = str(Path(worktree_root) / BACKUP_FILENAME)
    manual_commands: list[str] = []

    if classification == "already_synced":
        return (
            SyncPlanStep("Local base branch is already synced with upstream. No action needed."),
        ), ()

    if classification == "safe_fast_forward":
        pull_cmd = f"git pull --ff-only {remote} {base_branch}"
        return (
            SyncPlanStep("Fast-forward local base branch from remote.", command=pull_cmd),
            SyncPlanStep("Verify worktree is clean after fast-forward.", command="git status --porcelain"),
        ), ()

    if classification == "verified_stale_pr_copy":
        paths_arg = " ".join(f'"{p}"' for p in verified_pr_paths)
        return (
            SyncPlanStep(
                "Create patch backup of stale PR-copy paths.",
                command=f"git diff HEAD -- {paths_arg} > {backup_path}",
            ),
            SyncPlanStep(
                "Restore stale PR-copy paths from local HEAD (removes working-tree copy of merged content).",
                command=f"git restore -- {paths_arg}",
            ),
            SyncPlanStep(
                f"Fast-forward local {base_branch!r} from {remote!r}.",
                command=f"git pull --ff-only {remote} {base_branch}",
            ),
            SyncPlanStep(
                "Verify PR changes are present in Git history (merged content now in local log).",
                command=f"git log --oneline -3 -- {paths_arg}",
            ),
            SyncPlanStep(
                "Verify worktree is clean (merged content in history, not in working tree).",
                command="git status --porcelain",
            ),
            SyncPlanStep(
                "Remove patch backup after successful verification.",
                command=f"del {backup_path}" if _is_windows() else f"rm {backup_path}",
            ),
        ), ()

    if classification == "blocked_not_base_branch":
        manual_commands.append(f"git checkout {base_branch}")
        return (), tuple(manual_commands)

    if classification == "blocked_missing_facts":
        manual_commands.append(f"git branch --set-upstream-to=origin/{base_branch} {base_branch}")
        return (), tuple(manual_commands)

    if classification == "blocked_conflicts":
        manual_commands.append("git status  # review conflicted files before proceeding")
        return (), tuple(manual_commands)

    if classification in ("blocked_unrelated_dirty_work", "blocked_ambiguous_dirty_work"):
        pull_cmd = f"git pull --ff-only {remote} {base_branch}"
        manual_commands.append("git stash  # stash unrelated local work")
        manual_commands.append(pull_cmd)
        manual_commands.append("git stash pop  # restore local work after fast-forward")
        return (), tuple(manual_commands)

    return (), ()


def _execute_sync_plan(
    classification: str,
    pr_state: PRMergeState,
    worktree_status: WorktreeStatusSummary,
    verified_pr_paths: tuple[str, ...],
    worktree_path: str,
) -> ExecutionResult:
    remote = _remote_from_upstream(worktree_status.branch.upstream) or "origin"
    base_branch = pr_state.base_branch

    if classification == "already_synced":
        return ExecutionResult(
            backup_path=None,
            steps_executed=(),
            steps_skipped=("fast-forward: already synced",),
            failed_step=None,
            cleanup_result=None,
            verification_result="already_synced",
        )

    if classification == "safe_fast_forward":
        return _execute_fast_forward(worktree_path, remote, base_branch)

    if classification == "verified_stale_pr_copy":
        backup_path = str(Path(worktree_path) / BACKUP_FILENAME)
        return _execute_stale_pr_copy_sync(worktree_path, remote, base_branch, verified_pr_paths, backup_path)

    return ExecutionResult(
        backup_path=None,
        steps_executed=(),
        steps_skipped=(),
        failed_step=None,
        cleanup_result=None,
        verification_result=None,
    )


def _execute_fast_forward(worktree_path: str, remote: str, base_branch: str) -> ExecutionResult:
    steps_executed: list[str] = []
    pull_cmd = f"git pull --ff-only {remote} {base_branch}"

    try:
        _run_git_mutation(worktree_path, "pull", "--ff-only", remote, base_branch)
        steps_executed.append(pull_cmd)
    except GitCommandError as error:
        return ExecutionResult(
            backup_path=None,
            steps_executed=tuple(steps_executed),
            steps_skipped=(),
            failed_step=f"{pull_cmd}: {error.stderr}",
            cleanup_result=None,
            verification_result=None,
        )

    verification = _check_clean_status(worktree_path)
    if verification == "ok":
        steps_executed.append("verified worktree is clean")

    return ExecutionResult(
        backup_path=None,
        steps_executed=tuple(steps_executed),
        steps_skipped=(),
        failed_step=None if verification == "ok" else f"post-execute verification: {verification}",
        cleanup_result=None,
        verification_result=verification,
    )


def _execute_stale_pr_copy_sync(
    worktree_path: str,
    remote: str,
    base_branch: str,
    verified_pr_paths: tuple[str, ...],
    backup_path: str,
) -> ExecutionResult:
    steps_executed: list[str] = []

    # Step 1: Create patch backup
    try:
        patch_content = _git(worktree_path, "diff", "HEAD", "--", *verified_pr_paths)
        Path(backup_path).write_text(patch_content, encoding="utf-8")
        steps_executed.append(f"created backup patch at {backup_path}")
    except (GitCommandError, OSError) as error:
        err_msg = getattr(error, "stderr", str(error))
        return ExecutionResult(
            backup_path=None,
            steps_executed=tuple(steps_executed),
            steps_skipped=(),
            failed_step=f"backup creation failed: {err_msg}",
            cleanup_result=None,
            verification_result=None,
        )

    # Step 2: Restore PR paths from local HEAD
    try:
        _run_git_mutation(worktree_path, "restore", "--", *verified_pr_paths)
        steps_executed.append(f"restored {len(verified_pr_paths)} PR path(s) from local HEAD")
    except (GitCommandError, OSError) as error:
        err_msg = getattr(error, "stderr", str(error))
        return ExecutionResult(
            backup_path=backup_path,
            steps_executed=tuple(steps_executed),
            steps_skipped=(),
            failed_step=f"restore failed: {err_msg}",
            cleanup_result="preserved (restore step failed)",
            verification_result=None,
        )

    # Step 3: Fast-forward
    pull_cmd = f"git pull --ff-only {remote} {base_branch}"
    try:
        _run_git_mutation(worktree_path, "pull", "--ff-only", remote, base_branch)
        steps_executed.append(f"fast-forwarded {base_branch!r} from {remote!r}")
    except GitCommandError as error:
        return ExecutionResult(
            backup_path=backup_path,
            steps_executed=tuple(steps_executed),
            steps_skipped=(),
            failed_step=f"{pull_cmd}: {error.stderr}",
            cleanup_result="preserved (fast-forward failed)",
            verification_result=None,
        )

    # Step 4+5: Verify history + clean status
    verification = _check_clean_status_excluding_backup(worktree_path, backup_path)
    if verification == "ok":
        steps_executed.append("verified PR changes in Git history and worktree is clean")

    # Step 6: Remove backup if verification passed
    cleanup_result: str | None
    if verification == "ok":
        try:
            Path(backup_path).unlink()
            cleanup_result = "ok"
            steps_executed.append("removed backup patch")
            backup_path_result: str | None = None
        except OSError as error:
            cleanup_result = f"cleanup failed: {error}"
            backup_path_result = backup_path
    else:
        cleanup_result = "preserved (verification not passed)"
        backup_path_result = backup_path

    failed = None if (verification == "ok" and cleanup_result == "ok") else (
        f"post-execute verification: {verification}" if verification != "ok"
        else f"backup cleanup: {cleanup_result}"
    )

    return ExecutionResult(
        backup_path=backup_path_result,
        steps_executed=tuple(steps_executed),
        steps_skipped=(),
        failed_step=failed,
        cleanup_result=cleanup_result,
        verification_result=verification,
    )


def _check_clean_status(worktree_path: str) -> str:
    try:
        output = _git(worktree_path, "status", "--porcelain=v1")
        if output.strip():
            return f"worktree not clean: {output.strip()[:200]}"
        return "ok"
    except GitCommandError as error:
        return f"status check failed: {error.stderr}"


def _check_clean_status_excluding_backup(worktree_path: str, backup_path: str) -> str:
    backup_name = Path(backup_path).name
    try:
        output = _git(worktree_path, "status", "--porcelain=v1", "-z")
        entries = [e for e in output.split("\0") if e and backup_name not in e]
        if entries:
            return f"worktree not clean after fast-forward: {'; '.join(e.strip() for e in entries[:5])}"
        return "ok"
    except GitCommandError as error:
        return f"status check failed: {error.stderr}"


def _run_git_mutation(worktree_path: str, *args: str) -> str:
    """Run a git command that may mutate local state. Raises GitCommandError on failure."""
    return _git(worktree_path, *args)


def _parse_pr_merge_state(raw: dict[str, Any], *, command: list[str], returncode: int) -> PRMergeState:
    try:
        number = int(raw["number"])
        title = raw.get("title") or ""
        url = raw.get("url") or ""
        state = raw.get("state") or ""
        merged = bool(raw.get("merged")) or bool(raw.get("mergedAt")) or state == "MERGED"
        base_branch = raw.get("baseRefName") or ""
        head_branch = raw.get("headRefName") or ""
        head_sha = raw.get("headRefOid") or ""
        merge_commit = raw.get("mergeCommit")
        merge_sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
        base_sha_after_merge = raw.get("baseRefOid") if merged else None
    except (KeyError, TypeError, ValueError) as error:
        raise GHCommandError(
            command, returncode, f"GitHub PR payload could not be parsed: {error}", error="gh-parse-failed"
        ) from error

    return PRMergeState(
        number=number,
        title=title,
        url=url,
        state=state,
        merged=merged,
        base_branch=base_branch,
        head_branch=head_branch,
        head_sha=head_sha,
        merge_sha=merge_sha,
        base_sha_after_merge=base_sha_after_merge,
    )


def _is_windows() -> bool:
    import sys
    return sys.platform == "win32"
