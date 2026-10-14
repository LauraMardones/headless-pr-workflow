"""The metered Anthropic path is gone from the dispatcher (issue #314, AC14 and AC17; part of F308.2).

Static checks over the files the dispatcher runs: ``scripts/`` and
``.github/workflows/``, comments included. #315 extends ``FORBIDDEN`` with the
OpenAI names.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_dispatcher_workflow_allowance import step

REPO_ROOT = Path(__file__).resolve().parent.parent

# The key name is assembled so this file does not carry the literal either.
METERED_KEY = "ANTHROPIC" + "_API_KEY"

FORBIDDEN = {
    "scripts": ("run_anthropic_agent", "ANTHROPIC_API_URL", METERED_KEY),
    ".github/workflows": (METERED_KEY,),
}


def first_match(directory: str, names: tuple[str, ...]) -> str | None:
    for path in sorted((REPO_ROOT / directory).rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for name in names:
                if name in line:
                    return f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: {name}"
    return None


def test_metered_anthropic_names_are_absent():
    for directory, names in FORBIDDEN.items():
        match = first_match(directory, names)
        assert match is None, f"metered Anthropic path still referenced at {match}"


def test_invoke_step_wires_the_subscription_token_only():
    body = step("Run dispatcher invoke")
    env_block = body.split("        run:", 1)[0]
    assert "CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in env_block
    assert METERED_KEY not in env_block
    assert METERED_KEY not in (REPO_ROOT / ".github" / "workflows" / "dispatcher.yml").read_text(encoding="utf-8")


def test_both_scripts_source_the_shared_helper():
    for script in ("dispatcher-invoke.sh", "dispatcher-allowance.sh"):
        text = (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert 'source "$SCRIPT_DIR/lib/claude-session.sh"' in text, script
        assert "env -u" not in text, script  # the scrub is the helper's, by prefix
