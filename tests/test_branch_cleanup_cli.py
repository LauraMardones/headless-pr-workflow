"""CLI tests for hpw branch-cleanup command."""

from __future__ import annotations

import json

import pytest

from headless_pr_workflow import cli
from headless_pr_workflow.branch_cleanup import BranchCleanupSummary, CleanupCandidate, WorktreeInfo


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_summary(
    *,
    ok: bool = True,
    mode: str = "dry_run",
    target_type: str = "pr",
    number: int | None = 47,
    merged: bool | None = True,
    head_branch: str = "feature/my-thing",
    base_branch: str = "main",
    candidates: tuple[CleanupCandidate, ...] = (),
    deleted: tuple[str, ...] = (),
    kept: tuple[dict, ...] = (),
    manual_commands: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
    blocking_reasons: tuple[str, ...] = (),
    worktrees_checked: tuple[WorktreeInfo, ...] = (),
) -> BranchCleanupSummary:
    return BranchCleanupSummary(
        command="branch-cleanup",
        mode=mode,
        ok=ok,
        target_type=target_type,
        number=number,
        title="Test PR" if number else None,
        url=f"https://github.com/owner/repo/pull/{number}" if number else None,
        state="MERGED" if merged else "OPEN",
        merged=merged,
        base_branch=base_branch,
        head_branch=head_branch,
        candidates=candidates,
        worktrees_checked=worktrees_checked,
        deleted=deleted,
        kept=kept,
        manual_commands=manual_commands,
        warnings=warnings,
        blocking_reasons=blocking_reasons,
    )


def safe_to_delete_local(branch: str = "feature/my-thing") -> CleanupCandidate:
    return CleanupCandidate(
        branch=branch, type="local", disposition="safe_to_delete",
        reason=None, worktree=None, content_verified=False, ahead_by=None,
    )


def safe_to_delete_remote(branch: str = "feature/my-thing") -> CleanupCandidate:
    return CleanupCandidate(
        branch=branch, type="remote", disposition="safe_to_delete",
        reason=None, worktree=None, content_verified=True, ahead_by=0,
    )


def deleted_local(branch: str = "feature/my-thing") -> CleanupCandidate:
    return CleanupCandidate(
        branch=branch, type="local", disposition="deleted",
        reason=None, worktree=None, content_verified=False, ahead_by=None,
    )


def kept_candidate(branch: str = "feature/my-thing", reason: str = "checked out in worktree") -> CleanupCandidate:
    return CleanupCandidate(
        branch=branch, type="local", disposition="kept",
        reason=reason, worktree="/worktree", content_verified=False, ahead_by=None,
    )


# ---------------------------------------------------------------------------
# Tests: JSON output
# ---------------------------------------------------------------------------


def test_json_output_dry_run_safe_to_delete(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            candidates=(safe_to_delete_local(),),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--json"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "branch-cleanup"
    assert output["mode"] == "dry_run"
    assert output["ok"] is True
    assert len(output["candidates"]) == 1
    assert output["candidates"][0]["disposition"] == "safe_to_delete"
    assert output["deleted"] == []


def test_json_output_has_all_required_keys(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(),
    )
    cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--json"])
    output = json.loads(capsys.readouterr().out)
    required = {
        "command", "mode", "ok", "target_type",
        "number", "title", "url", "state", "merged", "base_branch", "head_branch",
        "candidates", "worktrees_checked", "deleted", "kept",
        "manual_commands", "warnings", "blocking_reasons",
    }
    assert required <= set(output.keys())


def test_json_output_pr_not_merged_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            ok=False,
            merged=False,
            blocking_reasons=("PR #47 is not merged on GitHub (state=OPEN).",),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--json"])
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert len(output["blocking_reasons"]) > 0


def test_json_output_execute_mode(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            mode=mode,
            candidates=(deleted_local(),),
            deleted=("feature/my-thing",),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--execute", "--json"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "execute"
    assert "feature/my-thing" in output["deleted"]


def test_json_output_execute_permission_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            mode=mode,
            ok=False,
            candidates=(CleanupCandidate(
                branch="feature/my-thing", type="remote", disposition="kept",
                reason="remote deletion failed (permissions/auth): permission denied",
                worktree=None, content_verified=True, ahead_by=0,
            ),),
            kept=({"branch": "feature/my-thing", "reason": "remote deletion failed (permissions/auth): permission denied"},),
            manual_commands=("git push origin --delete feature/my-thing",),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--execute", "--json"])
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert len(output["manual_commands"]) > 0
    assert "git push origin --delete" in output["manual_commands"][0]


def test_json_output_branch_target_with_warnings(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            target_type="branch",
            number=None,
            merged=None,
            warnings=("GitHub PR merged-state verification not available; using local Git ancestry and GitHub compare API only.",),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "feature/my-thing", "--repo", "owner/repo", "--json"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["target_type"] == "branch"
    assert output["number"] is None
    assert any("merged-state verification" in w for w in output["warnings"])


# ---------------------------------------------------------------------------
# Tests: human-readable output
# ---------------------------------------------------------------------------


def test_human_output_includes_key_fields(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            candidates=(safe_to_delete_local(),),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "47", "--repo", "owner/repo"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "PR #47" in output
    assert "feature/my-thing" in output
    assert "safe to delete" in output or "safe_to_delete" in output


def test_human_output_not_merged_shows_blocking_reason(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            ok=False,
            merged=False,
            blocking_reasons=("PR #47 is not merged on GitHub (state=OPEN).",),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "47", "--repo", "owner/repo"])
    assert exit_code == 1
    output = capsys.readouterr().out
    assert "blocking reasons" in output or "not merged" in output.lower()


def test_human_output_manual_commands_shown(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            ok=False,
            manual_commands=("git push origin --delete feature/my-thing",),
        ),
    )
    cli.main(["branch-cleanup", "47", "--repo", "owner/repo"])
    output = capsys.readouterr().out
    assert "manual commands" in output
    assert "git push origin --delete" in output


def test_human_output_kept_branches_shown(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            candidates=(kept_candidate(),),
            kept=({"branch": "feature/my-thing", "reason": "checked out in worktree"},),
        ),
    )
    cli.main(["branch-cleanup", "47", "--repo", "owner/repo"])
    output = capsys.readouterr().out
    assert "kept" in output
    assert "feature/my-thing" in output


def test_human_output_deleted_branches_shown(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            mode="execute",
            candidates=(deleted_local(),),
            deleted=("feature/my-thing",),
        ),
    )
    cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--execute"])
    output = capsys.readouterr().out
    assert "deleted" in output
    assert "feature/my-thing" in output


