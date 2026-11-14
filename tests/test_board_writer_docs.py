"""``docs/DISPATCHER-CONFIG.md`` describes the board writer (issue #478, AC 10, F3.7)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")

SECTION_TITLE = "## Board status — the only writer"
MIGRATION_TITLE = "**Migration of the board options.**"
OPTIONS = ("No spec", "In specification", "Not refined", "Blocked", "Refinement failed", "Refined",
           "Ready to implement", "In implementation", "In review", "Mergeable", "In progress", "Done")
REMOVED = ("Backlog", "Ready for refinement", "In refinement", "Ready for implementation", "In merge")
RETIRED = ("set_project_status", "set_project_status_checked", "set_project_status_ready", "board_status_kept",
           "set-project-status", "project-status-sync", "workflow-intent-detect", "tests/test_set_project_status.py")


def section() -> str:
    start = DOC.index(SECTION_TITLE)
    return DOC[start:DOC.index("\n## ", start + len(SECTION_TITLE))]


def migration() -> str:
    text = section()
    return text[text.index(MIGRATION_TITLE):]


def test_the_writer_step_and_its_command_are_named():
    text = section()
    assert 'The board is a display. One command writes its Status field: `hpw dispatcher status sync`' in text
    assert '"Write the board status"' in text and "bash scripts/dispatcher-workflow-step.sh status-sync" in text
    assert "It is the only writer of board status (issue #478)" in text
    assert "`src/headless_pr_workflow/dispatcher/gateway.py`, the one allowed place" in text
    assert "`tests/test_single_board_writer.py`" in text


def test_the_conditions_are_described():
    text = section()
    assert 'after "Run dispatcher invoke" and "Notify usage limit reached" and before "Check the dispatch trigger"' in text
    assert "only when `DISPATCHER_ENABLED` is `true`" in text
    assert "The writer itself does not read `DISPATCHER_ENABLED`" in text
    assert "when the poll found no work, when the invoke step was skipped and when it failed" in text
    assert "`if: ${{ !cancelled() && steps.guard.outputs.enabled == 'true' }}`" in text
    assert "with `--dry-run` unless `STATE_WRITER=true`: a run off the default branch only reports" in text
    assert "`GH_TOKEN` set to `PROJECT_TOKEN`" in text


def test_the_exit_codes_are_described():
    text = section()
    assert "`0` (`synced` or `dry-run`) passes" in text
    assert "`1` (`failed`), `2` (invalid arguments) and `3` (`unreadable`) fail the step" in text
    assert "Any other code fails the step with `1`" in text


def test_the_option_list_is_complete_and_in_order():
    text = section()
    line = next(row for row in text.splitlines() if row.startswith("**Status options.**"))
    listed = line.split("in this order: ", 1)[1].split(". `PROJECT_TOKEN`", 1)[0]
    assert tuple(name.strip() for name in listed.split(",")) == OPTIONS


def test_the_migration_has_four_ordered_steps():
    text = migration()
    steps = [line for line in text.splitlines() if line[:3] in ("1. ", "2. ", "3. ", "4. ")]
    assert [s[:2] for s in steps] == ["1.", "2.", "3.", "4."]
    assert "Before the PR of issue #478 is merged: add the options that do not exist yet" in steps[0]
    assert "keeping the old ones" in steps[0]
    assert "The first run with `DISPATCHER_ENABLED=true` writes every item" in steps[1]
    assert "`hpw dispatcher status sync --repo <owner/repo> --dry-run` lists no difference and no `missing_option`" in steps[2]
    assert all(f"`{name}`" in steps[2] for name in REMOVED)
    assert "Delete those five old options" in steps[3]


def test_no_retired_name_and_no_removed_status_outside_the_migration_steps():
    rest = DOC.replace(migration(), "")
    assert [name for name in RETIRED if name in DOC] == []
    assert [name for name in REMOVED if name in rest] == []
