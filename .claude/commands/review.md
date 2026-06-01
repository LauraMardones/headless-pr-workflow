Review PR #$ARGUMENTS in LauraMardones/headless-pr-workflow.

Follow:
- docs/MERGE-POLICY.md

You are acting only as reviewer in this session.
Do not implement fixes in this session.
Do not merge the PR from the review session.

Important:
- Review only the current head SHA.
- Use GitHub as source of truth.
- If no blockers remain, use formal approval when GitHub allows it.
- If GitHub blocks approval because the authenticated account owns the PR, follow the documented solo-maintainer override path exactly.

Workflow status goals:
- If blockers are found: "In review" -> "In progress"
- If no blockers remain for the current head SHA: "In review" -> "In merge"

Required behavior:
1. Refresh PR state from GitHub before reviewing.
2. Record the current PR head SHA before review.
3. Review only that current head SHA.
4. Validate the PR using the relevant deterministic checks and documented repo checks.
5. Review for:
   - correctness
   - safety and failure modes
   - missing or weak deterministic tests
   - unresolved blockers
   - review/implementation separation
   - whether any new commit has made earlier approval stale
6. If blockers are found:
   - record findings on GitHub in the correct PR review surfaces
   - use "Request changes" when available
   - clearly describe the blockers
   - set the story status to "In progress"
   - state that the next action is implementation
7. If no blockers remain:
   - if the PR is still Draft, mark it Ready for review before final approval output when possible
   - if GitHub allows formal approval, approve the PR
   - if GitHub blocks formal approval because the authenticated account owns the PR, use the solo-maintainer override path exactly as documented in docs/MERGE-POLICY.md
   - ensure the approval evidence is recorded against the current head SHA
   - set the story status to "In merge"
   - state that the next action is merge
8. If the PR head SHA changes during review, stop and re-evaluate against the new SHA instead of continuing on stale review context.

Do not rely on chat-only feedback as workflow-authoritative if it is not also recorded on GitHub.

---

## Required GitHub Output — Must Not Be Skipped

**Formal PR review posted on the current head SHA** via `mcp__github__pull_request_review_write`.

- If blockers remain: submit with event `REQUEST_CHANGES`.
- If no blockers remain: submit with event `APPROVE`, or leave a solo-maintainer override summary if GitHub blocks self-approval.

The Session Summary block is **unconditionally required** — include it in every review regardless of whether findings exist. `Blockers` and `Next action` must never be omitted. When no blockers are found, write `Blockers: none` and `Next action: merge`. Only `Head SHA` may be omitted for non-code reviews where no commit SHA was examined.

```
## Session Summary
Command: review
Issue / PR: #<pr-number>
Head SHA: <reviewed-sha>
Checks run: <list, or "none">
Blockers: <list, or "none">
Next action: <"implementation" or "merge">
```
