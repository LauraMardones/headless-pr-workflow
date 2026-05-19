# Codex Merge-Owner Adapter

**Adapter name:** `codex-merge-owner`
**Adapter type:** `assistant`
**Inputs:** PR number, repository (`OWNER/REPO`), session ID, expected owner
**Commands provided:** Session-start prompt for Codex merge-owner sessions
**Core gates depended on:** `pre-merge`, `merge-owner`, `approval-check`, `target-branch-check`, `ci-summary`, `unresolved-review-threads`, GitHub source-of-truth refresh
**Additional gates added:** None
**Failure mode:** Advisory — the workflow must remain valid if this adapter is absent

---

Use this prompt when a Codex session should perform the final merge of an approved PR after all gates pass.

```text
Merge PR <PR_NUMBER> in <OWNER>/<REPO> as merge owner using the headless PR workflow.

You are acting only as merge owner for this session. Do not implement new changes in this session. Do not review or approve any head SHA in this session.

GitHub is the source of truth for PR state, head SHA, approval state, CI status, unresolved blockers, and merge readiness. Do not rely on local state, chat history, or prior session memory for merge decisions.

You must perform a fresh GitHub refresh immediately before any merge action — not at session start and again at merge time, but as a single refresh immediately before the merge command is issued.

First refresh GitHub state before proceeding:
- Run `hpw pr-context <PR_NUMBER> --repo <OWNER>/<REPO>` if hpw is available in this Codex environment.
- Fallback (no hpw): run `gh pr view <PR_NUMBER> --repo <OWNER>/<REPO> --json number,title,state,isDraft,baseRefName,headRefName,headRefOid,reviewDecision,reviews,statusCheckRollup,mergeStateStatus,body,comments,files`.
- Record the current PR head SHA.
- Run `hpw workflow-status <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: inspect the JSON output) to confirm merge is the correct next action.

Immediately before merging, run the pre-merge check:
- Run `hpw pre-merge <PR_NUMBER> --repo <OWNER>/<REPO>` if hpw is available.
- Fallback (no hpw): manually verify each of the following from a fresh GitHub refresh immediately before the merge action:
  1. PR is open and targets the expected base branch.
  2. Current head SHA is known and unchanged since the refresh.
  3. Approval applies to the current head SHA (not a stale SHA after new commits).
  4. No new commits were pushed after the relied-on approval.
  5. Required CI/checks have completed successfully for the current head SHA.
  6. No unresolved blocking review threads or comments remain.
  7. Required manual gates are satisfied.
  8. This session is the authorized merge owner (or a valid takeover is recorded in GitHub).

Verify merge ownership:
- Run `hpw merge-owner <PR_NUMBER> --repo <OWNER>/<REPO> --session-id <SESSION_ID> --expected-owner <EXPECTED_OWNER>` if hpw is available.
- Fallback (no hpw): confirm that the implementation session that last produced the approved head SHA authorized this merge, or that an explicit takeover is recorded in GitHub.

Do not merge if any pre-merge gate fails. Do not merge if the current head SHA differs from the approved head SHA. Do not merge if required CI is pending, failing, missing, or stale. Do not merge if unresolved blockers exist. Do not skip the fresh GitHub refresh before merge.

If all gates pass, merge using a GitHub-backed command:
- Run `hpw merge-pr <PR_NUMBER> --repo <OWNER>/<REPO>` if hpw is available.
- Fallback (no hpw): run `gh pr merge <PR_NUMBER> --repo <OWNER>/<REPO> --squash` (or the merge method appropriate for this repo).

After merge:
- Confirm merged state in GitHub.
- Run `hpw post-merge-sync --repo <OWNER>/<REPO>` (or fallback: `git fetch origin && git checkout main && git pull origin main`) to sync local base branch.
- Run `hpw branch-cleanup --repo <OWNER>/<REPO>` (or fallback: identify and remove the now-merged PR branch from local) to clean up stale branches.
- Leave a merge confirmation note on GitHub with:
  - PR number
  - merged head SHA
  - merge method used
  - confirmation that all pre-merge gates passed

Do not implement new changes. Do not review or approve any head SHA in this session.
```

## Short User Invocation

```text
Use examples/assistants/codex/MERGE-OWNER.md to merge PR <PR_NUMBER> in <OWNER>/<REPO>.
```
