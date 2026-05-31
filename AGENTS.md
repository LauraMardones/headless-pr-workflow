# Agent Guidance

This file defines project-level assistant guidance for work in this repository. It is advisory assistant behavior, not normative workflow policy.

## Sandbox And Escalation

When working in this repository from Codex or a similar sandboxed assistant environment:

- Prefer direct escalated execution for `gh`, GitHub auth, network-dependent GitHub operations, and repo operations that frequently fail in the sandbox.
- Do not spend time on avoidable "try in sandbox first" retries when the command is likely to need higher privileges or broader environment access.
- Continue to use normal workspace-scoped execution for local file reads, local file edits inside the repository, and deterministic checks that do not need broader access.
- If a command can safely run without escalation and is unlikely to hit sandbox limits, keep it inside the workspace sandbox.

## Intent

The goal is to reduce wasted retries and command rewrites caused by sandbox restrictions, while still keeping ordinary repository edits and local checks lightweight.

## Workflow Commands

To refine an issue: Follow `.claude/commands/refine.md` with the issue number.
To implement an issue: Follow `.claude/commands/implement.md` with the issue number.
To review a PR: Follow `.claude/commands/review.md` with the PR number.
To merge a PR: Follow `.claude/commands/merge.md` with the PR number.
To clean up after a merged PR: Follow `.claude/commands/cleanup.md` with the PR number.
