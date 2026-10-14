"""/merge and /cleanup are documented as script-only dispatcher phases (issue #264)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_adapters_states_the_script_only_phases():
    text = read("docs/ADAPTERS.md")
    assert "**Script-only phases: `/merge` and `/cleanup` (issue #264).**" in text
    assert "no executor tier applies" in text.lower()
    assert "no subscription-allowance check" in text and "no executor budget increment" in text
    assert "ADR-012's session-start mechanism does not cover these two phases" in text
    assert "`/merge` and `/cleanup` are script-only phases and start no session" in text


def test_dispatcher_config_states_the_script_only_phases():
    text = read("docs/DISPATCHER-CONFIG.md")
    assert "**script-only phases** (issue #264)" in text
    assert "no executor tier, no allowance check, no executor budget" in text
    assert "ADR-012's session-start mechanism does not cover them" in text


def test_no_doc_lists_merge_or_cleanup_as_a_model_session_or_allowance_start():
    config = read("docs/DISPATCHER-CONFIG.md")
    adapters = read("docs/ADAPTERS.md")
    for stale in (
        "Before every model session start (`/implement`, `/review`, `/merge` and `/cleanup`)",
        "Before `/review`, `/merge` or `/cleanup`:",
        "shared across `/implement`/`/review`/`/merge`/`/cleanup`",
        "and `/merge` and `/cleanup` of a Claude-tier story",
    ):
        assert stale not in config
    assert "and `/merge` and `/cleanup`. No metered" not in adapters
    assert "Before every model session start (`/implement` and `/review`)" in config


def test_step_map_exists_and_names_both_scripts_and_both_command_files():
    text = read("docs/DISPATCHER-MERGE-CLEANUP-MAP.md")
    for name in ("scripts/dispatcher-merge.sh", "scripts/dispatcher-cleanup.sh",
                 ".claude/commands/merge.md", ".claude/commands/cleanup.md", "ADR-011"):
        assert name in text
    for step in range(1, 16):
        assert str(step) in text
