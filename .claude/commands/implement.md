Implement issue #$ARGUMENTS in LauraMardones/headless-pr-workflow using GitHub as source of truth.

You are acting only as implementer in this session.
Do not review or approve the same PR head SHA that you implement in this session.

## GitHub operation fallback

- Prefer the GitHub plugin/MCP integration for GitHub reads and mutations when it is available.
- If the plugin/MCP integration is unavailable, use the authenticated `gh` CLI for required reads and mutations.
- Use a direct GitHub API request only when neither the plugin/MCP integration nor `gh` supports the required operation.
- Before any mutation, verify the target repository and issue or PR number. For review- or merge-related mutations, also verify the current head SHA where applicable.
- Never expose, print, log, persist, or commit GitHub credentials.
- The fallback changes only the transport. It never bypasses workflow gates, and it must produce the same durable GitHub evidence as the preferred integration.

Follow core workflow policy and keep repo-specific or governance rules out of core commands.

Workflow status goals:
- Create and link a PR as early as possible.
- PR linked to issue -> story status should be "In implementation".
- When implementation is ready for review -> story status should be "In review".
- If you are fixing review blockers on an existing PR, keep the story in "In implementation" while fixes are underway, then move it back to "In review" only when the new head SHA is ready for review.
- Write a board status with `scripts/set-project-status --issue <n> --status "<Status name>" --repo LauraMardones/headless-pr-workflow` (exit `0` only after it read the status back; non-zero with the reason on stderr otherwise). Board status writes are best-effort. If a status write fails because `gh` or GraphQL is unavailable, do not stop: continue the command and record the skipped status change in the Session Summary with `--deviation` (for example `--deviation "board status not updated: gh GraphQL unavailable; intended: In review"`).

Required behavior:
1. Read the GitHub issue, acceptance criteria, linked context, and any existing PR state first.
2. Run a pre-flight environment check before implementation begins:
   - confirm `git status` shows no unexpected staged or uncommitted changes
   - confirm the current branch or note that a fresh branch will be created
   - confirm GitHub API access by fetching the issue or repo metadata
   - stop and report if any pre-flight check fails
3. Fetch and pull the latest `main` before creating a new branch or PR.
4. Use a dedicated branch for this story only, named `<agent>/issue-<number>-<short-slug>` per `docs/WORKTREE-MODEL.md`. If the session was assigned a branch name it cannot change, use it and state the conforming name in the PR body.
5. Keep the work strictly within the issue scope and acceptance criteria.
6. If the issue is dependency-heavy, verify that hard dependencies are complete before implementation starts.
7. Run the WIP pre-flight check defined in `docs/PROJECT-STATUS.md` before pulling the story:
   - skip this check when the story is already "In implementation" and has an open PR linked to it (continuing work, or fixing review blockers under steps 10 and 17): the check applies only when a story is pulled from "Ready for implementation", and this story already holds its place within the WIP limit
   - read the candidate story's `## Files affected` section
   - scan open stories with status "In implementation"
   - stop if 2 or more stories are already "In implementation"
   - stop if any active story declares overlapping files
   - report any WIP limit failure or file overlap as a conflict blocker using the Blocked Status Protocol
8. If you discover blocked prerequisites, unclear acceptance criteria, scope drift, unresolved dependency decisions, WIP limit violations, or file ownership conflicts:
   - before declaring a `Type: decision` blocker, search `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections; follow and record an existing answer instead of escalating it
   - only if no documented answer exists, declare the decision blocker with the protocol's required `Checked:` line naming what was searched
   - when declaring the decision blocker, also write the `Question:` (one sentence), `Options:` (2–4 lines, each `<letter>) <option> — <consequence>`) and `Recommendation:` (one option letter plus a one-sentence reason) fields, after `Checked:`
   - stop
   - report WIP limit violations and file ownership conflicts through the Blocked Status Protocol
   - report other blockers clearly
   - do not silently expand scope
9. If no PR exists yet:
   - create a Draft PR as early as possible
   - the PR body must be non-empty: include `Closes #$ARGUMENTS` and a brief description of what the PR changes and why; `--fill` alone is insufficient
   - if a branch for the story already exists on the remote without a PR (a session stopped, for example at a provider usage limit, before it opened the draft PR), continue on that branch and open the PR from it; do not start a second branch
   - link the PR to issue #$ARGUMENTS
   - ensure the PR references and closes #$ARGUMENTS when merged
   - ensure the story moves to "In implementation" (best-effort, as described under Workflow status goals)
