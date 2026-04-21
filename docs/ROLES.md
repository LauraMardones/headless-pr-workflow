# Roles

Roles describe responsibilities in the workflow. A role may be performed by a human, an assistant, or a script, but role separation rules still apply.

## Implementer

The implementer changes code, docs, tests, configuration, or other repo content for a PR.

Responsibilities:

- Start from fresh GitHub PR context.
- Keep local worktree state disposable.
- Push implementation commits to the PR branch.
- Run deterministic readiness checks before requesting review.
- Respond to blockers by creating a new implementation head SHA.
- Avoid reviewing or approving the same head SHA implemented in the same session.

## Reviewer

The reviewer evaluates a specific PR head SHA.

Responsibilities:

- Refresh PR context from GitHub before review.
- Identify correctness, safety, architecture, UX, test, and maintainability risks.
- Record blockers in GitHub review comments, review threads, or checks.
- Approve only the specific head SHA reviewed.
- Re-review if implementation commits change the head SHA after approval.

## Merge Owner

The merge owner performs the final merge action for an approved PR head SHA.

Responsibilities:

- Confirm the current head SHA matches the approved head SHA.
- Confirm merge ownership according to `MERGE-POLICY.md`.
- Run a fresh pre-merge check immediately before merging.
- Merge only through GitHub or a GitHub-backed command.
- Perform or trigger post-merge sync and cleanup.

## Orchestrator

The orchestrator coordinates state across issues, PRs, sessions, assistants, and loops.

Responsibilities:

- Select next action from GitHub state.
- Route work to implementation, review, adapter, or human decision paths.
- Preserve handoff context when work moves across sessions.
- Avoid treating any local session as authoritative.

## Adapter Author

The adapter author maintains optional repo or assistant integrations.

Responsibilities:

- Keep adapters subordinate to core policy.
- Clearly label adapter-specific assumptions.
- Provide deterministic checks where possible.
- Avoid encoding product-specific rules in core workflow scripts.

## Human Approver

A human approver supplies judgment that should not be automated away.

Responsibilities:

- Approve product, risk, design, legal, or release decisions when required.
- Resolve ambiguous policy conflicts.
- Decide whether an adapter-specific gate is required for a repo.
