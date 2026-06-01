# Agent Guidance

This file defines project-level assistant guidance for work in this repository. It is advisory assistant behavior, not normative workflow policy.

## GitHub Operations

When working in this repository from Codex or a similar sandboxed assistant environment:

- Use the GitHub plugin for all GitHub operations (reading issues, creating PRs, posting comments, updating issues, adding reviews).
- Do not use `gh` CLI for GitHub API operations — the GitHub plugin is available and works within the sandbox without escalation.
- Use normal workspace-scoped execution for local file reads, local file edits, and deterministic checks.

## Stale Checkout Handling

The sandbox workspace may be initialized from an older checkout and `git fetch` may be blocked. At the start of each session:

1. Run `git log --oneline -1` to get the local HEAD commit.
2. Use the GitHub plugin to get the latest commit SHA on `main`.
3. If they differ, the local checkout is stale — do not trust local file contents.
4. Read all repository files via the GitHub plugin (`get_file_contents`) instead of the local filesystem until the checkout is current.
5. For edits: write changes to local files as normal, but verify against the remote version first so edits apply on top of the current content.

## Intent

The goal is to avoid wasted retries caused by sandbox restrictions. The GitHub plugin handles all GitHub API needs; local execution handles repo file operations only when the checkout is confirmed current.

## Workflow Commands

To refine an issue: Follow `.claude/commands/refine.md` with the issue number.
To implement an issue: Follow `.claude/commands/implement.md` with the issue number.
To review a PR: Follow `.claude/commands/review.md` with the PR number.
To merge a PR: Follow `.claude/commands/merge.md` with the PR number.
To clean up after a merged PR: Follow `.claude/commands/cleanup.md` with the PR number.
