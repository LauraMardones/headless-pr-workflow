Review PR #$ARGUMENTS in LauraMardones/headless-pr-workflow.

Follow:
- docs/MERGE-POLICY.md

You are acting only as reviewer in this session.
Do not implement fixes in this session.
Do not merge the PR from the review session.

## When this command applies

- It remains the review path for `executor:codex` stories, which Claude Opus reviews, and for a separate-session review under ADR-007 path 2.
- A Claude-implemented story (`executor:claude-code-*`) is reviewed by Codex (ADR-006). The `codex-review-bookkeeping` workflow then moves the story and posts the review Session Summary with no model session, so do not run this command for it unless a path 2 review is explicitly wanted.

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
- If GitHub blocks approval because the authenticated account owns the PR, record the separate-session review summary documented in docs/MERGE-POLICY.md exactly, including the `Reviewer provider:` line.
- Never compose or paraphrase the separate-session review summary. Generate it with `hpw review-evidence-text` or copy the template below verbatim; the approval gate rejects any other wording.

Workflow status goals:
- If blockers are found: "In review" -> "In implementation"
- If no blockers remain for the current head SHA: "In review" -> "In merge"
- Write a board status with `scripts/set-project-status --issue <n> --status "<Status name>" --repo LauraMardones/headless-pr-workflow` (exit `0` only after it read the status back; non-zero with the reason on stderr otherwise). Board status writes are best-effort. If a status write fails because `gh` or GraphQL is unavailable, do not stop: continue the command and record the skipped status change in the Session Summary with `--deviation` (for example `--deviation "board status not updated: gh GraphQL unavailable; intended: In merge"`).

Required behavior:
1. Refresh PR state from GitHub before reviewing.
2. Record the current PR head SHA before review.
3. Review only that current head SHA.
4. Validate the PR using the relevant deterministic checks and documented repo checks.
5. Review for:
   - correctness
   - safety and failure modes
   - missing or weak deterministic tests
   - acceptance criteria of the linked issue that the change does not meet, or that are testable but not covered by a test
   - unresolved blockers
   - review/implementation separation
   - whether any new commit has made earlier approval stale
   - classify every finding against the linked story's `## Acceptance Criteria`, `## Scope`, `## Out of Scope` and the documented policy (`docs/MERGE-POLICY.md`, accepted ADRs, `AGENTS.md`):
     - **Blocker**: the change breaks or does not meet the story's acceptance criteria, its documented scope or documented policy, or the change itself introduces a defect (a bug in code the PR added or changed)
     - **Out of scope**: a valid observation the story does not own: behaviour the PR did not add or change, an edge case outside the story's acceptance criteria, or an Epic/Feature requirement the story did not take on
     - when in doubt, a finding is a Blocker
     - classifying is not a waiver: only the PO waives a finding, with a PR comment that names it and its follow-up issue
   - do not report again an Out of scope finding the PO already waived on the PR; a PO comment that names a Blocker is not a waiver, because a Blocker is fixed in the PR and is never waived, so keep reporting it
