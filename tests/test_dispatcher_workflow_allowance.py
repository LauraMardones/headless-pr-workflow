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
    for name in ("Restore budget state cache", "Save budget state cache"):
        paths = cache_paths(step(name))
        assert ".dispatcher-allowance" in paths, (name, paths)
        assert ".dispatcher-budget" in paths, (name, paths)


def test_allowance_state_dir_is_gitignored():
    lines = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert ".dispatcher-allowance/" in lines


def test_pinned_version_is_documented():
    version = re.search(r"@anthropic-ai/claude-code@(\d+\.\d+\.\d+)", step("Install Claude Code")).group(1)
    doc = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text()
    assert f"@anthropic-ai/claude-code@{version}" in doc
