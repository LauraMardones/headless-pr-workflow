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


def test_merge_graphql_preflight_precedes_every_mutation() -> None:
    text = command_text("merge.md")

    preflight = text.index("gh api graphql -f query='{ viewer { login } }'")

    assert preflight < text.index("set the story status")
    assert preflight < text.index("merge the PR")
    assert "exits non-zero, stop" in text
    assert "Make no GitHub mutation" in text
    assert "GitHub Actions or a local session" in text
    assert "Do not fall back to the GitHub plugin/MCP integration or REST for the merge gate" in text


def test_implement_and_review_status_writes_are_best_effort() -> None:
    for name in ("implement.md", "review.md"):
        text = command_text(name)

        assert "Board status writes are best-effort" in text, name
        assert "do not stop: continue the command" in text, name
        assert "record the skipped status change in the Session Summary with `--deviation`" in text, name


def test_implement_falls_back_to_issue_body_when_ac_summary_lacks_gh() -> None:
    text = command_text("implement.md")

    assert "exits `1` because `gh` or GraphQL is unavailable" in text
    assert "read the AC/DoD checklist from the issue body" in text
    assert "AC/DoD coverage must still be verified" in text


def test_review_leaves_unresolvable_thread_open() -> None:
    text = command_text("review.md")

    assert "leave the thread open" in text
    assert "list it in the Session Summary with `--deviation`" in text
    assert "never treat it as resolved" in text


def test_agents_guidance_names_gh_graphql_dependency() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    operations = agents.split("## GitHub Operations", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    operations = " ".join(operations.split())

    for step in (
        "board status writes",
        "`scripts/ac-summary.sh`",
        "review-thread resolution",
        "`scripts/merge-gate-summary`",
    ):
        assert step in operations, step
    assert "2.50" in operations
    assert "GitHub Actions and a local session" in operations
    assert "Claude Code cloud session does not" in operations
    assert 'HTTP 403 "GraphQL is not available from Claude Code sessions"' in operations
    assert "Board writes are best-effort" in operations
    assert "The merge gate is never skipped" in operations

    sandbox_row = next(line for line in agents.splitlines() if line.startswith("| `gh` CLI blocked"))
    assert "Board status writes are skipped" in sandbox_row
    assert "`/merge` cannot run there" in sandbox_row


def test_required_outputs_are_transport_agnostic() -> None:
    for name in COMMANDS:
        required_output = command_text(name).split("## Required GitHub Output", maxsplit=1)[1]

        assert "using `mcp__github__" not in required_output, name
        assert "via `mcp__github__" not in required_output, name
