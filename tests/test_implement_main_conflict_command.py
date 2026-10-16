"""implement.md keeps the merge-conflict text of issue #381."""

from pathlib import Path

COMMAND = (Path(__file__).resolve().parents[1] / ".claude/commands/implement.md").read_text(encoding="utf-8")


def step_16() -> str:
    start = COMMAND.index("16. When implementation is complete")
    return COMMAND[start:COMMAND.index("17. If you are addressing")]


def test_conflict_check_precedes_the_final_push_and_uses_a_merge() -> None:
    step = step_16()
    check = step.index("check whether the PR branch conflicts with the base branch")

    assert check < step.index("commit only the intended changes and push")
    assert "first run and on a review-fix run" in step
    assert "merge `origin/main` into the branch with a merge commit" in step
    assert "never rebase and never force-push" in step


def test_unresolvable_conflict_keeps_draft_and_reports_the_blocker() -> None:
    step = step_16()

    assert "keep the PR Draft and the story \"In implementation\"" in step
    assert "`merge conflict with main on PR #<n>`" in step
    assert "without `--next review`" in step


def test_absent_check_reads_mergeability() -> None:
    step = step_16()

    assert "check for the current head is absent, read the PR's mergeability" in step
    assert "GitHub starts no CI run for a conflicting PR" in step
