"""CLI tests for hpw post-merge-sync command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from headless_pr_workflow import cli
from headless_pr_workflow.github.pr_context import GHCommandError
from headless_pr_workflow.post_merge_sync import PRMergeState, PostMergeSyncSummary, SyncPlanStep
from headless_pr_workflow.worktree_status import (
    BranchStatus,
    FileStatus,
    WorktreeStatusSummary,
)


# ---------------------------------------------------------------------------
# Test fixtures / builders
# ---------------------------------------------------------------------------


def build_merged_pr(
    *,
    number: int = 42,
    base_branch: str = "main",
    head_sha: str = "abc123",
    merge_sha: str | None = "merge999",
) -> PRMergeState:
    return PRMergeState(
        number=number,
        title="Test PR",
        url=f"https://github.com/owner/repo/pull/{number}",
        state="MERGED",
        merged=True,
        base_branch=base_branch,
        head_branch="feature/test",
        head_sha=head_sha,
        merge_sha=merge_sha,
        base_sha_after_merge=merge_sha,
    )


def build_open_pr(*, number: int = 42) -> PRMergeState:
    return PRMergeState(
        number=number,
        title="Open PR",
        url=f"https://github.com/owner/repo/pull/{number}",
        state="OPEN",
        merged=False,
        base_branch="main",
        head_branch="feature/test",
        head_sha="head111",
        merge_sha=None,
        base_sha_after_merge=None,
    )


def build_clean_worktree(*, head_sha: str = "local123", upstream_sha: str = "merge999") -> WorktreeStatusSummary:
    return WorktreeStatusSummary(
        command="worktree-status",
        ok=True,
        path="/repo",
        repository_root="/repo",
        worktree_path="/repo",
        head_sha=head_sha,
        branch=BranchStatus(
            name="main",
            detached=False,
            upstream="origin/main",
            upstream_sha=upstream_sha,
            ahead=0,
            behind=1 if head_sha != upstream_sha else 0,
            tracking_status="tracking",
        ),
        status=FileStatus(clean=True, staged=(), unstaged=(), untracked=(), conflicted=()),
        unpushed_commits=(),
        linked_worktrees=(),
        branch_in_use_by_other_worktree=False,
        warnings=("ahead/behind counts use the local upstream tracking ref and may be stale until fetch",),
        error=None,
    )


def build_dirty_worktree(*, unstaged: tuple[str, ...] = ("file.py",)) -> WorktreeStatusSummary:
    return WorktreeStatusSummary(
        command="worktree-status",
        ok=True,
        path="/repo",
        repository_root="/repo",
        worktree_path="/repo",
        head_sha="local123",
        branch=BranchStatus(
            name="main",
            detached=False,
            upstream="origin/main",
            upstream_sha="merge999",
            ahead=0,
            behind=1,
            tracking_status="tracking",
        ),
        status=FileStatus(
            clean=False, staged=(), unstaged=unstaged, untracked=(), conflicted=()
        ),
        unpushed_commits=(),
        linked_worktrees=(),
        branch_in_use_by_other_worktree=False,
        warnings=(),
        error=None,
    )


# ---------------------------------------------------------------------------
# Tests: JSON output
# ---------------------------------------------------------------------------


def test_json_output_dry_run_safe_fast_forward(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree())

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "post-merge-sync"
    assert output["mode"] == "dry_run"
    assert output["ok"] is True
    assert output["merged"] is True
    assert output["classification"] in ("safe_fast_forward", "already_synced")
    assert output["execution"] is None


def test_json_output_blocked_not_merged(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_open_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree())

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["classification"] == "blocked_not_merged"
    assert len(output["blocking_reasons"]) > 0


def test_json_output_github_error(monkeypatch, capsys):
    def fail_fetch(t, repo=None):
        raise GHCommandError(["gh", "pr", "view"], 1, "not found")

    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", fail_fetch)

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "error" in output


def test_json_output_all_required_keys_present(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree())

    cli.main(["post-merge-sync", "42", "--repo", "owner/repo", "--json"])

    output = json.loads(capsys.readouterr().out)
    required_keys = {
        "command", "mode", "ok", "number", "title", "url", "state", "merged",
        "base_branch", "head_branch", "head_sha", "merge_sha", "base_sha_after_merge",
        "local", "status", "classification", "verified_pr_paths", "blocked_paths",
        "plan", "execution", "manual_commands", "warnings", "blocking_reasons",
    }
    assert required_keys <= set(output.keys())


# ---------------------------------------------------------------------------
# Tests: human-readable output
# ---------------------------------------------------------------------------


def test_human_output_includes_key_fields(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree())

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "PR #42" in output
    assert "merged: true" in output
    assert "base branch: main" in output
    assert "classification:" in output


def test_human_output_blocked_not_merged_shows_reason(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_open_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree())

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "classification: blocked_not_merged" in output
    assert "blocking reasons:" in output


def test_human_output_shows_manual_commands_for_wrong_branch(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr(base_branch="main"))
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())

    wrong_branch = WorktreeStatusSummary(
        command="worktree-status",
        ok=True,
        path="/repo",
        repository_root="/repo",
        worktree_path="/repo",
        head_sha="sha",
        branch=BranchStatus(
            name="feature/other",
            detached=False,
            upstream="origin/feature/other",
            upstream_sha=None,
            ahead=0,
            behind=0,
            tracking_status="tracking",
        ),
        status=FileStatus(clean=True, staged=(), unstaged=(), untracked=(), conflicted=()),
        unpushed_commits=(),
        linked_worktrees=(),
        branch_in_use_by_other_worktree=False,
        warnings=(),
        error=None,
    )
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: wrong_branch)

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "blocked_not_base_branch" in output
    assert "manual commands:" in output
    assert "git checkout main" in output


def test_human_output_shows_plan_for_verified_stale_pr_copy(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ("pr_file.py",))

    # Patch check_paths_match_upstream to return all paths as matched
    import headless_pr_workflow.post_merge_sync as pms
    original = pms.check_paths_match_upstream
    pms.check_paths_match_upstream = lambda path, paths: frozenset(paths)
    try:
        monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_dirty_worktree(unstaged=("pr_file.py",)))
        exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo"])
    finally:
        pms.check_paths_match_upstream = original

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "verified_stale_pr_copy" in output
    assert "sync plan:" in output
    assert "backup" in output.lower() or "restore" in output.lower()


# ---------------------------------------------------------------------------
# Tests: execute mode
# ---------------------------------------------------------------------------


def test_execute_flag_activates_execute_mode(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree())

    # Mock _run_git_mutation to avoid actual git operations
    import headless_pr_workflow.post_merge_sync as pms
    original = pms._run_git_mutation
    pms._run_git_mutation = lambda path, *args: ""
    try:
        exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo", "--execute", "--json"])
    finally:
        pms._run_git_mutation = original

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "execute"
    assert output["execution"] is not None


def test_execute_mode_json_includes_execution_result(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr(merge_sha="local123"))
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    # Head SHA matches merge SHA -> already_synced
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree(head_sha="local123", upstream_sha="local123"))

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo", "--execute", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "execute"
    assert output["classification"] == "already_synced"
    assert output["execution"] is not None
    assert output["execution"]["failed_step"] is None


def test_execute_mode_blocked_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_open_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree())

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo", "--execute"])

    assert exit_code == 1


# ---------------------------------------------------------------------------
# Tests: --worktree flag
# ---------------------------------------------------------------------------


def test_worktree_flag_passes_path_to_worktree_status(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr())
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())

    received_paths: list[str | None] = []

    def capture_path(path):
        received_paths.append(path)
        return build_clean_worktree()

    monkeypatch.setattr(cli, "summarize_worktree_status", capture_path)

    cli.main(["post-merge-sync", "42", "--repo", "owner/repo", "--worktree", "/some/path"])

    assert received_paths == ["/some/path"]


# ---------------------------------------------------------------------------
# Tests: failure handling
# ---------------------------------------------------------------------------


def test_github_fetch_failure_exits_nonzero(monkeypatch, capsys):
    def raise_error(t, repo=None):
        raise GHCommandError(["gh", "pr", "view"], 1, "HTTP 404 not found")

    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", raise_error)

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "404" in err or "not found" in err.lower() or err  # error is printed to stderr


def test_pr_changed_paths_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", lambda t, repo=None: build_merged_pr())

    def raise_error(t, repo=None):
        raise GHCommandError(["gh", "pr", "diff"], 1, "cannot diff merged PR")

    monkeypatch.setattr(cli, "fetch_pr_changed_paths", raise_error)

    exit_code = cli.main(["post-merge-sync", "42", "--repo", "owner/repo"])

    assert exit_code == 1


def test_missing_repo_flag_still_passes_none(monkeypatch, capsys):
    received_repos: list[str | None] = []

    def capture_repo(t, repo=None):
        received_repos.append(repo)
        return build_merged_pr()

    monkeypatch.setattr(cli, "fetch_post_merge_pr_state", capture_repo)
    monkeypatch.setattr(cli, "fetch_pr_changed_paths", lambda t, repo=None: ())
    monkeypatch.setattr(cli, "summarize_worktree_status", lambda p: build_clean_worktree())

    cli.main(["post-merge-sync", "42"])

    assert received_repos == [None]


def test_usage_error_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["post-merge-sync", "--not-a-real-flag"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Tests: catalog marks post-merge-sync implemented
# ---------------------------------------------------------------------------


def test_catalog_marks_post_merge_sync_implemented(capsys):
    exit_code = cli.main(["catalog"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "post-merge-sync\tP1-high\tI-post-merge\taction\tcore\timplemented" in output
