# Codex Fix-Review Adapter

**Adapter name:** `codex-fix-review`
**Adapter type:** `assistant`
**Inputs:** PR number, repository (`OWNER/REPO`)
**Commands provided:** Session-start prompt for Codex fix-review (reimplementation) sessions triggered by review blockers
**Core gates depended on:** `pr-context`, `review-sha`, `unresolved-review-threads`, `blocking-comments`, GitHub source-of-truth refresh
**Additional gates added:** None
**Failure mode:** Advisory — the workflow must remain valid if this adapter is absent

---

Use this prompt when a Codex session should address review blockers on a PR that was returned from review (phase G in the workflow lifecycle). This is a reimplementation session, not a combined review-and-fix session.

```text
Address review blockers for PR <PR_NUMBER> in <OWNER>/<REPO> using the headless PR workflow.

You are acting only as implementer for this fix-review session. You were triggered because a prior review session recorded blockers on GitHub. Your role is to implement the fixes and produce a new head SHA ready for re-review.

Do not act as reviewer for the head SHA produced by this session. Do not approve the new head SHA you implement. Do not treat any earlier approval as valid once you push a new commit — any prior approval is stale after new commits are pushed.

GitHub is the source of truth for review blockers, PR state, unresolved threads, failing checks, and handoff notes. Local branches, worktrees, chat history, and terminal output are disposable execution context.

First refresh GitHub state before implementation:
- Run `hpw pr-context <PR_NUMBER> --repo <OWNER>/<REPO>` if hpw is available in this Codex environment.
- Fallback (no hpw): run `gh pr view <PR_NUMBER> --repo <OWNER>/<REPO> --json number,title,state,isDraft,baseRefName,headRefName,headRefOid,reviewDecision,reviews,statusCheckRollup,mergeStateStatus,body,comments,files`.
- Run `hpw unresolved-review-threads <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: inspect review threads from the JSON) to identify all unresolved blockers.
- Run `hpw blocking-comments <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: inspect review comment blocking status) to identify blocking comments.
- Run `hpw workflow-status <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: read the JSON) to confirm reimplementation is the correct next action.
- Record the current PR head SHA before making any changes.
- Read every review comment, unresolved thread, and handoff note on GitHub before touching any code.
- Continue only if fix-review implementation is the safe next action.

Prepare a disposable implementation context:
- Fetch current remote refs.
- Confirm the local branch or worktree is on the PR head branch, or check out the PR branch explicitly.
- Keep GitHub state authoritative when local state disagrees with GitHub.

Implement fixes only for recorded review blockers:
- Address each blocker documented in GitHub review threads or comments.
- Do not expand scope beyond the recorded blockers.
- Do not resolve review comments only in chat; push code or record the resolution in GitHub.
- Do not perform a review or approval of the new head SHA produced by this session.

Before pushing:
- Run deterministic tests and checks relevant to the repository.
- Record any checks that could not be run and why.

When fixes are complete:
- Commit only the blocker fixes.
- Push commits to the PR branch.
- Confirm the new PR head SHA from GitHub after pushing.
- Request or retrigger review for the new head SHA — a new review session is required because any prior approval is stale.
- Leave a handoff note as a GitHub PR comment with:
  - PR number
  - new head SHA
  - list of blockers addressed
  - checks run or skipped
  - suggested next action: re-review of the new head SHA by a separate session
  - confirmation that this session implemented fixes and did not review or approve the new head SHA

Do not merge the PR from this session.
```

## Short User Invocation

```text
Use examples/assistants/codex/FIX-REVIEW.md to address review blockers for PR <PR_NUMBER> in <OWNER>/<REPO>.
```
