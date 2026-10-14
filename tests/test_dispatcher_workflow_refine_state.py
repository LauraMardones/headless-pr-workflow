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

SCOPE = "Resolve notification state scope"
RESTORE = "Restore refinement notification state"
POLL = "Run dispatcher poll"
SAVE = "Save refinement notification state"
INVOKE = "Run dispatcher invoke"
RETRY = "Retry actionable Slack notifications"
EARLY_SAVE = "Save poll notification state"


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


def test_restore_before_poll_and_save_after_invoke_and_retry():
    """Issue #295: invoke queues events and the retry step records delivery, so
    the state is saved after both, not right after the poll."""
    names = [name for name, _ in _steps()]
    for name in (RESTORE, POLL, INVOKE, RETRY, SAVE):
        assert name in names, f"missing step {name!r}"
    assert names.index(RESTORE) < names.index(POLL) < names.index(INVOKE)
    assert names.index(INVOKE) < names.index(RETRY) < names.index(SAVE)


def test_retry_step_runs_every_enabled_cycle_even_without_work():
    """Issue #295: the main failure case is a blocked story with nothing ready
    or resumable, where the invoke step is skipped. The retry step must not be
    gated on the poll outputs or on the invoke step succeeding."""
    block = _step(RETRY)
    condition = _field(block, "if")
    assert "steps.guard.outputs.enabled == 'true'" in condition
    assert "!cancelled()" in condition
    assert "issue_number" not in condition
    assert "resumable" not in condition
    assert "steps.invoke" not in condition
    assert "dispatcher-notify-retry.sh" in block
    assert re.search(
        r'if \[ "\$\{\{ steps\.scope\.outputs\.state_writer \}\}" != "true" \]; then\s*\n'
        r"\s+RETRY_ARGS\+=\(--dry-run\)",
        block,
    ), "non-default-ref runs must not send or record"


def test_retry_does_not_record_delivery_when_state_will_not_be_saved():
    """Codex finding on #295: the save requires a successful poll, so the retry
    step must require it too; otherwise a delivery recorded after a failed poll
    is never persisted and the next cycle sends it again."""
    retry_condition = _field(_step(RETRY), "if")
    save_condition = _field(_step(SAVE), "if")
    assert "steps.poll.outcome == 'success'" in save_condition
    assert "steps.poll.outcome == 'success'" in retry_condition


def test_state_is_saved_even_when_invoke_or_retry_fails():
    condition = _field(_step(SAVE), "if")
    assert "!cancelled()" in condition
    assert "steps.poll.outcome == 'success'" in condition


def test_state_steps_run_on_every_enabled_poll_regardless_of_issue_number():
    for name in (SCOPE, RESTORE, SAVE):
        condition = _field(_step(name), "if")
        assert "steps.guard.outputs.enabled == 'true'" in condition
        assert "issue_number" not in condition
        assert "always()" not in condition


def test_only_default_branch_runs_write_state():
    """Actions caches are ref-scoped: a snapshot saved by a manual run on a
    non-default ref is invisible to scheduled default-branch runs, which would
    then re-notify. Only default-branch runs may notify and save."""
    names = [name for name, _ in _steps()]
    assert names.index(SCOPE) < names.index(RESTORE)

    scope = _step(SCOPE)
    assert re.search(r"^\s+id:\s*scope\s*$", scope, re.MULTILINE)
    writer = _field(scope, "STATE_WRITER")
    assert "github.event_name == 'schedule'" in writer
    assert (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        in writer
    )
    assert "state_writer=$STATE_WRITER" in scope

    # Restore is not gated by ref: non-default refs read the default-branch
    # snapshot (cache fallback) so their dry-run poll reflects shared state.
    assert "state_writer" not in _field(_step(RESTORE), "if")

    save_condition = _field(_step(SAVE), "if")
    assert "steps.scope.outputs.state_writer == 'true'" in save_condition

    poll = _step(POLL)
    assert re.search(
        r'if \[ "\$\{\{ steps\.scope\.outputs\.state_writer \}\}" != "true" \]; then\s*\n'
        r"\s+POLL_ARGS\+=\(--dry-run\)",
        poll,
    ), "non-default-ref polls must run with --dry-run (no Slack, no state write)"
    assert 'dispatcher-poll.sh "${POLL_ARGS[@]}"' in poll


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


def test_poll_state_is_saved_before_the_cancellable_invoke_step():
    """Codex finding on #295: the invoke step can run for hours and be
    cancelled, which skips the final save and re-sends a ready_for_refinement
    alert already sent by the poll. The poll's state is saved right after it."""
    names = [name for name, _ in _steps()]
    assert names.index(POLL) < names.index(EARLY_SAVE) < names.index(INVOKE)
    early = _step(EARLY_SAVE)
    condition = _field(early, "if")
    assert "steps.poll.outcome == 'success'" in condition
    assert "steps.scope.outputs.state_writer == 'true'" in condition
    assert "uses: actions/cache/save@" in early
    assert _field(early, "path") == ".dispatcher-state"
    early_key = _field(early, "key")
    final_key = _field(_step(SAVE), "key")
    # Cache entries are immutable, so the two saves need different keys, and
    # both must match the restore prefix.
    assert early_key != final_key
    assert early_key.startswith("dispatcher-state-")
    assert "${{ github.run_id }}" in early_key
