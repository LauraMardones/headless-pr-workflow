Review PR #$ARGUMENTS in LauraMardones/headless-pr-workflow.

Follow:
- docs/MERGE-POLICY.md

You are acting only as reviewer in this session.
Do not implement fixes in this session.
Do not merge the PR from the review session.

## GitHub operation fallback

- Prefer the GitHub plugin/MCP integration for GitHub reads and mutations when it is available.
- If the plugin/MCP integration is unavailable, use the authenticated `gh` CLI for required reads and mutations.
- Use a direct GitHub API request only when neither the plugin/MCP integration nor `gh` supports the required operation.
- Before any mutation, verify the target repository, PR number, and current head SHA. Refresh them again if the review state changes.
- Never expose, print, log, persist, or commit GitHub credentials.
- The fallback changes only the transport. It never bypasses workflow gates, and it must produce the same durable GitHub evidence as the preferred integration.

Important:
- Review only the current head SHA.
- Use GitHub as source of truth.
- If no blockers remain, use formal approval when GitHub allows it.
- If GitHub blocks approval because the authenticated account owns the PR, follow the documented solo-maintainer override path exactly.

Workflow status goals:
- If blockers are found: "In review" -> "In implementation"
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
   - set the story status to "In implementation"
   - state that the next action is implementation
   - before resolving an older review thread, refresh it, confirm it belongs to the verified PR, and resolve it only if the finding is superseded; never resolve a still-actionable thread
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

**Formal PR review posted on the current head SHA** using the available GitHub operation fallback. If formal self-approval is unavailable, record the documented solo-maintainer override against that same verified SHA.

- If blockers remain: submit with event `REQUEST_CHANGES`.
- If no blockers remain: submit with event `APPROVE`, or leave a solo-maintainer override summary if GitHub blocks self-approval.

The Session Summary block is **unconditionally required**. Generate it with `scripts/session-summary.sh --command review --pr $ARGUMENTS --head <reviewed-sha> --checks <checks> --blockers <blockers> --next <implementation|merge>` and post the exact stdout with the formal review. Use `--next implementation` when blockers require another implementation cycle and `--next merge` when none remain. Supply every required argument; use explicit values such as `--blockers none`. Use repeatable `--deviation <text>` flags for deviations, residual risks, or decisions. Do not add any recap section or freeform PR-body/AC repetition after the generated block.