10. If a PR already exists:
   - continue on the existing PR branch
   - update the existing PR instead of opening a parallel PR unless explicitly needed
   - keep the story in "In implementation" while implementation or blocker fixes are ongoing
   - if the existing PR is Ready for review (a review-fix run), convert it back to Draft before pushing (for example `gh pr ready <pr> --undo --repo LauraMardones/headless-pr-workflow`), so the dispatcher's draft signal shows the handoff incomplete until the new head's `pytest` check is green
11. Implement only the requested changes.
12. Add or update deterministic tests as needed.
13. For mutation work, preserve fresh-refresh and safety checks before mutating behavior.
14. Push commits to the same PR branch as work progresses.
15. Before requesting review, perform a self-review:
   - run `git diff main` (or the equivalent against the base branch) and verify the diff matches the issue scope exactly
   - run `scripts/ac-summary.sh --issue $ARGUMENTS --repo LauraMardones/headless-pr-workflow` and use the extracted checklist to verify AC/DoD coverage; exit `2` means no checklist was found and requires a manual issue-body fallback, not that coverage passed, while exit `1` is an invocation or lookup failure
   - if `scripts/ac-summary.sh` exits `1` because `gh` or GraphQL is unavailable, read the AC/DoD checklist from the issue body through the available GitHub transport (the same manual fallback as exit `2`) and continue; AC/DoD coverage must still be verified
   - do not copy the extracted AC/DoD checklist into the handoff comment
   - if any changed file is eligible under `scripts/dispatcher-change-check.sh`, run `bash scripts/dispatcher-change-check.sh --files <changed-files...>`; fix every `FAIL` (exit `1`) before handoff, and treat exit `2` as an invocation or input blocker
   - confirm the PR body contains `Closes #$ARGUMENTS` and a meaningful description
   - confirm the issue link in the PR references the correct issue number
   - confirm command files use `In implementation`, not the legacy progress-status spelling
   - confirm the PR title and body are accurate and not auto-generated boilerplate
   - run a failure-path pre-mortem on the diff: list every operation the diff adds or changes that is an external call (GitHub REST/GraphQL, `gh`, `git push`/`fetch`, any network call), a GitHub mutation (comment, label, status, review, thread resolution, board write, merge, branch), a persisted-state write (files used as state, Actions cache, artifacts, comments or markers read back later as state), or a GitHub Actions workflow step or condition that runs one of these (`if:`, `always()`, step order, concurrency); for each one, check these cases: (1) the operation fails; (2) it partly succeeds; (3) it is retried after an earlier attempt succeeded or partly succeeded; (4) the job or workflow is re-run; (5) the job or workflow is cancelled mid-way; (6) the state it reads is missing, stale or unreadable; (7) the PR head SHA changes between read and mutation
   - give each operation and case exactly one outcome: **handled** (the code handles it, with a deterministic test where a script-level test is possible); **not applicable** (one clause of reason, for example "read-only call"); or **residual risk** (not handled or not testable, for example workflow-level behaviour pytest cannot reach); a case with no outcome means the pre-mortem is incomplete, so fix the code or record the case before handoff; keep the per-case list in this session, not in the PR or any comment
   - record the pre-mortem in the Session Summary as `--deviation` flags: one `pre-mortem: <n> operations checked; residual risks: <count|none>` line, or `pre-mortem: not applicable — the diff adds no external call, GitHub mutation, persisted-state write or workflow step`; and one `residual risk: <operation> — <case> — <why it is accepted>` line for each residual risk
