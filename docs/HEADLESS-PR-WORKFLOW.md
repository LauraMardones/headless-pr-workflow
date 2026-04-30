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

## Solo-Maintainer Review Flow

Some repositories have only one GitHub account with write access. In that case, the workflow must still preserve review separation without inventing extra GitHub accounts or pretending that formal GitHub approval exists when GitHub does not allow it.

Use the solo-maintainer path only when the conditions in `MERGE-POLICY.md` are satisfied. The override substitutes only for formal GitHub approval. It does not waive any other merge condition:

- The review must still be performed in a separate review session from the session that implemented the current head SHA.
- The review must still be recorded on GitHub for the exact current head SHA.
- The PR must still be Ready for review, not draft.
- Required CI/checks must still pass for the current head SHA.
- Blocking review threads or comments must still be resolved, outdated, or explicitly waived according to repo policy.
- The merge owner must still perform a fresh GitHub refresh immediately before merge.

Practical sequence for a one-person repository:

1. The implementation session pushes the candidate head SHA and requests review without self-approving that SHA.
2. A separate review session refreshes GitHub state, reviews the exact current head SHA, and records blockers or a no-blockers review summary on GitHub.
3. If formal GitHub approval is unavailable because there is no independent approver, that review session records the solo-maintainer override summary described in `MERGE-POLICY.md`.
4. If the review finds blockers, the implementation session makes changes and pushes a new head SHA. The earlier review is then stale and the loop restarts.
5. After a no-blockers review exists for the current head SHA, the merge owner performs a fresh refresh and re-checks merge readiness before merging that same SHA.

The GitHub review summary for the override should make three facts explicit:

- Formal GitHub approval is unavailable in this repository.
- No blockers remain for the exact current head SHA.
- The solo-maintainer override is the approval to rely on for that current head SHA.

This keeps the flow compatible with deterministic commands:

- `approval-check` may treat the review summary as satisfying the approval gate only when the summary is attached to the current head SHA and contains the required solo-maintainer override language.
- `pre-merge` must still fail if the PR is draft, if checks are missing or failing, if mergeability is bad, if unresolved review threads remain active, or if the reviewed SHA is no longer current.

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
