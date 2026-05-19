# Codex PR Review Adapter

**Adapter name:** `codex-review-pr`
**Adapter type:** `assistant`
**Inputs:** PR number, repository (`OWNER/REPO`)
**Commands provided:** Session-start prompt for Codex review sessions
**Core gates depended on:** `pr-context`, `review-sha`, `approval-check`, `unresolved-review-threads`, GitHub source-of-truth refresh
**Additional gates added:** None
**Failure mode:** Advisory — the workflow must remain valid if this adapter is absent

---

Use this prompt when a Codex session should review a GitHub pull request without implementing fixes.

```text
Review PR <PR_NUMBER> in <OWNER>/<REPO> using the headless PR workflow.

You are acting only as reviewer for the current PR head SHA. Do not implement fixes in this session. Do not merge the PR from this review session.

GitHub is the source of truth for PR state, review threads, approvals, CI, blockers, and merge state. Review findings must be recorded on GitHub, not only in chat.

First refresh GitHub state:
- Run `hpw pr-context <PR_NUMBER> --repo <OWNER>/<REPO>` if hpw is available in this Codex environment.
- Fallback (no hpw): run `gh pr view <PR_NUMBER> --repo <OWNER>/<REPO> --json number,title,state,isDraft,baseRefName,headRefName,headRefOid,reviewDecision,reviews,statusCheckRollup,mergeStateStatus,body,comments,files`.
- Run `hpw review-sha <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: inspect headRefOid from the above JSON) to record the current head SHA before reviewing.
- Run `hpw workflow-status <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: read the JSON output) to confirm review is the correct next action.

Record review findings on GitHub:
- Use GitHub PR review comments for line-specific findings.
- Use a GitHub PR review summary for overall findings and the final no-blockers statement.
- If you cannot create GitHub comments or reviews, say so clearly and provide exact findings in chat so they can be copied to GitHub.
- Do not treat chat-only feedback as workflow-authoritative.

Run deterministic checks:
- Run `hpw unresolved-review-threads <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: inspect unresolved threads from the JSON output).
- Run `hpw ci-summary <PR_NUMBER> --repo <OWNER>/<REPO>` (or fallback: inspect statusCheckRollup).
- Run any repo-adapter checks documented in the repo.
- Record any checks that could not be run and why.

Review for:
- correctness
- safety and failure modes
- SHA-bound approval readiness
- review/implementation separation
- portability across assistant sessions and platforms
- missing or weak deterministic tests
- whether repo-specific concerns are in adapters rather than core policy

Return findings ordered by severity. If no blockers remain, say so explicitly in a GitHub PR review summary and include the reviewed head SHA.

If no blockers remain and the PR is still draft, mark it Ready for review before leaving the final approval or solo-maintainer override summary. If you cannot change draft state, say so clearly in the GitHub review summary.

If GitHub allows formal approval and no blockers remain, approve the PR.

If GitHub blocks formal approval because the authenticated account owns the PR, leave a GitHub PR review summary attached to the reviewed head SHA that states:
- `Reviewed head SHA <head-sha>`
- `No blockers remain for <head-sha>.`
- `solo-maintainer override accepted.`
- `Formal GitHub approval is unavailable because no independent GitHub approver is available for this pull request.`
- `This solo-maintainer override is the approval to rely on for the current head SHA.`
- whether the PR was marked Ready for review, or that changing draft state was unavailable

Do not implement fixes. Do not merge the PR.
```

## Short User Invocation

```text
Use examples/assistants/codex/REVIEW-PR.md to review PR <PR_NUMBER> in <OWNER>/<REPO>.
```
