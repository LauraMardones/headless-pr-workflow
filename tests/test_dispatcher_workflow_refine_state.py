"""Workflow contract for ready_for_refinement notification state (issue #257).

GitHub-hosted runners are ephemeral, so ``scripts/dispatcher-poll.sh``'s
de-duplication state must be restored from ``actions/cache`` before the poll
and saved after it on every enabled run, and runs must be serialized so two
runs never read the same snapshot and race to write it.

The workflow is parsed textually (PyYAML is not a project dependency).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"
POLL_SCRIPT = REPO_ROOT / "scripts" / "dispatcher-poll.sh"

RESTORE = "Restore refinement notification state"
POLL = "Run dispatcher poll"
SAVE = "Save refinement notification state"
INVOKE = "Run dispatcher invoke"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _steps() -> list[tuple[str, str]]:
    """Return (name, block) for each step in document order."""
    text = _workflow()
    matches = list(re.finditer(r"^      - name: (.+)$", text, re.MULTILINE))
    steps = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        steps.append((match.group(1).strip(), text[match.start() : end]))
    return steps


def _step(name: str) -> str:
    for step_name, block in _steps():
        if step_name == name:
            return block
    raise AssertionError(f"step {name!r} not found in {WORKFLOW}")


def _field(block: str, key: str) -> str:
    match = re.search(rf"^\s+{re.escape(key)}:\s*(.+)$", block, re.MULTILINE)
    assert match, f"{key!r} missing from step block:\n{block}"
    return match.group(1).strip()


def test_restore_before_poll_and_save_after_poll_before_invoke():
    names = [name for name, _ in _steps()]
    for name in (RESTORE, POLL, SAVE, INVOKE):
        assert name in names, f"missing step {name!r}"
    assert names.index(RESTORE) < names.index(POLL) < names.index(SAVE) < names.index(INVOKE)


def test_state_steps_run_on_every_enabled_poll_regardless_of_issue_number():
    for name in (RESTORE, SAVE):
        condition = _field(_step(name), "if")
        assert "steps.guard.outputs.enabled == 'true'" in condition
        assert "issue_number" not in condition
        assert "always()" not in condition, "save must not run after a failed poll"


def test_cache_actions_paths_and_keys():
    restore = _step(RESTORE)
    save = _step(SAVE)
    assert "uses: actions/cache/restore@" in restore
    assert "uses: actions/cache/save@" in save
    assert _field(restore, "path") == ".dispatcher-state"
    assert _field(save, "path") == ".dispatcher-state"

    save_key = _field(save, "key")
    # Cache entries are immutable: every run must write a new, unique key.
    assert "${{ github.run_id }}" in save_key
    assert "${{ github.run_attempt }}" in save_key
    assert _field(restore, "key") == save_key

    restore_keys = re.search(r"restore-keys:\s*\|\s*\n\s+(\S+)", restore)
    assert restore_keys, "restore step needs a prefix restore-keys entry"
    prefix = restore_keys.group(1)
    assert save_key.startswith(prefix), (save_key, prefix)
    assert "${{" not in prefix, "restore prefix must match every earlier run's key"


def test_concurrency_serializes_scheduled_and_manual_runs():
    text = _workflow()
    block = re.search(r"^concurrency:\n((?:  .+\n)+)", text, re.MULTILINE)
    assert block, "workflow-level concurrency block is required"
    body = block.group(1)
    group = re.search(r"^\s+group:\s*(.+)$", body, re.MULTILINE)
    assert group, "concurrency group missing"
    # One group shared by schedule and workflow_dispatch: it must not vary by
    # event, ref, or run.
    for varying in ("github.event_name", "github.run_id", "github.ref", "github.sha"):
        assert varying not in group.group(1)
    assert re.search(r"^\s+cancel-in-progress:\s*false\s*$", body, re.MULTILINE)
    assert "on:" in text and "schedule:" in text and "workflow_dispatch:" in text


def test_poll_script_default_state_dir_matches_cached_path():
    script = POLL_SCRIPT.read_text(encoding="utf-8")
    assert 'STATE_DIR="${DISPATCHER_STATE_DIR:-$REPO_ROOT/.dispatcher-state}"' in script
    assert "${TMPDIR:-/tmp}/dispatcher-poll-notified-refinement" not in script


def test_state_dir_is_gitignored():
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".dispatcher-state/" in ignored
