# Agent Guidance

This file defines project-level assistant guidance for work in this repository. It is advisory assistant behavior, not normative workflow policy.

## GitHub Operations

When working in this repository from Codex or a similar sandboxed assistant environment:

- Use the GitHub plugin/MCP integration for GitHub operations when it is available.
- If the GitHub plugin/MCP integration is not available in the current session, use the authenticated `gh` CLI for required GitHub reads and mutations.
- Use direct GitHub API calls such as `curl` only when neither the plugin/MCP integration nor `gh` supports the required operation.
- Never expose, print, log, persist, or commit GitHub credentials.
- Before mutating a pull request, review, issue, or branch, verify the target repository and resource number. For review- or merge-related operations, also verify the current head SHA where relevant.
- Use normal workspace-scoped execution for local file reads, local file edits, and deterministic checks.

The fallback changes only the transport used for a GitHub operation. It does not
relax approval, review, status-transition, branch-protection, or merge gates. A
fallback mutation must produce the same durable GitHub evidence required by the
workflow that requested it.

For review-thread resolution, refresh the pull request and thread first, confirm
that the thread belongs to the verified pull request, and resolve it only when
the finding is superseded. For formal reviews and solo-maintainer overrides,
record evidence against the verified current head SHA. For merges, refresh the
head SHA and every required gate immediately before the merge mutation.

Four steps need the `gh` CLI (2.50 or later) authenticated with GraphQL access:
board status writes, `scripts/ac-summary.sh`, review-thread resolution, and
`scripts/merge-gate-summary`. GitHub Actions and a local session provide this; a
Claude Code cloud session does not, and the Codex sandbox may not. The symptom
is HTTP 403 "GraphQL is not available from Claude Code sessions" or
`gh: command not found`. Board writes are best-effort: when the capability is
missing they are skipped with a note in the Session Summary, and `/implement`
and `/review` still finish their real work. The merge gate is never skipped, so
`/merge` requires the capability and stops before any GitHub mutation without
it.

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
| `gh` CLI blocked | `gh issue view` and similar commands are denied | Use `mcp__github__*` tools exclusively. Board status writes are skipped with a note, and `/merge` cannot run there because the merge gate needs GraphQL-capable `gh`. See [GitHub Operations](#github-operations). |

## Branch Naming

Name every branch `<agent>/issue-<number>-<short-slug>`, or
`<agent>/<kind>-<short-slug>` when there is no issue, as defined in
[docs/WORKTREE-MODEL.md](docs/WORKTREE-MODEL.md#naming). The name must say what
the branch is for; no random suffixes.

## Review Guidelines

This section applies to automated pull request reviewers, such as Codex Cloud
automatic review, that can comment on a PR but do not run the full `/review`
workflow in `.claude/commands/review.md` (board transitions, formal approval,
session summary).

Review the PR's current head commit for:

- Correctness: logic errors, wrong conditions, off-by-one errors, unhandled
  error paths, broken exit codes.
- Workflow-policy safety: anything that would let a PR merge without the gates
  in `docs/MERGE-POLICY.md` — approval bound to the reviewed head SHA, green
  required checks, no unresolved review threads, a fresh refresh before merge.
- Tests: behavior changes without a deterministic test that would fail on the
  bug.
- Consistency: a change to workflow behavior that contradicts `docs/*.md` or an
  accepted ADR in `docs/decisions/` without updating it.
- Acceptance criteria: when the PR is linked to an issue, every acceptance
  criterion in that issue is met by the change and, where it is testable,
  covered by a test. An unmet or untested criterion is a P1 finding.

For every finding, state the file and line, the concrete consequence, and a
reproduction case or failing input. Report only P0/P1 issues; do not comment on
style, naming, or formatting. If you find no P0/P1 issues, say so explicitly
and name the head commit you reviewed.

When fixing a review finding that a deterministic test could have caught, add
that test in the same fix.

## Intent

The goal is to avoid wasted retries caused by sandbox restrictions. The GitHub plugin handles all GitHub API needs; local execution handles repo file operations only when the checkout is confirmed current.

## Workflow Commands

To specify an Epic with the PO: Follow `.claude/commands/spec.md` with no argument, a quoted working title, or the Epic issue number; it opens a spec PR that only the PO merges.
To refine an issue: Follow `.claude/commands/refine.md` with the issue number.
To implement an issue: Follow `.claude/commands/implement.md` with the issue number.
To review a PR: Follow `.claude/commands/review.md` with the PR number.
To merge a PR: Follow `.claude/commands/merge.md` with the PR number.
To clean up after a merged PR: Follow `.claude/commands/cleanup.md` with the PR number.
To verify Feature or Epic closure: Follow `.claude/commands/verify-closure.md`
with exactly one issue number. This first phase is read-only with respect to
repository contents and issue lifecycle state; only its single successful
technical-evidence comment is permitted before PO product confirmation.
After that confirmation, run the same command again for the fail-closed,
idempotent closure continuation documented by the command contract.