def test_human_output_warnings_shown(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            target_type="branch",
            number=None,
            merged=None,
            warnings=("GitHub PR merged-state verification not available; using local Git ancestry and GitHub compare API only.",),
            blocking_reasons=("No local or remote branches found for head branch.",),
        ),
    )
    cli.main(["branch-cleanup", "feature/my-thing", "--repo", "owner/repo"])
    output = capsys.readouterr().out
    assert "warnings" in output
    assert "merged-state verification" in output


def test_human_output_worktrees_shown(monkeypatch, capsys):
    worktree = WorktreeInfo(path="/other-worktree", branch="feature/my-thing", dirty=True)
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            worktrees_checked=(worktree,),
            candidates=(kept_candidate(reason="branch checked out in dirty worktree"),),
            kept=({"branch": "feature/my-thing", "reason": "branch checked out in dirty worktree"},),
        ),
    )
    cli.main(["branch-cleanup", "47", "--repo", "owner/repo"])
    output = capsys.readouterr().out
    assert "/other-worktree" in output


# ---------------------------------------------------------------------------
# Tests: --execute flag behavior
# ---------------------------------------------------------------------------


def test_execute_flag_passes_execute_mode(monkeypatch, capsys):
    received_modes: list[str] = []

    def capture(target, repo=None, mode="dry_run", repo_path=None):
        received_modes.append(mode)
        return build_summary(mode=mode)

    monkeypatch.setattr(cli, "summarize_branch_cleanup", capture)
    cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--execute"])
    assert received_modes == ["execute"]


def test_without_execute_flag_uses_dry_run(monkeypatch, capsys):
    received_modes: list[str] = []

    def capture(target, repo=None, mode="dry_run", repo_path=None):
        received_modes.append(mode)
        return build_summary(mode=mode)

    monkeypatch.setattr(cli, "summarize_branch_cleanup", capture)
    cli.main(["branch-cleanup", "47", "--repo", "owner/repo"])
    assert received_modes == ["dry_run"]


def test_execute_all_clean_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            mode="execute",
            deleted=("feature/my-thing",),
            candidates=(deleted_local(),),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--execute"])
    assert exit_code == 0


def test_execute_blocked_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "summarize_branch_cleanup",
        lambda target, repo=None, mode="dry_run", repo_path=None: build_summary(
            ok=False,
            mode="execute",
            blocking_reasons=("PR #47 is not merged on GitHub (state=OPEN).",),
        ),
    )
    exit_code = cli.main(["branch-cleanup", "47", "--repo", "owner/repo", "--execute"])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Tests: error handling / argument validation
# ---------------------------------------------------------------------------


def test_missing_target_exits_nonzero(capsys):
    exit_code = cli.main(["branch-cleanup", "--repo", "owner/repo"])
    assert exit_code in (1, 2)


def test_missing_repo_exits_nonzero(monkeypatch, capsys):
    exit_code = cli.main(["branch-cleanup", "47"])
    assert exit_code in (1, 2)


def test_missing_repo_json_mode_reports_error(monkeypatch, capsys):
    exit_code = cli.main(["branch-cleanup", "47", "--json"])
    assert exit_code in (1, 2)
    out = capsys.readouterr().out
    if out:
        data = json.loads(out)
        assert data["ok"] is False


def test_usage_error_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["branch-cleanup", "--not-a-real-flag"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Tests: catalog marks branch-cleanup implemented
# ---------------------------------------------------------------------------


def test_catalog_marks_branch_cleanup_implemented(capsys):
    exit_code = cli.main(["catalog"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "branch-cleanup\tP2-medium\tI-post-merge\taction\tcore\timplemented" in output
