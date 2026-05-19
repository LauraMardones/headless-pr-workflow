# Codex PR Implementation Adapter

**Adapter name:** `codex-implement-pr`
**Adapter type:** `assistant`
**Inputs:** PR number, repository (`OWNER/REPO`)
**Commands provided:** Session-start prompt for Codex implementation sessions
**Core gates depended on:** `pr-context`, `workflow-status`, GitHub source-of-truth refresh
**Additional gates added:** None
**Failure mode:** Advisory — the workflow must remain valid if this adapter is absent

---

Use this prompt when a Codex session should implement changes on a GitHub pull request branch without reviewing or approving its own work.

```text
Implement changes for PR <PR_NUMBER> in <OWNER>/<REPO> using the headless PR workflow.

You are acting only as implementer for this session. Do not act as reviewer or approver for the same PR head SHA you implement in this session. If review is needed after your commits, request or retrigger a separate review session.

GitHub is the source of truth for issue context, PR state, review threads, approvals, CI, blockers, and merge state. Local branches, worktrees, chat history, and terminal output are disposable execution context.

First refresh GitHub state before implementation:
- Run `hpw pr-context <PR_NUMBER> --repo <OWNER>/<REPO>` if hpw is available in this Codex environment.
- Fallback (no hpw): run `gh pr view <PR_NUMBER> --repo <OWNER>/<REPO> --json number,title,state,isDraft,baseRefName,headRefName,headRefOid,reviewDecision,reviews,statusCheckRollup,mergeStateStatus,body,comments,files`.
- Run `hpw workflow-status <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: read the above JSON) to determine the safe next action.
- Read any linked issue, PR description, review comments, unresolved threads, failing checks, and handoff notes from GitHub.
- Record the current PR head SHA before making changes.
- Continue only if implementation is the safe next action.

Prepare a disposable implementation context:
- Fetch current remote refs.
- Confirm the local branch or worktree is on the PR head branch, or check out the PR branch explicitly.
- Keep GitHub state authoritative when local state disagrees with GitHub.

Implement only the requested changes:
- Follow the linked issue, PR description, current review blockers, and repo docs.
- Do not resolve review comments only in chat; push code or record the blocker on GitHub.
- Do not perform a review or approval of the head SHA produced by this session.

Before pushing:
- Run deterministic tests and checks relevant to the repository.
- Record any checks that could not be run and why.

When implementation is complete:
- Commit the intended changes only.
- Push commits to the PR branch.
- Confirm the new PR head SHA from GitHub after pushing.
- Mark the PR Ready for review (if it was draft), or request review for the new head SHA using the repo's normal GitHub mechanism.
- Leave a handoff note as a GitHub PR comment with:
  - PR number
  - current head SHA
  - last action performed
  - known blockers
  - checks run or skipped
  - suggested next action
  - confirmation that this session implemented changes and did not review or approve the resulting head SHA

Do not merge the PR unless the user explicitly asks you to act as merge owner.
```

## Short User Invocation

```text
Use examples/assistants/codex/IMPLEMENT-PR.md to implement PR <PR_NUMBER> in <OWNER>/<REPO>.
```
