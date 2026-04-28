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

Solo-maintainer example:

- If no independent GitHub approver exists, record a GitHub review summary for the current head SHA stating that no blockers remain, formal approval is unavailable, and the solo-maintainer override is the approval to rely on for that SHA.
- Do not treat the override as permission to skip CI, unresolved blocker cleanup, or the Ready for review transition.

## Merge Owner

The merge owner performs the final merge action for an approved PR head SHA.

Responsibilities:

- Confirm the current head SHA matches the approved head SHA.
- Confirm merge ownership according to `MERGE-POLICY.md`.
- Run a fresh pre-merge check immediately before merging.
- Merge only through GitHub or a GitHub-backed command.
- Perform or trigger post-merge sync and cleanup.

Solo-maintainer example:

- Treat the reviewer summary as a substitute only for formal approval.
- Still require a fresh GitHub refresh, a matching current head SHA, and passing merge gates before merging.

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

## Worked Solo-Maintainer Flow

Implementation session responsibilities:

- Produce the candidate changes and push the reviewable head SHA.
- Request review from a separate session instead of self-approving.
- If blockers are found, address them by pushing a new head SHA and requesting another review.

Review session responsibilities:

- Refresh GitHub state before reviewing so the exact current head SHA is known.
- Record blockers on GitHub, or record a no-blockers review summary for that SHA.
- When formal approval is unavailable, use the solo-maintainer override language required by `MERGE-POLICY.md`.

Merge-owner responsibilities:

- Refresh GitHub again immediately before merge.
- Confirm the reviewed SHA still matches the current PR head SHA.
- Run fresh merge gates such as `pre-merge`; the solo-maintainer override does not bypass checks or stale-state protection.
