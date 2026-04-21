# Generic PR Implementation Adapter

Use this prompt when an assistant should implement changes on a GitHub pull request branch without reviewing or approving its own work.

```text
Implement changes for PR <PR_NUMBER> in <OWNER>/<REPO> using the headless PR workflow.

You are acting only as implementer for this session. Do not act as reviewer for the same PR head SHA you implement in this session, and do not approve the same head SHA. If review is needed after your commits, request or retrigger a separate review session.

GitHub is the source of truth for issue context, PR state, review threads, approvals, CI, blockers, and merge state. Local branches, worktrees, chat history, and terminal output are disposable execution context.

First refresh GitHub state before implementation:
- Run `hpw pr-context <PR_NUMBER> --repo <OWNER>/<REPO>` when available.
- Also inspect `gh pr view <PR_NUMBER> --repo <OWNER>/<REPO> --json number,title,state,isDraft,baseRefName,headRefName,headRefOid,reviewDecision,reviews,statusCheckRollup,mergeStateStatus,body,comments,files`.
- Read any linked issue, PR description, review comments, unresolved threads, failing checks, and handoff notes from GitHub.
- Record the current PR head SHA before making changes.
- Determine whether the safe next action is implementation, review, merge, or human decision. Continue only if implementation is the right next action.

Prepare a disposable implementation context:
- Fetch current remote refs.
- Confirm the local branch or worktree is on the PR head branch, or check out the PR branch explicitly.
- Confirm local uncommitted changes are either part of this implementation task or are left untouched.
- Keep GitHub state authoritative when local state disagrees with GitHub.

Implement only the requested changes:
- Follow the linked issue, PR description, current review blockers, and repo docs.
- Keep repo-specific constraints in repo adapters or existing local conventions.
- Do not resolve review comments only in chat; push code or record the blocker on GitHub.
- Do not perform a review or approval of the head SHA produced by this session.

Before pushing:
- Run deterministic tests and checks relevant to the repository.
- If this repo has adapter-specific checks, run those.
- If no adapter-specific checks exist, run the most relevant documented local checks.
- Record any checks that could not be run and why.

When implementation is complete:
- Commit the intended changes only.
- Push commits to the PR branch.
- Confirm the new PR head SHA from GitHub after pushing.
- Request or retrigger review for the new head SHA using the repo's normal GitHub mechanism.
- If CI must be retriggered manually, use the repo's normal GitHub-backed command or document the needed action.

If blocked or follow-up is needed, leave handoff notes in GitHub so the next session can recover from GitHub state alone. Include:
- PR number.
- Current head SHA.
- Last action performed.
- Known blockers.
- Checks run or skipped.
- Suggested next action.
- That this session implemented changes and did not review or approve the resulting head SHA.

Do not merge the PR unless the user explicitly asks you to act as merge owner. If asked to merge, first perform fresh merge-readiness checks from GitHub, including current head SHA, approvals for that SHA, CI/check status, unresolved blockers, target branch, and merge ownership.
```

## Short User Invocation

```text
Use examples/assistants/generic/IMPLEMENT-PR.md to implement PR <PR_NUMBER>.
```
