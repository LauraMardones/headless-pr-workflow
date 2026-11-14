"""The board has one Status writer (issue #478, F3.5).

``hpw dispatcher status sync`` (#476) writes the board's Status field through
``src/headless_pr_workflow/dispatcher/gateway.py``. No other tracked file under
``scripts/``, ``src/``, ``.github/`` or ``.claude/`` may hold a Status write;
``docs/``, ``specs/``, ``tests/`` and ``AGENTS.md`` may name the writes as text.
The dispatcher's scripts, workflow and Python code name no removed status, and
the retired writers are gone.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The writer's gateway: the one file that may send the Status mutation.
GATEWAY = "src/headless_pr_workflow/dispatcher/gateway.py"
SCANNED = ("scripts/", "src/", ".github/", ".claude/")
WRITES = ("updateProjectV2ItemFieldValue", "gh project item-edit", "set-project-status", "set_project_status",
          "set_story_status")
# The refine commands set the Epic/Feature Parent field (a text value) with the same mutation, never the
# Status field: they may hold the mutation name only while they hold no single-select option write.
PARENT_FIELD_WRITERS = (".claude/commands/refine-epic.md", ".claude/commands/refine-feature.md")
STATUS_VALUE = re.compile(r"singleSelectOptionId|single-select-option-id")

REMOVED_STATUSES = ("Backlog", "Ready for refinement", "In refinement", "Ready for implementation", "In merge")
BOARD_OPTIONS = ("No spec", "In specification", "Not refined", "Blocked", "Refinement failed", "Refined",
                 "Ready to implement", "In implementation", "In review", "Mergeable", "In progress", "Done")
RETIRED = ("scripts/set-project-status", "tests/test_set_project_status.py", "scripts/project-status-sync.sh",
           "docs/commands/project-status-sync.md", "scripts/workflow-intent-detect.sh")
# The places that may keep naming a retired file (#479 rewrites the first; ADRs and specs are history).
HISTORY = ("docs/PROJECT-STATUS.md", "docs/decisions/", "specs/", "tests/")


def tracked_files(root: Path) -> list[str]:
    """Tracked files of the checkout, or every file of a tree that is not a git checkout."""

    if (root / ".git").exists() and shutil.which("git"):
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
        return [name for name in out.decode("utf-8").split("\0") if name and (root / name).is_file()]
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def read(root: Path, name: str) -> str:
    return (root / name).read_text(encoding="utf-8", errors="replace")


def status_writers(root: Path, files: list[str]) -> list[tuple[str, str]]:
    """Every ``(file, write)`` outside the gateway that holds a Status write."""

    found = []
    for name in files:
        if not name.startswith(SCANNED) or name == GATEWAY:
            continue
        text = read(root, name)
        for write in WRITES:
            if write not in text:
                continue
            if (write == "updateProjectV2ItemFieldValue" and name in PARENT_FIELD_WRITERS
                    and not STATUS_VALUE.search(text)):
                continue
            found.append((name, write))
    return found


def dispatcher_files(files: list[str]) -> list[str]:
    """The dispatcher's scripts, workflows and Python code (issue #478 → Scope, second bullet)."""

    return [
        name for name in files
        if re.fullmatch(r"scripts/dispatcher-[^/]*\.sh|scripts/flow-review\.sh|scripts/lib/[^/]*\.sh", name)
        or re.fullmatch(r"\.github/workflows/[^/]*\.ya?ml", name)
        or (name.startswith("src/") and name.endswith(".py"))
    ]


def removed_status_names(root: Path, files: list[str]) -> list[tuple[str, str]]:
    return [(name, status) for name in dispatcher_files(files) for status in REMOVED_STATUSES
            if status in read(root, name)]


# ── AC 8: the single-writer rule ─────────────────────────────────────────────

def test_only_the_gateway_writes_board_status():
    assert status_writers(ROOT, tracked_files(ROOT)) == []