16. When implementation is complete:
   - run focused local tests before the final push, not the full suite (see `AGENTS.md` → Running Tests): the test files for changed modules or scripts and for directly affected callers, commands, or contracts, found by repository search when names do not match; a change to shared test infrastructure (`conftest.py`, `tests/dispatcher_fakes.py`, another shared fixture or helper) needs every test file that uses it or a representative subset
   - record the exact commands, results, selected coverage and reason for each selected file, and any omitted local coverage or unrun check with its reason, in the handoff `--checks` and `--deviation` fields
   - before the final push, on a first run and on a review-fix run, check whether the PR branch conflicts with the base branch (fetch `origin/main` with full history, then `git merge --no-commit --no-ff origin/main`, or read `mergeable` / `mergeable_state` from `gh api repos/LauraMardones/headless-pr-workflow/pulls/<pr>`; GitHub starts no `pull_request` CI run for a conflicting PR); if it conflicts, merge `origin/main` into the branch with a merge commit, resolve the conflict without dropping main's changes, rerun the focused tests and push the merge as an ordinary commit; never rebase and never force-push
   - if you cannot resolve the conflict, keep the PR Draft and the story "In implementation", and report `merge conflict with main on PR #<n>` as the blocker in the Session Summary `--blockers`, without `--next review`
   - commit only the intended changes and push
   - confirm the current PR head SHA from GitHub
   - read the required `pytest` check for that exact head SHA; only a successful result for the current head counts, and a failed, pending, absent, unreadable, or older-head result never does
   - if the `pytest` check failed: keep the PR Draft (re-draft it if it is Ready for review) and the story "In implementation", inspect the failures, fix the implementation or tests, rerun the affected local tests, push a new head, and wait for that head's check
   - while the check is pending, keep this session alive and poll the current head's `pytest` check (for example `gh pr checks <pr> --repo LauraMardones/headless-pr-workflow --watch`, or re-read it at intervals) until it completes; the check normally reports within minutes, so do not stop on the first pending read, and re-read the PR head SHA before each poll so a newer head restarts the wait
   - if the `pytest` check for the current head is absent, read the PR's mergeability (`mergeable` / `mergeable_state` through `gh api repos/LauraMardones/headless-pr-workflow/pulls/<pr>`) before reporting; when the PR conflicts with main, report `merge conflict with main` as the cause ("PR conflicts with main; GitHub starts no CI run for a conflicting PR"), not a guess at another cause such as the push token, and resolve it as described above
   - if the `pytest` check is still pending after waiting as long as the session allows, or is absent or unreadable: do not claim completion, keep the PR Draft (re-draft it if it is Ready for review) and the story "In implementation", and report the state or blocker truthfully; a later continuation re-reads the PR head and check instead of trusting an earlier result
   - only when the current head's `pytest` check is green, continue with the steps below (Ready for review, "In review", Codex review request, completion handoff with `--next review`); until then do not use `--next review`
   - mark the PR Ready for review, or explicitly request review if Ready for review is unavailable
   - set the story status to "In review" (best-effort, as described under Workflow status goals)
   - if the story carries an `executor:claude-code-*` label, request the Codex review (ADR-006): once the PR is Ready for review, post exactly one PR conversation comment whose first line is the Codex review trigger phrase (read it from ADR-006 → Decision or the constant `CODEX_REVIEW_TRIGGER_PHRASE` in `src/headless_pr_workflow/review_policy.py`)
   - post no review request on intermediate pushes, never post one on an issue, and post none for an `executor:codex` story
   - write the trigger phrase only as that PR conversation comment, never in an issue, a PR description, a committed file or any other comment, because it starts a Codex task wherever it appears
   - do not run `/review` for a Claude-implemented story: the `codex-review-bookkeeping` workflow moves the story and posts the review Session Summary after Codex answers
   - generate the handoff with `scripts/session-summary.sh --command implement --issue $ARGUMENTS --pr <pr-number> --head <current-head-sha> --checks <checks> --blockers <blockers> --next review` and post its exact stdout
   - use repeatable `--deviation <text>` flags when deviations, residual risks, or decisions must be recorded; the step 15 `pre-mortem:` line is always required, and each residual risk gets its own `residual risk:` line
   - do not add any section after the generated Session Summary block; the PR body is the implementation document, so do not append “What was implemented”, “AC coverage”, or equivalent recap prose
