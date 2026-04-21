# Worktree Model

Worktrees and branches are temporary execution contexts. They help assistants and humans work safely, but they are not the source of truth.

## Principles

- A worktree belongs to a task, PR, assistant, and session.
- Worktrees may be deleted after work is pushed or abandoned.
- Unpushed local commits are not durable workflow state.
- Fresh GitHub context must be fetched at session start and before merge.
- Worktree naming should make accidental cross-session work obvious.

## Naming

Recommended branch pattern:

```text
hpw/<issue-or-pr>-<short-slug>
```

Recommended worktree pattern:

```text
../wt/<repo>/<pr-number>-<assistant>-<session-id>
```

Examples:

```text
hpw/123-sha-bound-approval
../wt/example-repo/123-codex-20260421-0915
../wt/example-repo/123-claude-review-20260421-1010
```

Repo adapters may override branch conventions when a project already has established naming policy.

## Session Start

At session start:

- Fetch remote refs.
- Identify current repo and branch.
- Fetch PR context from GitHub.
- Confirm whether the local branch tracks the PR branch.
- Confirm whether the current worktree has uncommitted or unpushed changes.
- Determine next action from GitHub state.

## Cleanup

Worktrees can be cleaned when:

- The PR is merged and local sync is complete.
- The branch is abandoned and no unpushed work remains.
- A newer takeover worktree supersedes the old one.

Cleanup must not delete unpushed work without explicit confirmation.
