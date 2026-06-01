# Agent Guidance

This file defines project-level assistant guidance for work in this repository. It is advisory assistant behavior, not normative workflow policy.

## GitHub Operations

When working in this repository from Codex or a similar sandboxed assistant environment:

- Use the GitHub plugin for all GitHub operations (reading issues, creating PRs, posting comments, updating issues, adding reviews).
- Do not use `gh` CLI for GitHub API operations — the GitHub plugin is available and works within the sandbox without escalation.
- Use normal workspace-scoped execution for local file reads, local file edits, and deterministic checks.

## Intent

The goal is to avoid wasted retries caused by sandbox restrictions. The GitHub plugin handles all GitHub API needs; local execution handles all repo file operations.

## Workflow Commands

To refine an issue: Follow `.claude/commands/refine.md` with the issue number.
To implement an issue: Follow `.claude/commands/implement.md` with the issue number.
To review a PR: Follow `.claude/commands/review.md` with the PR number.
To merge a PR: Follow `.claude/commands/merge.md` with the PR number.
To clean up after a merged PR: Follow `.claude/commands/cleanup.md` with the PR number.
