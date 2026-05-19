# Generic PR Takeover Adapter

Use this prompt when an assistant is resuming a PR after review feedback, a conflict resolution, or a merge-owner handoff.

```text
Take over PR <PR_NUMBER> in <OWNER>/<REPO> using the headless PR workflow.

You are acting in exactly one role for this session: implementer, reviewer, or merge owner. Determine your role from the recommended next action class produced by the takeover command. Do not perform a second role in the same session. If you are taking over for implementation, do not review or approve the head SHA you produce.

GitHub is the source of truth for PR state, approval, CI, review threads, and handoff notes. Local branches, worktrees, and prior chat history are disposable.

First run the takeover command:
- Run `hpw pr-takeover <PR_NUMBER> --repo <OWNER>/<REPO>` as the mandatory first step.
- Record the recommended next action class: `implementation`, `review`, `merge`, or `human_decision`.
- Also inspect `gh pr view <PR_NUMBER> --repo <OWNER>/<REPO> --json number,title,state,isDraft,baseRefName,headRefName,headRefOid,reviewDecision,reviews,statusCheckRollup,mergeStateStatus,body,comments,files`.
- Record the current head SHA before acting.
- If the recommended next action is `human_decision`, stop and report the blocker. Do not proceed.

Identify the handoff trigger and apply the rules for that scenario:

Post-review (review feedback requires implementation changes):
- New commits have been requested or required by review feedback.
- Any prior approval applies only to the SHA that was approved, not to the new head SHA this session will produce.
- After pushing new commits, the prior approval is invalidated. Re-review is required before merge.
- This session must not review or approve the head SHA it implements.

Post-conflict (conflict was resolved and ownership must transfer):
- A conflict resolution commit produces a new head SHA.
- Any prior approval applies only to the SHA that was approved before the conflict resolution.
- The new head SHA requires re-review before merge, regardless of who resolved the conflict.
- If this session resolves the conflict and pushes a new head SHA, it must not review or approve that SHA.

Merge-owner handoff (original merge owner is unavailable):
- The original merge owner has handed off or is unavailable.
- Before merging, confirm the current head SHA still matches the approved head SHA.
- Run fresh pre-merge checks from GitHub: current head SHA, approvals for that SHA, CI/check status, unresolved blockers, target branch.
- If the current head SHA does not match the approved SHA, do not merge. The PR needs re-review first.
- Merge only through GitHub or a GitHub-backed command.

Prepare a disposable execution context only after determining the next action:
- Fetch current remote refs.
- Confirm the local branch or worktree matches the PR head branch, or check out the PR branch explicitly.
- Keep GitHub state authoritative when local state disagrees.

If your role is implementer:
- Follow the linked issue, PR description, current review blockers, and repo docs.
- Do not resolve review comments only in chat; push code or record the blocker on GitHub.
- Run deterministic tests and checks relevant to the repository before pushing.
- Record any checks that could not be run and why.
- Commit only the intended changes and push to the PR branch.
- Confirm the new head SHA from GitHub after pushing.
- Request or retrigger review for the new head SHA.
- Do not review or approve the head SHA produced in this session.

If your role is reviewer:
- Follow the review steps in the applicable review adapter or core review guidance.
- Do not implement fixes in this session.
- Record all findings in GitHub review comments or review summaries.
- Approve only the specific head SHA reviewed.

If your role is merge owner:
- Confirm current head SHA matches the approved head SHA before merging.
- Run fresh pre-merge checks.
- Merge only through GitHub or a GitHub-backed command.
- Do not merge on a stale or unapproved head SHA.

When this session is complete, leave handoff notes in GitHub so the next session can recover from GitHub state alone. Include:
- PR number.
- Current head SHA at handoff time.
- Last action performed.
- Known blockers.
- Checks run or skipped.
- Suggested next action.
- The role this session performed and whether the resulting head SHA has been reviewed or approved.
```

## Short User Invocation

```text
Use examples/assistants/generic/TAKEOVER-PR.md to take over PR <PR_NUMBER>.
```
