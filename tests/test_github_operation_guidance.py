"""Regression tests for assistant-side GitHub operation policy."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    "refine.md",
    "implement.md",
    "review.md",
    "merge.md",
    "cleanup.md",
)


def command_text(name: str) -> str:
    return (ROOT / ".claude" / "commands" / name).read_text(encoding="utf-8")


def test_all_mutating_commands_document_safe_fallback_order() -> None:
    for name in COMMANDS:
        text = command_text(name)
        plugin = text.index("Prefer the GitHub plugin/MCP integration")
        gh = text.index("authenticated `gh` CLI", plugin)
        direct_api = text.index("direct GitHub API request", gh)

        assert plugin < gh < direct_api, name
        assert "GitHub reads and mutations" in text, name
        assert "verify the target repository" in text, name
        assert "Never expose, print, log, persist, or commit GitHub credentials" in text, name
        assert "never bypasses workflow gates" in text, name


def test_review_guidance_preserves_current_head_and_thread_safety() -> None:
    text = command_text("review.md")

    assert "verify the target repository, PR number, and current head SHA" in text
    assert "record the documented solo-maintainer override against that same verified SHA" in text
    assert "confirm it belongs to the verified PR" in text
    assert "never resolve a still-actionable thread" in text


def test_merge_fallback_requires_fresh_gates() -> None:
    text = command_text("merge.md")

    assert "Refresh the head SHA and every required merge gate immediately before merging" in text
    assert "Merge only if" in text
    assert "all merge gates pass" in text


def test_required_outputs_are_transport_agnostic() -> None:
    for name in COMMANDS:
        required_output = command_text(name).split("## Required GitHub Output", maxsplit=1)[1]

        assert "using `mcp__github__" not in required_output, name
        assert "via `mcp__github__" not in required_output, name