def test_the_gateway_is_the_writer():
    assert "updateProjectV2ItemFieldValue" in read(ROOT, GATEWAY)
    assert "write_board_status" in read(ROOT, "src/headless_pr_workflow/dispatcher/board_sync.py")


@pytest.mark.parametrize("folder", SCANNED)
@pytest.mark.parametrize("write", WRITES)
def test_a_status_write_elsewhere_fails_the_rule_and_names_the_file(tmp_path, folder, write):
    name = f"{folder}fixture/writer.sh"
    (tmp_path / name).parent.mkdir(parents=True)
    (tmp_path / name).write_text(f"#!/usr/bin/env bash\n# writes the board\n{write} --status Done\n", encoding="utf-8")

    assert status_writers(tmp_path, tracked_files(tmp_path)) == [(name, write)]


def test_the_gateway_docs_specs_and_tests_may_name_the_writes(tmp_path):
    for name in (GATEWAY, "docs/x.md", "specs/x/spec.md", "tests/test_x.py", "AGENTS.md"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("\n".join(WRITES), encoding="utf-8")

    assert status_writers(tmp_path, tracked_files(tmp_path)) == []


def test_a_parent_field_writer_that_sets_a_single_select_option_fails_the_rule(tmp_path):
    name = PARENT_FIELD_WRITERS[0]
    (tmp_path / name).parent.mkdir(parents=True)
    (tmp_path / name).write_text("updateProjectV2ItemFieldValue(value: { text: $parentId })", encoding="utf-8")
    assert status_writers(tmp_path, [name]) == []

    (tmp_path / name).write_text("updateProjectV2ItemFieldValue(value: { singleSelectOptionId: $o })", encoding="utf-8")
    assert status_writers(tmp_path, [name]) == [(name, "updateProjectV2ItemFieldValue")]


# ── AC 3 and AC 5: no removed status name, no old write in the dispatcher ────

def test_no_dispatcher_file_names_a_removed_status():
    files = tracked_files(ROOT)
    assert "scripts/dispatcher-invoke.sh" in dispatcher_files(files)
    assert "src/headless_pr_workflow/codex_review_bookkeeping.py" in dispatcher_files(files)
    assert removed_status_names(ROOT, files) == []


@pytest.mark.parametrize("status", REMOVED_STATUSES)
def test_a_removed_status_in_a_dispatcher_file_fails_the_rule(tmp_path, status):
    name = "scripts/lib/fixture.sh"
    (tmp_path / name).parent.mkdir(parents=True)
    (tmp_path / name).write_text(f'# the story stays "{status}"\n', encoding="utf-8")

    assert removed_status_names(tmp_path, [name]) == [(name, status)]


def test_the_dispatcher_defines_and_calls_none_of_the_old_status_functions():
    names = ("set_project_status", "set_project_status_checked", "set_project_status_ready", "board_status_kept",
             "set-project-status")
    for path in [*dispatcher_files(tracked_files(ROOT)), "scripts/dispatcher-change-check.sh"]:
        text = read(ROOT, path)
        assert [n for n in names if n in text] == [], path


def test_flow_review_counts_ready_to_implement_and_the_startup_option_check_is_gone():
    assert '"Ready to implement")' in read(ROOT, "scripts/flow-review.sh")
    invoke = read(ROOT, "scripts/dispatcher-invoke.sh")
    assert "_required_status" not in invoke and "Status option; the dispatcher cannot run without it" not in invoke


def test_the_writer_knows_every_board_option():
    status = read(ROOT, "src/headless_pr_workflow/dispatcher/status.py")
    assert all(f'"{option}"' in status for option in BOARD_OPTIONS)


# ── AC 9: the retired writers ────────────────────────────────────────────────

def test_the_retired_files_are_gone_and_nothing_outside_history_refers_to_them():
    files = tracked_files(ROOT)
    assert [name for name in RETIRED if (ROOT / name).exists()] == []
    stems = [Path(name).name for name in RETIRED]
    refs = [(name, stem) for name in files if not name.startswith(HISTORY)
            for stem in stems if stem in read(ROOT, name)]
    assert refs == []