17. If you are addressing review blockers on an existing PR:
   - treat any earlier approval as stale once a new commit is pushed
   - never leave the story in "In merge" after a new implementation commit
   - return the story to "In review" only when the new head SHA is ready for review
   - before changing code, classify every unresolved finding on the current head as **Blocker** or **Out of scope** against the linked story's `## Acceptance Criteria`, `## Scope`, `## Out of Scope` and the documented policy: a Blocker breaks or does not meet those, or is a defect the PR itself introduced; an Out of scope finding is a valid observation the story does not own (behaviour the PR did not add or change, an edge case outside the acceptance criteria, or an Epic/Feature requirement the story did not take on); when in doubt it is a Blocker
   - classifying is not a waiver: only the PO waives a finding; never resolve the thread of an Out of scope finding
   - for each Out of scope finding, do not start a fix and make no commit for it: link an existing open follow-up issue that covers it, or create one draft follow-up issue (`type:story` or `type:bug`, body with `Origin:` linking the finding, `PO status: Backlog`, no `executor:` label); reply on the finding thread with the classification, the reason and the follow-up link, without resolving it
   - when the round leaves at least one Out of scope finding unwaived, declare one `Type: decision` blocker on the story (Question: waive the listed findings for this PR; Options: A) waive and track in the follow-up issues, B) bring them into this story's scope and fix them here; Recommendation: A); name the Out of scope findings and follow-up issues in a `--deviation` line of the Session Summary, not in `--blockers`
   - for each Blocker, fix the defect class, not only the reported line: name the class in one phrase (the general mistake, not the line, e.g. "phase marked done before its evidence comment is posted"); search the PR's own change for every other instance of that class: all files and hunks in `git diff <base>...HEAD`, plus the functions, scripts and command steps that the changed code calls or that call it, where the same pattern can recur; fix every instance that lies in code or text the PR added or changed, in the same round, before pushing; an instance found only in code the PR did not add or change is **Out of scope** under the rule above (follow-up issue, reply, no fix in this PR), not a reason to widen the PR; where a deterministic test can catch the class, the test added for the finding covers the other fixed instances too, where practical
   - the fix-round Session Summary records one `--deviation` line per Blocker finding in the form `Class check <finding link>: class "<class>"; checked <locations>; also fixed <locations | none>; out of scope <follow-up link | none>`; a finding with no other instance still gets the line, with `also fixed none`
   - fix Blockers as usual; after a round of fixes for Codex findings on an `executor:claude-code-*` story, post exactly one new review request (a PR conversation comment whose first line is the same trigger phrase), after the round's last push and after the story is back in "In review"; when the round also leaves an Out of scope finding unwaived, post no review request after the push: a clean Codex result on the new head would mark the old-head thread superseded and let the story reach `In merge` past the PO waiver, so the single review request waits for the post-waiver step below
   - when the round has only Out of scope findings, push nothing and post no review request; the story waits for the PO's decision
   - the waived thread is resolved by the PO, not by this session: the PO's waiver names the finding and its follow-up issue, and the PO resolves that thread on GitHub (a Codex thread on the current head is not superseded by a later review of the same head, so it stays unresolved until the PO resolves it)
   - after a PO waiver (a PR comment naming each waived finding and its follow-up issue) and `/unblock` on the story, there is nothing left to fix: return the story to "In review"; for an `executor:claude-code-*` story post exactly one new review request for the current head, and for an `executor:codex` story post none (step 16 forbids it) because its paired Claude `/review` session reviews the head; if a waived thread is still unresolved, record in a `--deviation` line that the PO must resolve it before merge

Do not merge the PR.
Do not perform review approval.
Record workflow-relevant facts in GitHub, not only in chat.

---

## Required GitHub Output — Must Not Be Skipped

**Draft PR created early** using the available GitHub operation fallback (draft: true), linked to the issue.
**Handoff comment posted on the PR** at completion using the available GitHub operation fallback.

Generate the handoff with `scripts/session-summary.sh --command implement` and all required phase arguments, then post the exact stdout. Do not add prose or sections after the generated block.
