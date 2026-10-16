"""No metered model-provider API path is left in the dispatcher (issues #314 and #315; F308.2).

Static checks over the files the dispatcher runs: ``scripts/`` and
``.github/workflows/``, comments included, with no exception list.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_dispatcher_workflow_allowance import step

REPO_ROOT = Path(__file__).resolve().parent.parent

# The names are assembled so this file does not carry the literals either.
METERED_KEY = "ANTHROPIC" + "_API_KEY"
FORBIDDEN_NAMES = (
    "run_" + "anthropic_agent",
    "run_" + "openai_agent",
    "ANTHROPIC" + "_API_URL",
    "OPENAI" + "_API_URL",
    "api." + "openai.com",
    "api." + "anthropic.com",
)
KEY_PATTERN = re.compile(r"(?:ANTHROPIC|OPENAI|GEMINI|GOOGLE_AI|MISTRAL|COHERE)" + r"_API_KEY\w*")
DIRECTORIES = ("scripts", ".github/workflows")


def first_match(directory: str, names: tuple[str, ...] = FORBIDDEN_NAMES, pattern=KEY_PATTERN) -> str | None:
    for path in sorted((REPO_ROOT / directory).rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            hit = next((name for name in names if name in line), None)
            if hit is None and (found := pattern.search(line)):
                hit = found.group(0)
            if hit:
                return f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: {hit}"
    return None


def test_metered_names_are_absent():
    for directory in DIRECTORIES:
        match = first_match(directory)
        assert match is None, f"metered model-provider path still referenced at {match}"


def test_the_matcher_finds_each_forbidden_name(tmp_path, monkeypatch):
    monkeypatch.setattr(__name__ + ".REPO_ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    probes = [*FORBIDDEN_NAMES, "OPENAI" + "_API_KEY_CODEX", "GEMINI" + "_API_KEY", "MISTRAL" + "_API_KEY_X",
              "COHERE" + "_API_KEY", "GOOGLE_AI" + "_API_KEY", METERED_KEY]
    for probe in probes:
        (tmp_path / "scripts" / "probe.sh").write_text(f"# comment mentions {probe}\n", encoding="utf-8")
        assert first_match("scripts") is not None, probe
    (tmp_path / "scripts" / "probe.sh").write_text("# CLAUDE_CODE_OAUTH_TOKEN only\n", encoding="utf-8")
    assert first_match("scripts") is None


def test_invoke_step_wires_the_subscription_token_only():
    body = step("Run dispatcher invoke")
    env_block = body.split("        run:", 1)[0]
    assert "CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in env_block
    assert not KEY_PATTERN.search(env_block)
    assert "OPENAI" + "_API_KEY_CODEX" not in (REPO_ROOT / ".github" / "workflows" / "dispatcher.yml").read_text(encoding="utf-8")
    secrets = set(re.findall(r"secrets\.(\w+)", env_block))
    assert secrets == {"CLAUDE_CODE_OAUTH_TOKEN"}  # the only model-provider secret the invoke step names


def test_both_scripts_source_the_shared_helper():
    for script in ("dispatcher-invoke.sh", "dispatcher-allowance.sh"):
        text = (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert 'source "$SCRIPT_DIR/lib/claude-session.sh"' in text, script
        assert "env -u" not in text, script  # the scrub is the helper's, by prefix
