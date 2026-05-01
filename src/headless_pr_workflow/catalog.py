"""Command catalog for the headless PR workflow CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    priority: str
    phase: str
    command_type: str
    layer: str
    status: str = "scaffolded"


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("pr-context", "Fetch complete current PR context from GitHub.", "P1-high", "C-session", "report", "core", "implemented"),
    CommandSpec("pr-takeover", "Produce safe takeover context for a PR.", "P1-high", "C-session", "action", "core"),
    CommandSpec("worktree-status", "Report local worktrees, branches, and dirty state.", "P1-high", "C-session", "report", "core"),
    CommandSpec("review-sha", "Report current head SHA and approval SHA relationship.", "P0-blocking", "F-review", "hard-gate", "core", "implemented"),
    CommandSpec("approval-check", "Verify approval applies to current PR head SHA.", "P0-blocking", "F-review", "hard-gate", "core", "implemented"),
    CommandSpec("re-review-needed", "Detect whether new commits require review re-evaluation.", "P1-high", "F-review", "hard-gate", "core"),
    CommandSpec("review-delta", "Show changes since the last reviewed SHA.", "P2-medium", "F-review", "report", "core"),
    CommandSpec("unresolved-review-threads", "Detect unresolved GitHub review threads.", "P1-high", "F-review", "hard-gate", "core", "implemented"),
    CommandSpec("blocking-comments", "Detect blocking review comments or labels.", "P1-high", "F-review", "hard-gate", "core"),
    CommandSpec("ci-summary", "Summarize CI/check state for the current head SHA.", "P1-high", "E-review-readiness", "report", "core", "implemented"),
    CommandSpec("target-branch-check", "Verify PR targets the expected base branch.", "P0-blocking", "H-merge", "hard-gate", "core", "implemented"),
    CommandSpec("merge-owner", "Determine whether this session may merge.", "P1-high", "H-merge", "hard-gate", "core", "implemented"),
    CommandSpec("pre-merge", "Compose merge-readiness checks.", "P0-blocking", "H-merge", "hard-gate", "core", "implemented"),
    CommandSpec("merge-pr", "Dry-run merge after fresh pre-merge gates pass.", "P0-blocking", "H-merge", "action", "core", "implemented"),
    CommandSpec("post-merge-sync", "Sync local state after merge.", "P1-high", "I-post-merge", "action", "core"),
    CommandSpec("branch-cleanup", "Remove stale merged local branches when safe.", "P2-medium", "I-post-merge", "action", "core"),
    CommandSpec("workflow-status", "Summarize PR state and next action.", "P1-high", "C-session", "report", "core"),
    CommandSpec("next-action", "Suggest exact next safe workflow action.", "P1-high", "C-session", "advisory", "core"),
)


def command_names() -> tuple[str, ...]:
    return tuple(command.name for command in COMMANDS)


def find_command(name: str) -> CommandSpec | None:
    for command in COMMANDS:
        if command.name == name:
            return command
    return None
