"""Unit tests for post_merge_sync classification logic and GitHub merged-state summarization."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from headless_pr_workflow.post_merge_sync import (
    BACKUP_FILENAME,
    PRMergeState,
    _parse_pr_merge_state,
    check_paths_match_upstream,
    classify_sync_state,
    fetch_post_merge_pr_state,
    fetch_pr_changed_paths,
    summarize_post_merge_sync,
)
from headless_pr_workflow.worktree_status import (
    BranchStatus,
    FileStatus,
    WorktreeStatusSummary,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_pr_merge_state(
    *,
    number: int = 99,
    title: str = "Merged PR",
    url: str = "https://github.com/owner/repo/pull/99",
    state: str = "MERGED",
    merged: bool = True,
    base_branch: str = "main",
    head_branch: str = "feature/x",
    head_sha: str = "abc123",
    merge_sha: str | None = "merge456",
    base_sha_after_merge: str | None = "post789",
) -> PRMergeState:
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


def build_branch_status(
    *,
    name: str | None = "main",
    detached: bool = False,
    upstream: str | None = "origin/main",
    upstream_sha: str | None = "post789",
    ahead: int | None = 0,
    behind: int | None = 1,
    tracking_status: str = "tracking",
) -> BranchStatus:
    return BranchStatus(
        name=name,
        detached=detached,
        upstream=upstream,
        upstream_sha=upstream_sha,
        ahead=ahead,
        behind=behind,
        tracking_status=tracking_status,
    )


def build_file_status(
    *,
    clean: bool = True,
    staged: tuple[str, ...] = (),
    unstaged: tuple[str, ...] = (),
    untracked: tuple[str, ...] = (),
    conflicted: tuple[str, ...] = (),
) -> FileStatus:
    effective_clean = clean and not staged and not unstaged and not untracked and not conflicted
    return FileStatus(
        clean=effective_clean,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicted=conflicted,
    )


def build_worktree_status(
    *,
    ok: bool = True,
    path: str = "/repo",
    repository_root: str | None = "/repo",
    worktree_path: str | None = "/repo",
    head_sha: str | None = "local123",
    branch: BranchStatus | None = None,
    file_status: FileStatus | None = None,
    warnings: tuple[str, ...] = (),
    error: dict[str, Any] | None = None,
) -> WorktreeStatusSummary:
    if branch is None:
        branch = build_branch_status()
    if file_status is None:
        file_status = build_file_status()
    return WorktreeStatusSummary(
        command="worktree-status",
        ok=ok,
        path=path,
        repository_root=repository_root,
        worktree_path=worktree_path,
        head_sha=head_sha,
        branch=branch,
        status=file_status,
        unpushed_commits=(),
        linked_worktrees=(),
        branch_in_use_by_other_worktree=False,
        warnings=warnings,
        error=error,
    )


# ---------------------------------------------------------------------------
# Tests: parse_pr_merge_state
# ---------------------------------------------------------------------------


def test_parse_pr_merge_state_merged_flag():
    raw = {
        "number": 42,
        "title": "PR Title",
        "url": "https://github.com/owner/repo/pull/42",
        "state": "MERGED",
        "merged": True,
        "mergedAt": "2026-05-01T10:00:00Z",
        "mergeCommit": {"oid": "mergeoid123"},
        "baseRefName": "main",
        "baseRefOid": "baseShaAfterMerge",
        "headRefName": "feature/y",
        "headRefOid": "headSha",
    }
    result = _parse_pr_merge_state(raw, command=["gh"], returncode=0)

    assert result.number == 42
    assert result.merged is True
    assert result.merge_sha == "mergeoid123"
    assert result.base_sha_after_merge == "baseShaAfterMerge"
    assert result.base_branch == "main"
    assert result.state == "MERGED"


def test_parse_pr_merge_state_infers_merged_from_state():
    raw = {
        "number": 5,
        "state": "MERGED",
        "merged": False,
        "baseRefName": "main",
        "headRefName": "feature",
        "headRefOid": "sha1",
    }
    result = _parse_pr_merge_state(raw, command=["gh"], returncode=0)
    assert result.merged is True


def test_parse_pr_merge_state_not_merged_has_no_base_sha():
    raw = {
        "number": 5,
        "state": "OPEN",
        "merged": False,
        "baseRefName": "main",
        "baseRefOid": "baseSha",
        "headRefName": "feature",
        "headRefOid": "sha1",
    }
    result = _parse_pr_merge_state(raw, command=["gh"], returncode=0)
    assert result.merged is False
    assert result.base_sha_after_merge is None


def test_parse_pr_merge_state_missing_merge_commit():
    raw = {
        "number": 7,
        "state": "MERGED",
        "merged": True,
        "mergeCommit": None,
        "baseRefName": "main",
        "headRefName": "feature",
        "headRefOid": "sha1",
    }
    result = _parse_pr_merge_state(raw, command=["gh"], returncode=0)
    assert result.merge_sha is None


# ---------------------------------------------------------------------------
# Tests: classify_sync_state — no upstream_matched_paths needed for most cases
# ---------------------------------------------------------------------------


def test_classify_blocked_not_merged():
    pr = build_pr_merge_state(merged=False, state="OPEN")
    ws = build_worktree_status()
    classification, _, _, reasons = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "blocked_not_merged"
    assert any("not merged" in r for r in reasons)


def test_classify_blocked_missing_facts_git_error():
    pr = build_pr_merge_state()
    ws = build_worktree_status(ok=False, error={"message": "not a git repo"})
    classification, _, _, reasons = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "blocked_missing_facts"
    assert any("not a git repo" in r for r in reasons)


def test_classify_blocked_conflicts():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=False, conflicted=("conflict.txt",))
    )
    classification, _, blocked, reasons = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "blocked_conflicts"
    assert "conflicted" in blocked
    assert "conflict.txt" in blocked["conflicted"]


def test_classify_blocked_detached_head():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        branch=build_branch_status(name=None, detached=True)
    )
    classification, _, _, reasons = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "blocked_not_base_branch"
    assert any("detached" in r for r in reasons)


def test_classify_blocked_wrong_branch():
    pr = build_pr_merge_state(base_branch="main")
    ws = build_worktree_status(
        branch=build_branch_status(name="feature/other")
    )
    classification, _, _, reasons = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "blocked_not_base_branch"
    assert any("feature/other" in r for r in reasons)


def test_classify_blocked_missing_upstream():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        branch=build_branch_status(upstream=None, upstream_sha=None, ahead=None, behind=None)
    )
    classification, _, _, reasons = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "blocked_missing_facts"
    assert any("upstream" in r for r in reasons)


def test_classify_already_synced_head_matches_upstream():
    sha = "synced123"
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        head_sha=sha,
        branch=build_branch_status(upstream_sha=sha, ahead=0, behind=0),
        file_status=build_file_status(clean=True),
    )
    classification, _, _, _ = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "already_synced"


def test_classify_already_synced_head_matches_merge_sha():
    merge_sha = "merge456"
    pr = build_pr_merge_state(merge_sha=merge_sha)
    ws = build_worktree_status(
        head_sha=merge_sha,
        branch=build_branch_status(upstream_sha="different", ahead=0, behind=0),
        file_status=build_file_status(clean=True),
    )
    classification, _, _, _ = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "already_synced"


def test_classify_already_synced_head_matches_base_sha_after_merge():
    base_sha = "post789"
    pr = build_pr_merge_state(base_sha_after_merge=base_sha)
    ws = build_worktree_status(
        head_sha=base_sha,
        branch=build_branch_status(upstream_sha="other", ahead=0, behind=0),
        file_status=build_file_status(clean=True),
    )
    classification, _, _, _ = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "already_synced"


def test_classify_safe_fast_forward_clean_behind():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        head_sha="premerge",
        branch=build_branch_status(upstream_sha="postmerge", ahead=0, behind=1),
        file_status=build_file_status(clean=True),
    )
    classification, _, _, _ = classify_sync_state(pr, ws, (), frozenset())
    assert classification == "safe_fast_forward"


def test_classify_blocked_staged_changes():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=False, staged=("staged.txt",))
    )
    classification, _, blocked, reasons = classify_sync_state(pr, ws, (), frozenset())
    assert classification in ("blocked_unrelated_dirty_work", "blocked_ambiguous_dirty_work")
    assert "staged" in blocked
    assert any("staged" in r for r in reasons)


def test_classify_blocked_unrelated_dirty_file():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=False, unstaged=("unrelated.txt",))
    )
    # PR changed a different file
    classification, _, blocked, reasons = classify_sync_state(pr, ws, ("pr_file.py",), frozenset())
    assert classification == "blocked_unrelated_dirty_work"
    assert "unrelated_dirty" in blocked
    assert "unrelated.txt" in blocked["unrelated_dirty"]


def test_classify_blocked_ambiguous_dirty_overlap():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=False, unstaged=("pr_file.py", "local_only.txt"))
    )
    # pr_file.py is PR-related, local_only.txt is not, and pr_file.py does NOT match upstream
    classification, verified, blocked, _ = classify_sync_state(pr, ws, ("pr_file.py",), frozenset())
    assert classification in ("blocked_ambiguous_dirty_work", "blocked_unrelated_dirty_work")


def test_classify_blocked_untracked_would_be_overwritten():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=False, untracked=("pr_file.py",))
    )
    classification, _, blocked, reasons = classify_sync_state(pr, ws, ("pr_file.py",), frozenset())
    assert classification in ("blocked_unrelated_dirty_work", "blocked_ambiguous_dirty_work")
    assert "untracked_would_be_overwritten" in blocked


def test_classify_verified_stale_pr_copy_all_paths_match_upstream():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=False, unstaged=("pr_file.py",))
    )
    # Simulate: pr_file.py matches upstream (verified stale copy)
    upstream_matched = frozenset({"pr_file.py"})
    classification, verified_paths, blocked, reasons = classify_sync_state(
        pr, ws, ("pr_file.py",), upstream_matched
    )
    assert classification == "verified_stale_pr_copy"
    assert "pr_file.py" in verified_paths
    assert not blocked
    assert not reasons


def test_classify_blocked_ambiguous_content_mismatch():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=False, unstaged=("pr_file.py",))
    )
    # pr_file.py is PR-related but does NOT match upstream (unique local changes)
    classification, verified, blocked, reasons = classify_sync_state(pr, ws, ("pr_file.py",), frozenset())
    assert classification == "blocked_ambiguous_dirty_work"
    assert "content_mismatch" in blocked


# ---------------------------------------------------------------------------
# Tests: execute mode with real git repos
# ---------------------------------------------------------------------------


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(("git", "-C", str(repo), *args), check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def init_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(("git", "init", "-b", "main", str(path)), check=True, capture_output=True, text=True)
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "commit.gpgsign", "false")
    return path


def commit_file(repo: Path, name: str = "file.txt", content: str = "content\n", message: str = "commit") -> str:
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def setup_tracking_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(("git", "init", "--bare", str(remote)), check=True, capture_output=True, text=True)
    repo = init_repo(tmp_path / "repo")
    commit_file(repo)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    return repo, remote


def test_execute_safe_fast_forward_succeeds(tmp_path):
    repo, remote = setup_tracking_repo(tmp_path)
    # Simulate a merge commit on remote that local doesn't have yet
    other = init_repo(tmp_path / "other")
    git(other, "remote", "add", "origin", str(remote))
    git(other, "fetch", "origin")
    git(other, "checkout", "-b", "main", "--track", "origin/main")
    commit_file(other, "merged.txt", "merged content\n", "merge commit")
    git(other, "push", "origin", "main")

    # local repo: fetch to know about the remote commit but don't pull yet
    git(repo, "fetch", "origin")

    from headless_pr_workflow.worktree_status import summarize_worktree_status
    local_status = summarize_worktree_status(str(repo))
    assert local_status.branch.behind == 1

    merge_sha = git(other, "rev-parse", "HEAD").stdout.strip()
    pr = build_pr_merge_state(merge_sha=merge_sha, base_sha_after_merge=merge_sha)

    summary = summarize_post_merge_sync(pr, local_status, ("merged.txt",), mode="execute")

    assert summary.classification == "safe_fast_forward"
    assert summary.execution is not None
    assert summary.execution.failed_step is None
    assert summary.execution.verification_result == "ok"
    assert summary.ok is True

    # Verify local HEAD advanced
    new_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert new_sha == merge_sha


def test_execute_already_synced_no_action_needed(tmp_path):
    repo, remote = setup_tracking_repo(tmp_path)
    git(repo, "fetch", "origin")

    from headless_pr_workflow.worktree_status import summarize_worktree_status
    local_status = summarize_worktree_status(str(repo))
    head_sha = local_status.head_sha

    pr = build_pr_merge_state(merge_sha=head_sha, base_sha_after_merge=head_sha)
    summary = summarize_post_merge_sync(pr, local_status, (), mode="execute")

    assert summary.classification == "already_synced"
    assert summary.ok is True
    assert summary.execution is not None
    assert summary.execution.failed_step is None
    assert summary.execution.verification_result == "already_synced"


def test_execute_verified_stale_pr_copy_backup_restore_pull_cleanup(tmp_path):
    repo, remote = setup_tracking_repo(tmp_path)

    # Add pr_file.py in the initial state (both repos will share the base)
    commit_file(repo, "pr_file.py", "base content\n", "add pr_file.py baseline")
    git(repo, "push", "origin", "main")

    # Stage a "merged" commit on the remote (simulating a merged PR that modified pr_file.py)
    other = init_repo(tmp_path / "other")
    git(other, "remote", "add", "origin", str(remote))
    git(other, "fetch", "origin")
    git(other, "checkout", "-b", "main", "--track", "origin/main")
    (other / "pr_file.py").write_text("PR content\n")
    git(other, "add", "pr_file.py")
    git(other, "commit", "-m", "Modify pr_file.py (merged PR)")
    git(other, "push", "origin", "main")

    # Fetch remote state into local repo without pulling
    git(repo, "fetch", "origin")

    # Simulate stale PR copy: local main still at pre-merge, but working tree has PR content
    # (user already applied the PR diff manually, matching origin/main's new content)
    (repo / "pr_file.py").write_text("PR content\n")

    from headless_pr_workflow.worktree_status import summarize_worktree_status
    local_status = summarize_worktree_status(str(repo))
    assert local_status.branch.behind == 1
    assert "pr_file.py" in local_status.status.unstaged

    # Verify content matches upstream (stale PR copy)
    matched = check_paths_match_upstream(str(repo), {"pr_file.py"})
    assert "pr_file.py" in matched

    pr = build_pr_merge_state()
    summary = summarize_post_merge_sync(pr, local_status, ("pr_file.py",), mode="execute")

    assert summary.classification == "verified_stale_pr_copy"
    assert summary.execution is not None
    assert summary.execution.failed_step is None
    assert summary.execution.verification_result == "ok"
    assert summary.execution.cleanup_result == "ok"
    assert summary.execution.backup_path is None
    assert summary.ok is True

    # Local HEAD should now be at the merged commit
    assert not (repo / BACKUP_FILENAME).exists()


def test_execute_stale_pr_copy_preserves_backup_on_pull_failure(tmp_path):
    repo, remote = setup_tracking_repo(tmp_path)

    # Add pr_file.py as a tracked file in initial state
    commit_file(repo, "pr_file.py", "base content\n", "add pr_file.py baseline")
    git(repo, "push", "origin", "main")

    # Push a remote change to simulate merged PR
    other = init_repo(tmp_path / "other")
    git(other, "remote", "add", "origin", str(remote))
    git(other, "fetch", "origin")
    git(other, "checkout", "-b", "main", "--track", "origin/main")
    (other / "pr_file.py").write_text("PR content\n")
    git(other, "add", "pr_file.py")
    git(other, "commit", "-m", "Modify pr_file.py (merged PR)")
    git(other, "push", "origin", "main")

    git(repo, "fetch", "origin")

    # Modify working tree to match remote (stale PR copy)
    (repo / "pr_file.py").write_text("PR content\n")

    from headless_pr_workflow.worktree_status import summarize_worktree_status
    local_status = summarize_worktree_status(str(repo))

    upstream_matched = frozenset({"pr_file.py"})
    classification, verified_pr_paths, _, _ = classify_sync_state(
        build_pr_merge_state(), local_status, ("pr_file.py",), upstream_matched
    )
    assert classification == "verified_stale_pr_copy"

    # Patch _run_git_mutation to fail on the pull step
    import headless_pr_workflow.post_merge_sync as pms_module
    original = pms_module._run_git_mutation

    call_count = [0]

    def fail_on_pull(path, *args):
        call_count[0] += 1
        if "pull" in args:
            raise pms_module.GitCommandError(
                command=("git", "-C", path, *args),
                returncode=1,
                stderr="fatal: Not possible to fast-forward, aborting.",
            )
        return original(path, *args)

    pms_module._run_git_mutation = fail_on_pull
    try:
        pr = build_pr_merge_state()
        summary = summarize_post_merge_sync(pr, local_status, ("pr_file.py",), mode="execute")
    finally:
        pms_module._run_git_mutation = original

    assert summary.execution is not None
    assert summary.execution.failed_step is not None
    assert "pull" in summary.execution.failed_step.lower() or "fast-forward" in summary.execution.failed_step.lower()
    assert summary.execution.backup_path is not None
    assert summary.ok is False

    # Backup file should still exist
    backup_file = repo / BACKUP_FILENAME
    assert backup_file.exists()
    backup_file.unlink()  # cleanup


def test_execute_stale_pr_copy_preserves_backup_on_verification_failure(tmp_path):
    repo, remote = setup_tracking_repo(tmp_path)

    # Add pr_file.py as a tracked file
    commit_file(repo, "pr_file.py", "base content\n", "add pr_file.py baseline")
    git(repo, "push", "origin", "main")

    # Push remote change
    other = init_repo(tmp_path / "other")
    git(other, "remote", "add", "origin", str(remote))
    git(other, "fetch", "origin")
    git(other, "checkout", "-b", "main", "--track", "origin/main")
    (other / "pr_file.py").write_text("PR content\n")
    git(other, "add", "pr_file.py")
    git(other, "commit", "-m", "Modify pr_file.py (merged PR)")
    git(other, "push", "origin", "main")

    git(repo, "fetch", "origin")
    (repo / "pr_file.py").write_text("PR content\n")

    from headless_pr_workflow.worktree_status import summarize_worktree_status
    local_status = summarize_worktree_status(str(repo))

    import headless_pr_workflow.post_merge_sync as pms_module
    original_check = pms_module._check_clean_status_excluding_backup

    def fake_dirty_status(worktree_path, backup_path):
        return "worktree not clean after fast-forward: M some_file.txt"

    pms_module._check_clean_status_excluding_backup = fake_dirty_status
    try:
        pr = build_pr_merge_state()
        summary = summarize_post_merge_sync(pr, local_status, ("pr_file.py",), mode="execute")
    finally:
        pms_module._check_clean_status_excluding_backup = original_check

    assert summary.execution is not None
    assert summary.execution.verification_result != "ok"
    assert summary.execution.cleanup_result is not None
    assert "preserved" in summary.execution.cleanup_result


# ---------------------------------------------------------------------------
# Tests: fetch_post_merge_pr_state (mocked subprocess)
# ---------------------------------------------------------------------------


def _mock_subprocess_run(stdout: str, returncode: int = 0, stderr: str = ""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_fetch_post_merge_pr_state_success(monkeypatch):
    payload = {
        "number": 42,
        "title": "My PR",
        "url": "https://github.com/owner/repo/pull/42",
        "state": "MERGED",
        "merged": True,
        "mergedAt": "2026-05-01T00:00:00Z",
        "mergeCommit": {"oid": "mergeoid"},
        "baseRefName": "main",
        "baseRefOid": "baseoid",
        "headRefName": "feature",
        "headRefOid": "headoid",
    }

    with patch("subprocess.run", return_value=_mock_subprocess_run(json.dumps(payload))):
        result = fetch_post_merge_pr_state("42", repo="owner/repo")

    assert result.number == 42
    assert result.merged is True
    assert result.merge_sha == "mergeoid"
    assert result.base_sha_after_merge == "baseoid"


def test_fetch_post_merge_pr_state_gh_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
        from headless_pr_workflow.github.pr_context import GHCommandError

        with pytest.raises(GHCommandError) as exc_info:
            fetch_post_merge_pr_state("42", repo="owner/repo")
        assert exc_info.value.error == "gh-not-found"


def test_fetch_post_merge_pr_state_nonzero_returncode():
    with patch("subprocess.run", return_value=_mock_subprocess_run("", returncode=1, stderr="not found")):
        from headless_pr_workflow.github.pr_context import GHCommandError

        with pytest.raises(GHCommandError):
            fetch_post_merge_pr_state("99", repo="owner/repo")


def test_fetch_pr_changed_paths_success(monkeypatch):
    with patch("subprocess.run", return_value=_mock_subprocess_run("src/foo.py\nsrc/bar.py\n")):
        paths = fetch_pr_changed_paths("42", repo="owner/repo")
    assert paths == ("src/foo.py", "src/bar.py")


def test_fetch_pr_changed_paths_empty():
    with patch("subprocess.run", return_value=_mock_subprocess_run("")):
        paths = fetch_pr_changed_paths("42", repo="owner/repo")
    assert paths == ()


# ---------------------------------------------------------------------------
# Tests: summarize_post_merge_sync — dry-run output contract
# ---------------------------------------------------------------------------


def test_summarize_dry_run_returns_plan_and_ok_for_safe_fast_forward():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        head_sha="pre",
        branch=build_branch_status(upstream_sha="post", ahead=0, behind=1),
        file_status=build_file_status(clean=True),
    )
    summary = summarize_post_merge_sync(pr, ws, (), mode="dry_run")

    assert summary.classification == "safe_fast_forward"
    assert summary.ok is True
    assert summary.mode == "dry_run"
    assert summary.execution is None
    assert len(summary.plan) > 0
    assert any("pull" in (step.command or "").lower() for step in summary.plan)


def test_summarize_dry_run_blocked_exits_not_ok():
    pr = build_pr_merge_state(merged=False, state="OPEN")
    ws = build_worktree_status()
    summary = summarize_post_merge_sync(pr, ws, (), mode="dry_run")

    assert summary.ok is False
    assert summary.classification == "blocked_not_merged"
    assert summary.blocking_reasons


def test_summarize_json_output_keys():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=True),
        branch=build_branch_status(ahead=0, behind=1),
    )
    summary = summarize_post_merge_sync(pr, ws, (), mode="dry_run")
    d = summary.to_dict()

    required_keys = {
        "command", "mode", "ok", "number", "title", "url", "state", "merged",
        "base_branch", "head_branch", "head_sha", "merge_sha", "base_sha_after_merge",
        "local", "status", "classification", "verified_pr_paths", "blocked_paths",
        "plan", "execution", "manual_commands", "warnings", "blocking_reasons",
    }
    assert required_keys <= set(d.keys())
    assert d["command"] == "post-merge-sync"


def test_summarize_stale_tracking_warning_included():
    pr = build_pr_merge_state()
    ws = build_worktree_status(
        file_status=build_file_status(clean=True),
        branch=build_branch_status(ahead=0, behind=1),
        warnings=("ahead/behind counts use the local upstream tracking ref and may be stale until fetch",),
    )
    summary = summarize_post_merge_sync(pr, ws, (), mode="dry_run")
    assert any("stale" in w for w in summary.warnings)
