# Headless PR Workflow

This document defines the normative workflow for assistant-agnostic, GitHub-centered pull request work.

## Non-Negotiable Invariants

- GitHub is the system of record for issues, pull requests, reviews, approvals, CI status, blockers, and merge state.
- Local branches, worktrees, editor state, terminal history, and assistant chat sessions are disposable execution contexts.
- A review approval applies only to the exact PR head SHA that was reviewed.
- If new commits are pushed after approval, approval must be re-evaluated against the new head SHA.
- Implementation and review must not happen in the same session for the same reviewed head SHA.
- Merge is allowed only after a fresh GitHub refresh confirms current head SHA, approvals, CI, blockers, target branch, and merge owner.
- Takeover between assistants, machines, and sessions must be recoverable from GitHub state plus explicit handoff notes.
- Repo-specific constraints must be implemented through adapters, not core workflow policy.
- Assistant-specific behaviors must be optional integrations, not normative requirements.

## Lifecycle

The workflow loops until the PR is either merged or intentionally abandoned:

1. Issue or task context is selected from GitHub.
2. An implementation session creates or takes over a branch/worktree.
3. Implementation produces commits and pushes them to the PR branch.
4. Review readiness checks run against the current GitHub PR state.
5. A separate review session reviews the current head SHA.
6. Blockers are recorded in GitHub review threads, comments, checks, or issue/PR metadata.
7. If blockers require changes, a new implementation loop pushes a new head SHA.
8. Approval is re-evaluated after every new push.
9. Merge readiness checks run after a fresh GitHub refresh.
10. The merge owner merges only the approved current head SHA.
11. Post-merge sync and cleanup are performed.

## State Model

The workflow distinguishes between durable and temporary state.

Durable state:

- GitHub issue state.
- GitHub PR state.
- PR head SHA.
- Reviews and approvals.
- Review threads and unresolved blockers.
- CI checks and check suites.
- Merge commit and merged branch state.
- Explicit handoff notes stored in GitHub or committed workflow metadata.

Temporary state:

- Local branch checkout.
- Worktree path.
- Assistant session memory.
- Terminal scrollback.
- Local scratch files.
- Unpushed commits.

Temporary state can help execution, but it must not be required to determine PR truth.

## Review Separation

For a reviewed head SHA, the same assistant session must not both implement and approve/review that SHA. A session may implement fixes after review, but the resulting new head SHA requires review by a separate review session.

This rule prevents self-review loops where an assistant validates its own implementation without an independent review context.

## Deterministic Automation

Scripts should cover facts and gates that can be determined mechanically:

- Current PR head SHA.
- Latest relevant approval SHA.
- Whether new commits exist after approval.
- CI/check status for the current SHA.
- Unresolved review threads.
- Target branch policy.
- Merge ownership.
- Worktree and branch hygiene.

Humans and assistants should focus on interpretation, architecture, UX, product judgment, risk assessment, and decisions that cannot be reduced to deterministic checks.

## Extension Model

Core workflow policy defines what must be true. Adapters define how a specific repo or assistant satisfies optional local conventions.

- Core workflow is reusable across repositories.
- Repo adapters encode project-specific checks.
- Assistant adapters encode optional prompt and session conventions.
- No adapter may weaken core approval, review separation, or merge policy.
