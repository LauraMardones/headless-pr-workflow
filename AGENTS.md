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
6. If `git push` or `git fetch` fails due to sandbox restrictions, use the GitHub plugin's `push_files` tool to push file changes directly via the API — do not give up or report a blocker.
7. To create a PR when git CLI is unavailable, use the GitHub plugin's `create_pull_request` tool directly.

## Running Tests

Run the test suite with:

```
python -m pytest
```

The root-level `conftest.py` redirects pytest's temp directory to `.pytest_tmp/` one level above the repo root (outside the git working tree) to avoid two issues: the system temp directory may not be writable in sandboxed environments like Codex, and a temp dir inside the repo causes tests that expect a non-git environment to fail.

**Fallback**: If you still encounter temp-directory setup errors (e.g. on Windows environments where the parent directory is also restricted), set the environment variable before running:

```
PYTEST_DEBUG_TEMPROOT=C:\tmp  # Windows / Codex Windows sandbox
```

`C:\tmp` is a confirmed writable location in the Codex Windows sandbox. On Linux/macOS, the `conftest.py` fix should be sufficient without any env-var override.

## Codex Sandbox Constraints

Quick reference for constraints specific to the Codex Windows sandbox. Each entry: constraint — symptom — workaround.

| Constraint | Symptom | Workaround |
|---|---|---|
| `.git/` directory operations blocked | `git switch`, `git fetch` fail with lock or permission errors | Use `mcp__github__*` tools for all branch and commit operations. See [Stale Checkout Handling](#stale-checkout-handling). |
| Default Python version lacks pytest | `python -m pytest` → `No module named pytest` | Use `py -3.12 -m pytest`. Run `py -0p` to list available Python versions. |
| pytest temp root outside sandbox | Setup errors on first test run | `conftest.py` redirects temp root automatically (fix from #135). If errors persist, set `PYTEST_DEBUG_TEMPROOT=C:\tmp`. See [Running Tests](#running-tests). |
| `gh` CLI blocked | `gh issue view` and similar commands are denied | Use `mcp__github__*` tools exclusively. See [GitHub Operations](#github-operations). |

## Intent

The goal is to avoid wasted retries caused by sandbox restrictions. The GitHub plugin handles all GitHub API needs; local execution handles repo file operations only when the checkout is confirmed current.

## Workflow Commands

To refine an issue: Follow `.claude/commands/refine.md` with the issue number.
To implement an issue: Follow `.claude/commands/implement.md` with the issue number.
To review a PR: Follow `.claude/commands/review.md` with the PR number.
To merge a PR: Follow `.claude/commands/merge.md` with the PR number.
To clean up after a merged PR: Follow `.claude/commands/cleanup.md` with the PR number.
