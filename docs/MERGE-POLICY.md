# Merge Policy

Merge policy is normative. Adapters may add stricter gates, but they must not weaken these rules.

## Required Merge Conditions

A PR may be merged only when all conditions are true after a fresh GitHub refresh:

- The PR is open and targets the expected base branch.
- The current PR head SHA is known.
- The approval being relied on applies to the current PR head SHA.
- No new commits were pushed after the relied-on approval.
- Required CI/checks have completed successfully for the current head SHA.
- Blocking review threads or comments are resolved or explicitly waived according to repo policy.
- Required manual gates are satisfied.
- The merge owner is the session that last implemented the approved current head SHA, or an authorized human/operator acting under explicit takeover rules.

## Solo-Maintainer Bootstrap Override

Some repositories have only one GitHub account with write access, especially during bootstrap. In that case GitHub may prevent a formal approval even when review was performed in a separate assistant session.

A solo-maintainer override may substitute for formal GitHub approval only when all conditions are true:

- The repository has no available independent GitHub approver.
- Review was performed in a separate session from the session that implemented the reviewed head SHA.
- The review is recorded on GitHub against the current head SHA.
- The review summary explicitly states that no blockers remain for that exact head SHA.
- Any earlier blocking inline comments are resolved, outdated by later commits, or explicitly waived in GitHub.
- The PR is not draft.
- A fresh GitHub refresh immediately before merge confirms the current head SHA still matches the reviewed head SHA.
- Required CI/checks and adapter gates are passing, absent by policy, or explicitly waived in GitHub.

This override is a bootstrap/solo-maintainer exception, not the default approval path. It must be visible in GitHub history and must not be used when an independent GitHub approver is available.

## SHA-Bound Approval

Approval is not a general property of a PR. Approval is a property of a reviewed PR head SHA.

If the PR head SHA changes after approval:

- Existing approval must be treated as stale until re-evaluated.
- Merge must be blocked.
- Review delta should be shown to the reviewer.
- A new review or explicit re-approval must be recorded against the new head SHA.

## Fresh GitHub Refresh

Merge must never rely on stale local state.

Immediately before merge, the merge owner must refresh from GitHub and verify:

- PR number and repository.
- Current head SHA.
- Base branch.
- Approval state.
- Review thread state.
- CI/check state.
- Branch protection or mergeability state.

## Merge Ownership

Merge ownership belongs to the implementation session that last produced the approved head SHA. This creates a clean chain:

implementation session -> pushed head SHA -> separate review session -> approval -> same implementation session may merge after fresh refresh

If that session is unavailable, a takeover session may become merge owner only after:

- Fetching fresh GitHub PR context.
- Confirming no local-only state is required.
- Recording takeover intent.
- Re-running pre-merge checks.

## Merge Command Behavior

The merge command must:

- Refuse to merge without a fresh pre-merge result.
- Refuse to merge if the current head SHA differs from the approved SHA.
- Refuse to merge if required CI is pending, failing, missing, or stale.
- Refuse to merge if unresolved blockers exist.
- Report the exact PR, head SHA, base branch, and merge method before action.

## Post-Merge Requirements

After merge:

- Confirm merged state in GitHub.
- Record or emit merge SHA when available.
- Sync local base branch if a local checkout exists.
- Mark temporary worktrees and branches as cleanup candidates.