6. If blockers are found:
   - before declaring a `Type: decision` blocker, search `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections; follow and record an existing answer instead of escalating it
   - only if no documented answer exists, declare the decision blocker with the Blocked Status Protocol's required `Checked:` line naming what was searched
   - when declaring the decision blocker, also write the `Question:` (one sentence), `Options:` (2–4 lines, each `<letter>) <option> — <consequence>`) and `Recommendation:` (one option letter plus a one-sentence reason) fields, after `Checked:`
   - record findings on GitHub in the correct PR review surfaces
   - use "Request changes" when available
   - clearly describe the blockers
   - set the story status to "In implementation" (best-effort, as described under Workflow status goals)
   - state that the next action is implementation
   - before resolving an older review thread, refresh it, confirm it belongs to the verified PR, and resolve it only if the finding is superseded; never resolve a still-actionable thread
   - if a superseded thread cannot be resolved because `gh` or GraphQL is unavailable, leave the thread open and list it in the Session Summary with `--deviation`; never treat it as resolved
7. If no blockers remain:
   - if the PR is still Draft, mark it Ready for review before final approval output when possible
   - if GitHub allows formal approval, approve the PR
   - if GitHub blocks formal approval because the authenticated account owns the PR, submit a `COMMENT` review that carries the separate-session review summary from docs/MERGE-POLICY.md (Path 2)
   - generate the summary with `hpw review-evidence-text <head-sha> --reviewer-provider <claude|codex>`, using the full 40-character head SHA, and post its exact output
   - if the helper cannot be run, copy this template verbatim, changing only `<head-sha>` and `<claude|codex>`:

     ```text
     Reviewed head SHA `<head-sha>`.

     No blockers remain for <head-sha>.

     solo-maintainer override accepted.

     Reviewer provider: <claude|codex>

     This solo-maintainer override is the approval to rely on for the current head SHA.
     ```

   - never compose or paraphrase these sentences
   - put the summary as its own block at the top of the review body, above the Session Summary; never rely on a `Deviation:` line as the only place that carries it
   - set `Reviewer provider:` to the provider running this review session (`claude` for Claude Code, `codex` for Codex); the approval gate rejects the summary when that provider is the one that implemented the PR, per the linked story's `executor:` label, so do not record it in that case and report that a different provider must review
   - ensure the approval evidence is recorded against the current head SHA
   - after posting a separate-session review summary, run `hpw approval-check $ARGUMENTS --repo LauraMardones/headless-pr-workflow` and confirm it prints `separate-session review: accepted`
   - if it is not accepted, post one corrected review on the same head SHA and run the check again
   - if it is still not accepted, do not claim the PR is ready to merge: leave the story in "In review", name the rejected evidence in `--blockers`, do not use `--next merge` (use `--next implementation`, the only other value the script accepts), and add a `--deviation` saying that only the review evidence needs redoing, not the code
   - if `hpw approval-check` cannot be run in the session, say so with `--deviation`; the verbatim-template rule still applies
   - set the story status to "In merge" (best-effort, as described under Workflow status goals)
   - state that the next action is merge
   - an Out of scope finding is reported with a proposed follow-up issue (title and one-line reason) or a link to an existing issue
   - it is not listed in `--blockers`, does not by itself cause `REQUEST_CHANGES`, and is recorded with a `--deviation` line
   - the PR stays unmergeable until the PO waives it (`Type: decision` blocker), so do not set the story to "In merge" or use `--next merge` while one is unwaived
   - when the only findings are unwaived Out of scope ones, this session records them itself and does not rely on a later `/implement` round: create or link the follow-up issues, declare one `Type: decision` blocker on the story (Question: waive the listed findings for this PR; Options: A) waive and track in the follow-up issues, B) bring them into this story's scope and fix them here; Recommendation: A), submit the review with event `COMMENT` (neither `APPROVE` nor `REQUEST_CHANGES`), leave the story in "In review", use `--blockers none` and `--next implementation` (the only other value the script accepts; it records that no merge is possible yet, not that code must change), and name the findings and follow-up issues in the `--deviation` line; the missing approval and the unresolved thread keep `/merge` from succeeding if the orchestrator reaches it anyway
8. If the PR head SHA changes during review, stop and re-evaluate against the new SHA instead of continuing on stale review context.

Do not rely on chat-only feedback as workflow-authoritative if it is not also recorded on GitHub.

---

## Required GitHub Output — Must Not Be Skipped

**Formal PR review posted on the current head SHA** using the available GitHub operation fallback. If formal self-approval is unavailable, record the documented separate-session review summary against that same verified SHA.

- If blockers remain: submit with event `REQUEST_CHANGES`.
- If no blockers remain: submit with event `APPROVE`, or leave a separate-session review summary with its `Reviewer provider:` line if GitHub blocks self-approval.

The Session Summary block is **unconditionally required**. Generate it with `scripts/session-summary.sh --command review --pr $ARGUMENTS --head <reviewed-sha> --checks <checks> --blockers <blockers> --next <implementation|merge>` and post the exact stdout with the formal review. Use `--next implementation` when blockers require another implementation cycle and `--next merge` when none remain. Supply every required argument; use explicit values such as `--blockers none`. Use repeatable `--deviation <text>` flags for deviations, residual risks, or decisions. Do not add any recap section or freeform PR-body/AC repetition after the generated block.
