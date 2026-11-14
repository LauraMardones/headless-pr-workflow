"""Workflow contract for the subscription allowance check (issue #317, AC19, AC20).

Reads .github/workflows/dispatcher.yml as text, the way
tests/test_required_check_policy.py reads its policy file. PyYAML is not a
dev dependency, so steps are split on their ``- name:`` lines.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"

STEP_RE = re.compile(r"^      - name: (.+)$", re.MULTILINE)


def steps() -> list[tuple[str, str]]:
    text = WORKFLOW.read_text()
    matches = list(STEP_RE.finditer(text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1).strip(), text[m.start():end]))
    return out


def step(name: str) -> str:
    for n, body in steps():
        if n == name:
            return body
    raise AssertionError(f"step {name!r} not found in {WORKFLOW}")


def step_if(body: str) -> str:
    m = re.search(r"^        if: (.+)$", body, re.MULTILINE)
    assert m, body
    return m.group(1).strip()


def cache_paths(body: str) -> list[str]:
    m = re.search(r"^          path: \|\n((?:            \S.*\n)+)", body, re.MULTILINE)
    if m:
        return [line.strip() for line in m.group(1).splitlines()]
    m = re.search(r"^          path: (\S+)$", body, re.MULTILINE)
    assert m, body
    return [m.group(1)]


def test_install_step_is_pinned_and_precedes_invoke():
    names = [n for n, _ in steps()]
    assert "Install Claude Code" in names
    assert names.index("Install Claude Code") < names.index("Run dispatcher invoke")
    body = step("Install Claude Code")
    assert re.search(r"npm install -g @anthropic-ai/claude-code@\d+\.\d+\.\d+\s*$", body, re.MULTILINE), body


def test_install_step_has_the_invoke_condition():
    assert step_if(step("Install Claude Code")) == step_if(step("Run dispatcher invoke"))


def test_invoke_env_wires_token_and_reserves():
    body = step("Run dispatcher invoke")
    env_block = body.split("        run:", 1)[0]
    for line in (
        "CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
        "RESERVE_FIVE_HOUR_PERCENT: ${{ vars.RESERVE_FIVE_HOUR_PERCENT }}",
        "RESERVE_WEEKLY_PERCENT: ${{ vars.RESERVE_WEEKLY_PERCENT }}",
    ):
        assert line in env_block, line


def test_cache_steps_keep_allowance_state():
    for name in ("Restore session state cache", "Save session state cache"):
        paths = cache_paths(step(name))
        assert ".dispatcher-allowance" in paths, (name, paths)
        assert ".dispatcher-usage" in paths, (name, paths)
        assert ".dispatcher-budget" not in paths, (name, paths)


def test_allowance_state_dir_is_gitignored():
    lines = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert ".dispatcher-allowance/" in lines


def test_pinned_version_is_documented():
    version = re.search(r"@anthropic-ai/claude-code@(\d+\.\d+\.\d+)", step("Install Claude Code")).group(1)
    doc = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text()
    assert f"@anthropic-ai/claude-code@{version}" in doc


def test_invoke_runs_dry_run_when_not_the_state_writer():
    # Issue #320: the session count is saved to this ref's cache scope, so a
    # run on a non-default ref must not start sessions the scheduled
    # default-branch runs cannot count.
    # Issue #434: the --dry-run and --issue/--resume choices are in the step script
    # (tests/test_dispatcher_workflow_step.py); the step passes the state-writer fact to it.
    body = step("Run dispatcher invoke")
    assert "STATE_WRITER: ${{ steps.scope.outputs.state_writer }}" in body
    assert "bash scripts/dispatcher-workflow-step.sh invoke" in body
    names = [n for n, _ in steps()]
    assert names.index("Resolve notification state scope") < names.index("Run dispatcher invoke")


# ── the board status writer step (issue #478) ────────────────────────────────

def test_board_status_step_runs_after_invoke_and_before_the_trigger_check():
    names = [n for n, _ in steps()]
    assert names.index("Run dispatcher invoke") < names.index("Notify usage limit reached") \
        < names.index("Write the board status") < names.index("Check the dispatch trigger")


def test_board_status_step_runs_every_enabled_run_even_after_a_failed_or_skipped_invoke():
    body = step("Write the board status")
    # Not tied to the poll or invoke outputs: it runs when there was no work, when invoke was
    # skipped and when it failed; !cancelled() keeps it running after a failure, and only the guard stops it.
    assert step_if(body) == "${{ !cancelled() && steps.guard.outputs.enabled == 'true' }}"
    assert "steps.poll" not in step_if(body) and "steps.invoke" not in step_if(body)
    assert "timeout-minutes: 10" in body
    assert "continue-on-error" not in body


def test_board_status_step_uses_the_project_token_and_the_state_writer_rule():
    body = step("Write the board status")
    assert "GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}" in body
    assert "STATE_WRITER: ${{ steps.scope.outputs.state_writer }}" in body
    assert "run: bash scripts/dispatcher-workflow-step.sh status-sync" in body
