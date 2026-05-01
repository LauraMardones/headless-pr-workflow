from headless_pr_workflow.catalog import COMMANDS, command_names, find_command


def test_mvp_commands_are_present():
    names = set(command_names())

    assert "pr-context" in names
    assert "review-sha" in names
    assert "approval-check" in names
    assert "merge-owner" in names
    assert "pre-merge" in names
    assert "merge-pr" in names


def test_command_names_are_unique():
    names = command_names()

    assert len(names) == len(set(names))


def test_core_merge_gates_are_blocking():
    blocking = {command.name for command in COMMANDS if command.priority == "P0-blocking"}

    assert "approval-check" in blocking
    assert "target-branch-check" in blocking
    assert "pre-merge" in blocking


def test_find_command_returns_catalog_entry():
    command = find_command("approval-check")

    assert command is not None
    assert command.layer == "core"
    assert command.command_type == "hard-gate"
