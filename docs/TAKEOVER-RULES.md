# Takeover Rules

Takeover is the process of continuing PR work from a different assistant, session, machine, or worktree.

## Takeover Principles

- GitHub state is authoritative.
- The previous local session is not required.
- Takeover must preserve review separation.
- Takeover must not assume approval is still valid.
- Takeover must identify whether the next action is implementation, review, merge, or human decision.

## Required Takeover Steps

Before acting on a PR, a takeover session must:

1. Fetch current PR context from GitHub.
2. Record PR number, title, branch, base branch, and current head SHA.
3. Check latest approval and the SHA it applies to.
4. Check unresolved review threads and blockers.
5. Check CI/check status for the current head SHA.
6. Check whether new commits exist after approval.
7. Identify the last implementation source when known.
8. Select the next safe action.

## Implementation Takeover

Implementation takeover is allowed when:

- The PR requires changes.
- The current session is not reviewing the same head SHA it will implement.
- The session creates or reuses a disposable worktree.
- The session pushes a new head SHA when fixes are complete.

After new commits are pushed, any previous approval must be re-evaluated.

## Review Takeover

Review takeover is allowed when:

- The session did not implement the reviewed head SHA.
- The current head SHA is freshly fetched from GitHub.
- The review output is recorded in GitHub.

Review takeover must not rely on local notes that are not represented in GitHub or explicit handoff metadata.

## Merge Takeover

Merge takeover is stricter than implementation takeover.

A takeover session may merge only when:

- The original merge owner is unavailable or explicitly hands off ownership.
- The takeover is recorded.
- The current head SHA still matches the approved head SHA.
- Fresh pre-merge checks pass.
- Core merge policy allows the takeover.

## Handoff Notes

Handoff notes should be concise and reconstructable from GitHub state. They should include:

- PR number.
- Current head SHA at handoff time.
- Last action performed.
- Known blockers.
- Suggested next action.
- Whether the session implemented, reviewed, or only inspected.

Handoff notes are advisory. GitHub state remains authoritative.
