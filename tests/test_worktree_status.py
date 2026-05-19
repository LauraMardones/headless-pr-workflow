import json
import subprocess
from pathlib import Path

import pytest

from headless_pr_workflow import cli
from headless_pr_workflow.worktree_status import (
    STALE_TRACKING_WARNING,
    LinkedWorktree,
    _branch_in_use_by_other_worktree,
    summarize_worktree_status,
)


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
    return path


def commit_file(repo: Path, name: str = "tracked.txt", content: str = "base\n", message: str = "commit") -> str:
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


def test_clean_tracking_worktree_reports_core_facts(tmp_path):
    repo, _remote = setup_tracking_repo(tmp_path)

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert Path(summary.repository_root) == repo
    assert Path(summary.worktree_path) == repo
    assert summary.branch.name == "main"
    assert summary.branch.detached is False
    assert summary.branch.upstream == "origin/main"
    assert summary.branch.upstream_sha == summary.head_sha
    assert summary.branch.ahead == 0
    assert summary.branch.behind == 0
    assert summary.branch.tracking_status == "tracking"
    assert summary.status.clean is True
    assert summary.unpushed_commits == ()
    assert STALE_TRACKING_WARNING in summary.warnings


def test_distinguishes_staged_unstaged_untracked_and_conflicted_paths(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit_file(repo, "staged.txt", "base\n")
    commit_file(repo, "unstaged.txt", "base\n")
    commit_file(repo, "conflict.txt", "base\n")
    git(repo, "checkout", "-b", "side")
    (repo / "conflict.txt").write_text("side\n")
    git(repo, "commit", "-am", "side change")
    git(repo, "checkout", "main")
    (repo / "conflict.txt").write_text("main\n")
    git(repo, "commit", "-am", "main change")
    git(repo, "merge", "side", check=False)
    (repo / "staged.txt").write_text("staged\n")
    git(repo, "add", "staged.txt")
    (repo / "unstaged.txt").write_text("unstaged\n")
    (repo / "untracked.txt").write_text("new\n")

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.status.clean is False
    assert summary.status.staged == ("staged.txt",)
    assert summary.status.unstaged == ("unstaged.txt",)
    assert summary.status.untracked == ("untracked.txt",)
    assert summary.status.conflicted == ("conflict.txt",)


def test_missing_upstream_is_reported_as_fact_not_failure(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit_file(repo)

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.branch.upstream is None
    assert summary.branch.ahead is None
    assert summary.branch.behind is None
    assert summary.branch.tracking_status == "missing_upstream"
    assert summary.unpushed_commits == ()
    assert "current branch has no upstream tracking branch" in summary.warnings


def test_detached_head_reports_detached_without_failing(tmp_path):
    repo = init_repo(tmp_path / "repo")
    head_sha = commit_file(repo)
    git(repo, "checkout", "--detach", head_sha)

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.head_sha == head_sha
    assert summary.branch.name is None
    assert summary.branch.detached is True
    assert summary.branch.tracking_status == "detached"
    assert summary.branch_in_use_by_other_worktree is None


def test_unpushed_commit_and_stale_tracking_counts_use_local_tracking_ref(tmp_path):
    repo, remote = setup_tracking_repo(tmp_path)
    commit_file(repo, "local.txt", "local\n", "local commit")
    other = init_repo(tmp_path / "other")
    git(other, "remote", "add", "origin", str(remote))
    git(other, "pull", "origin", "main")
    git(other, "checkout", "main")
    commit_file(other, "remote.txt", "remote\n", "remote commit")
    git(other, "push", "origin", "main")

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.branch.ahead == 1
    assert summary.branch.behind == 0
    assert [commit.subject for commit in summary.unpushed_commits] == ["local commit"]
    assert STALE_TRACKING_WARNING in summary.warnings


def test_multiple_unpushed_commits_are_all_reported_with_correct_subjects(tmp_path):
    repo, _remote = setup_tracking_repo(tmp_path)
    sha1 = commit_file(repo, "a.txt", "a\n", "first unpushed")
    sha2 = commit_file(repo, "b.txt", "b\n", "second unpushed")

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.branch.ahead == 2
    subjects = [c.subject for c in summary.unpushed_commits]
    shas = [c.sha for c in summary.unpushed_commits]
    assert subjects == ["second unpushed", "first unpushed"]
    assert sha2 in shas
    assert sha1 in shas
    assert all(len(s) == 40 for s in shas)


def test_linked_worktrees_are_reported_and_branch_in_use_helper_detects_other_path(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit_file(repo)
    git(repo, "branch", "feature")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", str(linked), "feature")

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.branch.name == "main"
    assert summary.branch_in_use_by_other_worktree is False
    assert any(Path(worktree.path) == linked and worktree.branch == "feature" for worktree in summary.linked_worktrees)
    assert (
        _branch_in_use_by_other_worktree(
            current_worktree=str(repo),
            branch_name="main",
            linked_worktrees=(LinkedWorktree(path=str(linked), branch="main", head_sha="abc123"),),
        )
        is True
    )


def test_unborn_empty_repository_reports_available_facts(tmp_path):
    repo = init_repo(tmp_path / "repo")

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.head_sha is None
    assert summary.branch.name == "main"
    assert summary.branch.tracking_status == "unborn"
    assert "HEAD is unborn or unavailable" in summary.warnings


def test_non_repository_failure_is_structured(tmp_path):
    path = tmp_path / "not-repo"
    path.mkdir()

    summary = summarize_worktree_status(str(path))

    assert summary.ok is False
    assert summary.error["type"] == "git-inspection-failed"


def test_cli_json_output_for_dirty_worktree_exits_zero(tmp_path, capsys):
    repo = init_repo(tmp_path / "repo")
    commit_file(repo)
    (repo / "untracked.txt").write_text("new\n")

    exit_code = cli.main(["worktree-status", str(repo), "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "worktree-status"
    assert output["ok"] is True
    assert output["status"]["untracked"] == ["untracked.txt"]


def test_cli_human_output_includes_counts_and_caveat(tmp_path, capsys):
    repo, _remote = setup_tracking_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n")

    exit_code = cli.main(["worktree-status", str(repo)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "repository root:" in output
    assert "worktree path:" in output
    assert "ahead/behind: 0 ahead, 0 behind" in output
    assert "tracking caveat: ahead/behind counts may be stale until fetch" in output
    assert "changes: 0 staged, 1 unstaged, 0 untracked, 0 conflicted" in output


def test_cli_non_repository_failure_exits_one(tmp_path, capsys):
    path = tmp_path / "not-repo"
    path.mkdir()

    exit_code = cli.main(["worktree-status", str(path)])

    assert exit_code == 1
    assert "worktree-status:" in capsys.readouterr().err


def test_cli_usage_error_exits_two(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["worktree-status", "--definitely-not-a-real-option"])

    assert error.value.code == 2
    assert "usage:" in capsys.readouterr().err


def _add_gitignore(repo: Path, content: str) -> None:
    (repo / ".gitignore").write_text(content)
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "add .gitignore")


def test_ignored_claude_artifacts_not_counted_as_untracked(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit_file(repo)
    _add_gitignore(repo, ".claude/\n")
    claude_dir = repo / ".claude" / "worktrees" / "session-abc"
    claude_dir.mkdir(parents=True)
    (claude_dir / "state.json").write_text("{}\n")

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.status.clean is True
    assert summary.status.untracked == ()


def test_genuine_untracked_files_still_reported_alongside_ignored_claude(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit_file(repo)
    _add_gitignore(repo, ".claude/\n")
    claude_dir = repo / ".claude" / "worktrees" / "session-abc"
    claude_dir.mkdir(parents=True)
    (claude_dir / "state.json").write_text("{}\n")
    (repo / "scratch.txt").write_text("notes\n")

    summary = summarize_worktree_status(str(repo))

    assert summary.ok is True
    assert summary.status.clean is False
    assert "scratch.txt" in summary.status.untracked
    assert not any(p.startswith(".claude") for p in summary.status.untracked)


def test_cli_json_ignored_claude_artifacts_not_in_untracked(tmp_path, capsys):
    repo = init_repo(tmp_path / "repo")
    commit_file(repo)
    _add_gitignore(repo, ".claude/\n")
    claude_dir = repo / ".claude" / "worktrees" / "session-abc"
    claude_dir.mkdir(parents=True)
    (claude_dir / "state.json").write_text("{}\n")

    exit_code = cli.main(["worktree-status", str(repo), "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"]["clean"] is True
    assert output["status"]["untracked"] == []


def test_cli_human_ignored_claude_artifacts_show_zero_untracked(tmp_path, capsys):
    repo = init_repo(tmp_path / "repo")
    commit_file(repo)
    _add_gitignore(repo, ".claude/\n")
    claude_dir = repo / ".claude" / "worktrees" / "session-abc"
    claude_dir.mkdir(parents=True)
    (claude_dir / "state.json").write_text("{}\n")

    exit_code = cli.main(["worktree-status", str(repo)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "0 untracked" in output
