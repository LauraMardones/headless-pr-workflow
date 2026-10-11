from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def spec_command() -> str:
    """Return the command text with line wrapping collapsed to single spaces."""
    return " ".join(read(".claude/commands/spec.md").split())


def test_spec_command_uses_template_and_definition_of_ready() -> None:
    command = spec_command()

    assert "specs/_template/spec.md" in command
    assert "Definition of Ready in `specs/README.md`" in command
    assert "## Open Questions` reads exactly `None.`" in command
    assert "If any item fails, do not open the PR." in command


def test_spec_command_requires_refs_and_forbids_closing_keywords() -> None:
    command = spec_command()

    assert "`Refs #<number>`" in command
    for keyword in ("`Closes`", "`Fixes`", "`Resolves`"):
        assert keyword in command
    assert "contains no closing keyword" in command


def test_spec_command_forbids_merge_and_board_status() -> None:
    command = spec_command()

    assert "**Never merge or approve the spec PR**, even if the PO asks in chat." in command
    assert "merges it personally in GitHub" in command
    assert "Never set or change a Project board status, a `status:*` label" in command


def test_spec_command_forbids_tasks_file() -> None:
    command = spec_command()

    assert "Never write `tasks.md`" in command
    assert "No `tasks.md` and no other file under the spec folder." in command


def test_spec_command_validates_existing_epic_target() -> None:
    command = spec_command()

    assert "The issue is **open** (open-state check)." in command
    assert "The issue is labeled `type:epic` (`type:epic` check)." in command
    assert "Stop without writing anything and state the reason" in command
    assert "point the PO to that PR" in command


def test_spec_command_checks_legacy_epic_instead_of_carrying_it_over() -> None:
    command = spec_command()

    assert "`## Decisions` are **input only, never carried over**." in command
    assert "against ADR-008, ADR-009, ADR-010 and ADR-011" in command
    assert "Nothing from the Epic body enters the spec unchecked." in command


def test_spec_command_names_spec_branch_pattern() -> None:
    command = spec_command()

    assert "`<agent>/issue-<number>-spec-<slug>`" in command
    assert "`*/issue-<number>-spec-*`" in command
    assert "`<agent>/issue-<number>-spec-change-<short-slug>`" in command


def test_spec_command_documents_github_operation_fallback() -> None:
    command = spec_command()

    plugin = command.index("Prefer the GitHub plugin/MCP integration")
    gh = command.index("authenticated `gh` CLI", plugin)
    direct_api = command.index("direct GitHub API request", gh)

    assert plugin < gh < direct_api
    assert "verify the target repository" in command
    assert "never bypasses workflow gates" in command


def test_agents_lists_spec_command() -> None:
    agents = read("AGENTS.md")
    workflow_commands = agents.split("## Workflow Commands", maxsplit=1)[1]

    assert "`.claude/commands/spec.md`" in workflow_commands
