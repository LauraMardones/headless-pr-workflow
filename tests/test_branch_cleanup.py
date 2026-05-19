"""Unit tests for branch_cleanup: merged-state verification and candidate classification."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from headless_pr_workflow.branch_cleanup import (
    BranchCleanupSummary,
    CleanupCandidate,
    WorktreeInfo,
    classify_local_candidate,
    classify_remote_candidate,
    delete_local_branch,
    delete_remote_branch,
    fetch_github_compare_ahead_by,
    find_local_branch,
    find_remote_tracking_branch,
    has_unique_content,
    inspect_worktree_dirty,
    is_ancestry_merged,
    summarize_branch_cleanup,
)
from headless_pr_workflow.post_merge_sync import PRMergeState
from headless_pr_workflow.worktree_status import GitCommandError, LinkedWorktree


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_merged_pr(
    *,
    number: int = 47,
    base_branch: str = "main",
    head_branch: str = "feature/my-thing",
) -> PRMergeState:
    return PRMergeState(
        number=number,
        title="Test PR",
        url=f"https://github.com/owner/repo/pull/{number}",
        state="MERGED",
        merged=True,
        base_branch=base_branch,
        head_branch=head_branch,
        head_sha="head123",
        merge_sha="merge456",
        base_sha_after_merge="base789",
    )


def build_open_pr(*, number: int = 47) -> PRMergeState:
    return PRMergeState(
        number=number,
        title="Open PR",
        url=f"https://github.com/owner/repo/pull/{number}",
        state="OPEN",
        merged=False,
        base_branch="main",
        head_branch="feature/my-thing",
        head_sha="head123",
        merge_sha=None,
        base_sha_after_merge=None,
    )


def build_linked_worktree(path: str, branch: str | None = None) -> LinkedWorktree:
    return LinkedWorktree(path=path, branch=branch, head_sha="sha123")


# ---------------------------------------------------------------------------
# Tests: fetch_github_compare_ahead_by
# ---------------------------------------------------------------------------


def test_compare_returns_ahead_by_zero(monkeypatch):
    result = MagicMock()
    result.returncode = 0
    result.stdout = json.dumps({"ahead_by": 0, "behind_by": 5})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: result)

    assert fetch_github_compare_ahead_by("owner/repo", "main", "feature/x") == 0


def test_compare_returns_ahead_by_nonzero(monkeypatch):
    result = MagicMock()
    result.returncode = 0
    result.stdout = json.dumps({"ahead_by": 3, "behind_by": 0})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: result)

    assert fetch_github_compare_ahead_by("owner/repo", "main", "feature/x") == 3


def test_compare_returns_none_on_api_failure(monkeypatch):
    result = MagicMock()
    result.returncode = 1
    result.stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: result)

    assert fetch_github_compare_ahead_by("owner/repo", "main", "feature/x") is None


def test_compare_returns_none_on_invalid_json(monkeypatch):
    result = MagicMock()
    result.returncode = 0
    result.stdout = "not-json"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: result)

    assert fetch_github_compare_ahead_by("owner/repo", "main", "feature/x") is None


def test_compare_returns_none_when_gh_not_found(monkeypatch):
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=FileNotFoundError))

    assert fetch_github_compare_ahead_by("owner/repo", "main", "feature/x") is None


# ---------------------------------------------------------------------------
# Tests: find_local_branch / find_remote_tracking_branch
# ---------------------------------------------------------------------------


def test_find_local_branch_present(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git_optional",
        lambda path, *args: "  feature/my-thing\n",
    )
    assert find_local_branch("/repo", "feature/my-thing") is True


def test_find_local_branch_absent(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git_optional",
        lambda path, *args: "",
    )
    assert find_local_branch("/repo", "feature/my-thing") is False


def test_find_remote_tracking_branch_present(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git_optional",
        lambda path, *args: "  origin/feature/my-thing\n",
    )
    assert find_remote_tracking_branch("/repo", "feature/my-thing") is True


def test_find_remote_tracking_branch_absent(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git_optional",
        lambda path, *args: None,
    )
    assert find_remote_tracking_branch("/repo", "feature/my-thing") is False


# ---------------------------------------------------------------------------
# Tests: is_ancestry_merged / has_unique_content
# ---------------------------------------------------------------------------


def test_is_ancestry_merged_when_present(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git_optional",
        lambda path, *args: "  main\n  feature/my-thing\n",
    )
    assert is_ancestry_merged("/repo", "feature/my-thing", "origin/main") is True


def test_is_ancestry_merged_when_not_present(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git_optional",
        lambda path, *args: "  main\n",
    )
    assert is_ancestry_merged("/repo", "feature/my-thing", "origin/main") is False


def test_is_ancestry_merged_when_output_is_none(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git_optional",
        lambda path, *args: None,
    )
    assert is_ancestry_merged("/repo", "feature/my-thing", "origin/main") is False


def test_has_unique_content_true_when_diff_nonempty(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git",
        lambda path, *args: "diff --git a/foo.py b/foo.py\n+something\n",
    )
    assert has_unique_content("/repo", "feature/x", "origin/main") is True


def test_has_unique_content_false_when_diff_empty(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git",
        lambda path, *args: "",
    )
    assert has_unique_content("/repo", "feature/x", "origin/main") is False


def test_has_unique_content_true_on_git_error(monkeypatch):
    def raise_err(path, *args):
        raise GitCommandError(command=("git",), returncode=1, stderr="error")

    monkeypatch.setattr("headless_pr_workflow.branch_cleanup._git", raise_err)
    assert has_unique_content("/repo", "feature/x", "origin/main") is True


# ---------------------------------------------------------------------------
# Tests: inspect_worktree_dirty
# ---------------------------------------------------------------------------


def test_inspect_worktree_dirty_returns_false_for_clean(monkeypatch):
    from headless_pr_workflow.worktree_status import FileStatus
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._file_status",
        lambda path: FileStatus(clean=True, staged=(), unstaged=(), untracked=(), conflicted=()),
    )
    assert inspect_worktree_dirty("/repo") is False


def test_inspect_worktree_dirty_returns_true_for_dirty(monkeypatch):
    from headless_pr_workflow.worktree_status import FileStatus
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._file_status",
        lambda path: FileStatus(clean=False, staged=("file.py",), unstaged=(), untracked=(), conflicted=()),
    )
    assert inspect_worktree_dirty("/repo") is True


def test_inspect_worktree_dirty_returns_true_on_error(monkeypatch):
    def raise_err(path):
        raise GitCommandError(command=("git",), returncode=1, stderr="error")

    monkeypatch.setattr("headless_pr_workflow.branch_cleanup._file_status", raise_err)
    assert inspect_worktree_dirty("/repo") is True


# ---------------------------------------------------------------------------
# Tests: classify_local_candidate
# ---------------------------------------------------------------------------


def test_classify_local_checked_out_in_clean_worktree(monkeypatch):
    worktree = build_linked_worktree("/other-worktree", "feature/my-thing")
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.inspect_worktree_dirty",
        lambda path: False,
    )
    result = classify_local_candidate("/repo", "feature/my-thing", "origin/main", (worktree,))
    assert result.disposition == "kept"
    assert "worktree" in (result.reason or "")
    assert result.worktree == "/other-worktree"


def test_classify_local_checked_out_in_dirty_worktree(monkeypatch):
    worktree = build_linked_worktree("/other-worktree", "feature/my-thing")
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.inspect_worktree_dirty",
        lambda path: True,
    )
    result = classify_local_candidate("/repo", "feature/my-thing", "origin/main", (worktree,))
    assert result.disposition == "skipped"
    assert "dirty" in (result.reason or "")


def test_classify_local_ancestry_merged(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.is_ancestry_merged",
        lambda repo_path, branch, base_ref: True,
    )
    result = classify_local_candidate("/repo", "feature/my-thing", "origin/main", ())
    assert result.disposition == "safe_to_delete"
    assert result.content_verified is False


def test_classify_local_squash_merged_no_unique_content(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.is_ancestry_merged",
        lambda repo_path, branch, base_ref: False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.has_unique_content",
        lambda repo_path, branch, base_ref: False,
    )
    result = classify_local_candidate("/repo", "feature/my-thing", "origin/main", ())
    assert result.disposition == "safe_to_delete"
    assert result.content_verified is True


def test_classify_local_has_unique_content(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.is_ancestry_merged",
        lambda repo_path, branch, base_ref: False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.has_unique_content",
        lambda repo_path, branch, base_ref: True,
    )
    result = classify_local_candidate("/repo", "feature/my-thing", "origin/main", ())
    assert result.disposition == "kept"
    assert result.content_verified is True
    assert result.reason is not None


# ---------------------------------------------------------------------------
# Tests: classify_remote_candidate
# ---------------------------------------------------------------------------


def test_classify_remote_checked_out_in_worktree():
    worktree = build_linked_worktree("/other-worktree", "feature/my-thing")
    result = classify_remote_candidate("feature/my-thing", "main", "owner/repo", (worktree,))
    assert result.disposition == "kept"
    assert result.worktree == "/other-worktree"


def test_classify_remote_ahead_by_zero(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_github_compare_ahead_by",
        lambda repo, base, head: 0,
    )
    result = classify_remote_candidate("feature/my-thing", "main", "owner/repo", ())
    assert result.disposition == "safe_to_delete"
    assert result.content_verified is True
    assert result.ahead_by == 0


def test_classify_remote_ahead_by_nonzero(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_github_compare_ahead_by",
        lambda repo, base, head: 2,
    )
    result = classify_remote_candidate("feature/my-thing", "main", "owner/repo", ())
    assert result.disposition == "kept"
    assert result.ahead_by == 2
    assert "2" in (result.reason or "")


def test_classify_remote_compare_unavailable(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_github_compare_ahead_by",
        lambda repo, base, head: None,
    )
    result = classify_remote_candidate("feature/my-thing", "main", "owner/repo", ())
    assert result.disposition == "skipped"
    assert "unavailable" in (result.reason or "")


# ---------------------------------------------------------------------------
# Tests: delete_local_branch / delete_remote_branch
# ---------------------------------------------------------------------------


def test_delete_local_branch_success(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git",
        lambda path, *args: "",
    )
    ok, err = delete_local_branch("/repo", "feature/x", content_verified=False)
    assert ok is True
    assert err is None


def test_delete_local_branch_uses_uppercase_d_for_content_verified(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git",
        lambda path, *args: calls.append(args) or "",
    )
    delete_local_branch("/repo", "feature/x", content_verified=True)
    assert "-D" in calls[0]


def test_delete_local_branch_uses_lowercase_d_for_ancestry(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git",
        lambda path, *args: calls.append(args) or "",
    )
    delete_local_branch("/repo", "feature/x", content_verified=False)
    assert "-d" in calls[0]


def test_delete_local_branch_failure(monkeypatch):
    def raise_err(path, *args):
        raise GitCommandError(command=("git",), returncode=1, stderr="not fully merged")

    monkeypatch.setattr("headless_pr_workflow.branch_cleanup._git", raise_err)
    ok, err = delete_local_branch("/repo", "feature/x", content_verified=False)
    assert ok is False
    assert "not fully merged" in (err or "")


def test_delete_remote_branch_success(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git",
        lambda path, *args: "",
    )
    ok, err = delete_remote_branch("/repo", "feature/x")
    assert ok is True
    assert err is None


def test_delete_remote_branch_failure(monkeypatch):
    def raise_err(path, *args):
        raise GitCommandError(command=("git",), returncode=1, stderr="permission denied")

    monkeypatch.setattr("headless_pr_workflow.branch_cleanup._git", raise_err)
    ok, err = delete_remote_branch("/repo", "feature/x")
    assert ok is False
    assert "permission denied" in (err or "")


# ---------------------------------------------------------------------------
# Tests: summarize_branch_cleanup (integration-style, fully mocked)
# ---------------------------------------------------------------------------


def _patch_local_git_env(monkeypatch, *, repo_root: str = "/repo") -> None:
    """Patch out Git subprocess helpers so summarize_branch_cleanup does not call real Git."""
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._git",
        lambda path, *args: repo_root if args[:2] == ("rev-parse", "--show-toplevel") else "",
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup._linked_worktrees",
        lambda path: (),
    )


def test_summarize_pr_not_merged(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_open_pr(),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.ok is False
    assert result.target_type == "pr"
    assert result.merged is False
    assert len(result.blocking_reasons) > 0
    assert "not merged" in result.blocking_reasons[0].lower()


def test_summarize_github_fetch_error(monkeypatch):
    from headless_pr_workflow.github.pr_context import GHCommandError

    def fail(*a, **kw):
        raise GHCommandError(["gh"], 1, "not found")

    monkeypatch.setattr("headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state", fail)
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.ok is False
    assert len(result.blocking_reasons) > 0


def test_summarize_no_local_or_remote_branches(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.ok is True
    assert len(result.candidates) == 0
    assert "No local or remote branches found" in result.blocking_reasons[0]


def test_summarize_local_branch_ancestry_merged_dry_run(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_local_candidate",
        lambda repo_path, branch, base_ref, linked_worktrees: CleanupCandidate(
            branch=branch, type="local", disposition="safe_to_delete",
            reason=None, worktree=None, content_verified=False, ahead_by=None,
        ),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.ok is True
    assert result.mode == "dry_run"
    assert len(result.candidates) == 1
    assert result.candidates[0].disposition == "safe_to_delete"
    assert len(result.deleted) == 0


def test_summarize_local_branch_checked_out_in_worktree(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_local_candidate",
        lambda repo_path, branch, base_ref, linked_worktrees: CleanupCandidate(
            branch=branch, type="local", disposition="kept",
            reason="branch currently checked out in a worktree",
            worktree="/some-worktree", content_verified=False, ahead_by=None,
        ),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.ok is True
    assert len(result.kept) == 1
    assert result.kept[0]["branch"] == "feature/my-thing"


def test_summarize_dirty_worktree_blocks_deletion(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_local_candidate",
        lambda repo_path, branch, base_ref, linked_worktrees: CleanupCandidate(
            branch=branch, type="local", disposition="skipped",
            reason="branch checked out in dirty worktree",
            worktree="/dirty-worktree", content_verified=False, ahead_by=None,
        ),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.ok is True
    assert result.candidates[0].disposition == "skipped"


def test_summarize_squash_merged_local_branch(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_local_candidate",
        lambda repo_path, branch, base_ref, linked_worktrees: CleanupCandidate(
            branch=branch, type="local", disposition="safe_to_delete",
            reason=None, worktree=None, content_verified=True, ahead_by=None,
        ),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.candidates[0].content_verified is True
    assert result.candidates[0].disposition == "safe_to_delete"


def test_summarize_remote_branch_ahead_by_zero(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_remote_candidate",
        lambda branch, base_branch, repo, linked_worktrees: CleanupCandidate(
            branch=branch, type="remote", disposition="safe_to_delete",
            reason=None, worktree=None, content_verified=True, ahead_by=0,
        ),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.candidates[0].disposition == "safe_to_delete"
    assert result.candidates[0].ahead_by == 0


def test_summarize_remote_branch_ahead_by_nonzero(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_remote_candidate",
        lambda branch, base_branch, repo, linked_worktrees: CleanupCandidate(
            branch=branch, type="remote", disposition="kept",
            reason="remote branch is 2 commit(s) ahead of base branch",
            worktree=None, content_verified=True, ahead_by=2,
        ),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    assert result.candidates[0].disposition == "kept"
    assert result.kept[0]["branch"] == "feature/my-thing"


def test_summarize_execute_mode_deletes_local(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_local_candidate",
        lambda repo_path, branch, base_ref, linked_worktrees: CleanupCandidate(
            branch=branch, type="local", disposition="safe_to_delete",
            reason=None, worktree=None, content_verified=False, ahead_by=None,
        ),
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.delete_local_branch",
        lambda repo_path, branch, content_verified: (True, None),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo", mode="execute")
    assert result.mode == "execute"
    assert "feature/my-thing" in result.deleted
    assert result.ok is True


def test_summarize_execute_mode_permission_failure_reports_manual_command(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_remote_candidate",
        lambda branch, base_branch, repo, linked_worktrees: CleanupCandidate(
            branch=branch, type="remote", disposition="safe_to_delete",
            reason=None, worktree=None, content_verified=True, ahead_by=0,
        ),
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.delete_remote_branch",
        lambda repo_path, branch, remote="origin": (False, "permission denied (publickey)"),
    )
    result = summarize_branch_cleanup("47", repo="owner/repo", mode="execute")
    assert result.ok is False
    assert len(result.manual_commands) > 0
    assert "git push origin --delete" in result.manual_commands[0]
    assert len(result.kept) > 0


def test_summarize_branch_target_notes_reduced_verification(monkeypatch):
    from headless_pr_workflow.github import GHCommandError as GHErr

    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: (_ for _ in ()).throw(AssertionError("should not be called for branch target")),
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_repo_default_branch",
        lambda repo=None: "main",
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    result = summarize_branch_cleanup("feature/my-thing", repo="owner/repo")
    assert result.target_type == "branch"
    assert result.number is None
    assert any("merged-state verification not available" in w for w in result.warnings)


def test_summarize_branch_target_not_fully_merged_is_blocked(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_repo_default_branch",
        lambda repo=None: "main",
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: True,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.classify_local_candidate",
        lambda repo_path, branch, base_ref, linked_worktrees: CleanupCandidate(
            branch=branch, type="local", disposition="kept",
            reason="branch has unique content not present in base branch",
            worktree=None, content_verified=True, ahead_by=None,
        ),
    )
    result = summarize_branch_cleanup("feature/my-thing", repo="owner/repo")
    assert result.target_type == "branch"
    assert result.candidates[0].disposition == "kept"
    assert result.kept[0]["branch"] == "feature/my-thing"


def test_summarize_json_output_has_required_keys(monkeypatch):
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.fetch_post_merge_pr_state",
        lambda target, repo=None: build_merged_pr(),
    )
    _patch_local_git_env(monkeypatch)
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_local_branch",
        lambda repo_path, branch: False,
    )
    monkeypatch.setattr(
        "headless_pr_workflow.branch_cleanup.find_remote_tracking_branch",
        lambda repo_path, branch, remote="origin": False,
    )
    result = summarize_branch_cleanup("47", repo="owner/repo")
    d = result.to_dict()
    required = {
        "command", "mode", "ok", "target_type",
        "number", "title", "url", "state", "merged", "base_branch", "head_branch",
        "candidates", "worktrees_checked", "deleted", "kept",
        "manual_commands", "warnings", "blocking_reasons",
    }
    assert required <= set(d.keys())
    assert d["command"] == "branch-cleanup"
