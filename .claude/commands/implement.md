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

Workflow facts this command records (it writes no board status; the board shows these facts):
- Create and link a PR as early as possible.
- PR linked to issue -> the Draft PR is the "implementation in progress" fact.
- When implementation is ready for review -> the PR is Ready for review, its `pytest` check is green, and the handoff says `--next review`.
- If you are fixing review blockers on an existing PR, keep the PR Draft while fixes are underway, then mark it Ready for review only when the new head SHA is ready for review.

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
   - the linked Draft PR is the record that the story is in implementation; write no board status
10. If a PR already exists:
   - continue on the existing PR branch
   - update the existing PR instead of opening a parallel PR unless explicitly needed
   - keep the PR Draft while implementation or blocker fixes are ongoing
   - if the existing PR is Ready for review (a review-fix run), convert it back to Draft before pushing (for example `gh pr ready <pr> --undo --repo LauraMardones/headless-pr-workflow`), so the dispatcher's draft signal shows the handoff incomplete until the new head's `pytest` check is green
   - conflict round (issue #409): when the existing PR conflicts with main (`mergeable == false` or `mergeable_state == "dirty"` from `gh api repos/LauraMardones/headless-pr-workflow/pulls/<pr>`) and no review finding on its current head is unresolved, this run only brings the PR up to date with main: keep a PR that is Ready for review as it is for the whole round (do not convert it to Draft before pushing, and not in step 16 either, unlike a review-fix run: after a stopped round the dispatcher decides from the non-draft PR, issue #449), merge `origin/main` into the PR branch with a merge commit, resolve the conflict without dropping main's changes or the PR's own changes, make no other change, run the focused tests for the files the conflict touched, push, and finish with step 16 (green `pytest` on the new head, Ready for review, the Codex review request, the Session Summary); when the new head's `pytest` check failed, is still pending when the session stops waiting, or is absent or unreadable, make no fix and no Draft conversion: leave the PR Ready for review, post no review request, and generate the handoff with `--next implementation` (the dispatcher reads the check and posts the blocker); if you cannot resolve the conflict, push nothing and follow step 16's unresolved-conflict rule (`merge conflict with main on PR #<n>`), except that the PR stays Ready for review
11. Implement only the requested changes.
12. Add or update deterministic tests as needed.
13. For mutation work, preserve fresh-refresh and safety checks before mutating behavior.
14. Push commits to the same PR branch as work progresses, and keep a durable checkpoint on the draft PR (issue #490):
   - checkpoint push: push a checkpoint commit to the draft PR branch after the first working edit and after each later coherent group of edits; push a checkpoint before a long test run, and at the latest every 15 turns (tool calls) since the last push, whenever the working tree has uncommitted changes
   - a checkpoint is an ordinary commit (a WIP message is allowed) and an ordinary `git push`: no rebase and no force-push, as in step 16
   - a checkpoint never marks the PR Ready for review, never posts a review request (ADR-006) and leaves the story "In implementation"; in a conflict round (step 10) the PR stays Ready for review and a checkpoint makes no Draft conversion
   - turn budget: the dispatcher's limit is 60 turns (`AGENT_MAX_TURNS` default in `scripts/dispatcher-invoke.sh`; a turn is one tool-call round trip); a session that reaches about 40 turns without having finished must stop starting new work, push what it has, and hand off as below
   - stop with a pushed state: when the session stops before step 16's green, Ready-for-review path (turn budget reached, or any other early stop), push the work first, keep the PR Draft, and generate the handoff with `--next implementation` naming what is done and what is left, so a fresh session continues from the PR
   - cheap reads: read large files in slices (search for the relevant function first, then `sed -n '<from>,<to>p'`); never read a whole large file at once, such as `scripts/dispatcher-invoke.sh` (about 4,100 lines) or `tests/dispatcher_fakes.py`
15. Before requesting review, perform a self-review:
   - run `git diff main` (or the equivalent against the base branch) and verify the diff matches the issue scope exactly
   - run `scripts/ac-summary.sh --issue $ARGUMENTS --repo LauraMardones/headless-pr-workflow` and use the extracted checklist to verify AC/DoD coverage; exit `2` means no checklist was found and requires a manual issue-body fallback, not that coverage passed, while exit `1` is an invocation or lookup failure
   - if `scripts/ac-summary.sh` exits `1` because `gh` or GraphQL is unavailable, read the AC/DoD checklist from the issue body through the available GitHub transport (the same manual fallback as exit `2`) and continue; AC/DoD coverage must still be verified
   - do not copy the extracted AC/DoD checklist into the handoff comment
   - if any changed file is eligible under `scripts/dispatcher-change-check.sh` (a `scripts/dispatcher-*.sh` file, `scripts/slack-notify.sh`, or a `scripts/lib/*.sh` file the dispatcher sources), run `bash scripts/dispatcher-change-check.sh --files <changed-files...>`; fix every `FAIL` (exit `1`) before handoff, and treat exit `2` as an invocation or input blocker; name the result of each check id in the Session Summary `--checks`, for example `dispatcher-change-check: executable PASS, slack-guard SKIP, dry-run SKIP, stdout-json SKIP, dispatch-handled PASS, verified-write PASS, own-marker PASS, saved-state PASS` (ADR-014 → Decision → 6)
   - confirm the PR body contains `Closes #$ARGUMENTS` and a meaningful description
   - confirm the issue link in the PR references the correct issue number
   - confirm no command file writes a board status or an issue-body status line
   - confirm the PR title and body are accurate and not auto-generated boilerplate
   - run a failure-path pre-mortem on the diff: list every operation the diff adds or changes that is an external call (GitHub REST/GraphQL, `gh`, `git push`/`fetch`, any network call), a GitHub mutation (comment, label, status, review, thread resolution, board write, merge, branch), a persisted-state write (files used as state, Actions cache, artifacts, comments or markers read back later as state), or a GitHub Actions workflow step or condition that runs one of these (`if:`, `always()`, step order, concurrency); for each one, check these cases: (1) the operation fails; (2) it partly succeeds; (3) it is retried after an earlier attempt succeeded or partly succeeded; (4) the job or workflow is re-run; (5) the job or workflow is cancelled mid-way; (6) the state it reads is missing, stale or unreadable; (7) the PR head SHA changes between read and mutation
   - give each operation and case exactly one outcome: **handled** (the code handles it, with a deterministic test where a script-level test is possible); **not applicable** (one clause of reason, for example "read-only call"); or **residual risk** (not handled or not testable, for example workflow-level behaviour pytest cannot reach); a case with no outcome means the pre-mortem is incomplete, so fix the code or record the case before handoff; keep the per-case list in this session, not in the PR or any comment
   - record the pre-mortem in the Session Summary as `--deviation` flags: one `pre-mortem: <n> operations checked; residual risks: <count|none>` line, or `pre-mortem: not applicable — the diff adds no external call, GitHub mutation, persisted-state write or workflow step`; and one `residual risk: <operation> — <case> — <why it is accepted>` line for each residual risk
16. When implementation is complete:
   - run focused local tests before the final push, not the full suite (see `AGENTS.md` → Running Tests): the test files for changed modules or scripts and for directly affected callers, commands, or contracts, found by repository search when names do not match; a change to shared test infrastructure (`conftest.py`, `tests/dispatcher_fakes.py`, another shared fixture or helper) needs every test file that uses it or a representative subset
   - record the exact commands, results, selected coverage and reason for each selected file, and any omitted local coverage or unrun check with its reason, in the handoff `--checks` and `--deviation` fields
   - before the final push, on a first run and on a review-fix run, check whether the PR branch conflicts with the base branch (fetch `origin/main` with full history, then `git merge --no-commit --no-ff origin/main`, or read `mergeable` / `mergeable_state` from `gh api repos/LauraMardones/headless-pr-workflow/pulls/<pr>`; GitHub starts no `pull_request` CI run for a conflicting PR); if it conflicts, merge `origin/main` into the branch with a merge commit, resolve the conflict without dropping main's changes, rerun the focused tests and push the merge as an ordinary commit; never rebase and never force-push
   - if you cannot resolve the conflict, keep the PR Draft, and report `merge conflict with main on PR #<n>` as the blocker in the Session Summary `--blockers`, and generate the handoff with `--next implementation`
   - commit only the intended changes and push
   - confirm the current PR head SHA from GitHub
   - read the required `pytest` check for that exact head SHA; only a successful result for the current head counts, and a failed, pending, absent, unreadable, or older-head result never does
   - if the `pytest` check failed: keep the PR Draft (re-draft it if it is Ready for review), inspect the failures, fix the implementation or tests, rerun the affected local tests, push a new head, and wait for that head's check
   - while the check is pending, keep this session alive and poll the current head's `pytest` check (for example `gh pr checks <pr> --repo LauraMardones/headless-pr-workflow --watch`, or re-read it at intervals) until it completes; the check normally reports within minutes, so do not stop on the first pending read, and re-read the PR head SHA before each poll so a newer head restarts the wait
   - if the `pytest` check for the current head is absent, read the PR's mergeability (`mergeable` / `mergeable_state` through `gh api repos/LauraMardones/headless-pr-workflow/pulls/<pr>`) before reporting; when the PR conflicts with main, report `merge conflict with main` as the cause ("PR conflicts with main; GitHub starts no CI run for a conflicting PR"), not a guess at another cause such as the push token, and resolve it as described above
   - if the `pytest` check is still pending after waiting as long as the session allows, or is absent or unreadable: do not claim completion, keep the PR Draft (re-draft it if it is Ready for review), and report the state or blocker truthfully; a later continuation re-reads the PR head and check instead of trusting an earlier result
   - only when the current head's `pytest` check is green, continue with the steps below (Ready for review, Codex review request, completion handoff with `--next review`); until then do not use `--next review`
   - every other ending of this step generates its handoff with `--next implementation` and keeps the PR Draft: a merge conflict left unresolved, or a `pytest` check for the current head that failed, is still pending, is absent or is unreadable when the session ends; `--next review` is only for the green, Ready-for-review completion path
   - mark the PR Ready for review, or explicitly request review if Ready for review is unavailable
   - if the story carries an `executor:claude-code-*` label, request the Codex review (ADR-006): once the PR is Ready for review, post exactly one PR conversation comment whose first line is the Codex review trigger phrase (read it from ADR-006 → Decision or the constant `CODEX_REVIEW_TRIGGER_PHRASE` in `src/headless_pr_workflow/review_policy.py`)
   - post no review request on intermediate pushes, never post one on an issue, and post none for an `executor:codex` story
   - write the trigger phrase only as that PR conversation comment, never in an issue, a PR description, a committed file or any other comment, because it starts a Codex task wherever it appears
   - do not run `/review` for a Claude-implemented story: the `codex-review-bookkeeping` workflow posts the review Session Summary after Codex answers
   - generate the handoff with `scripts/session-summary.sh --command implement --issue $ARGUMENTS --pr <pr-number> --head <current-head-sha> --checks <checks> --blockers <blockers> --next review` and post its exact stdout
   - use repeatable `--deviation <text>` flags when deviations, residual risks, or decisions must be recorded; the step 15 `pre-mortem:` line is always required, and each residual risk gets its own `residual risk:` line
   - do not add any section after the generated Session Summary block; the PR body is the implementation document, so do not append “What was implemented”, “AC coverage”, or equivalent recap prose
17. If you are addressing review blockers on an existing PR:
   - treat any earlier approval as stale once a new commit is pushed
   - never leave the PR Ready for review while a new implementation commit is unverified: re-draft it until the new head SHA is ready for review
   - before changing code, classify every unresolved finding on the current head as **Blocker** or **Out of scope** against the linked story's `## Acceptance Criteria`, `## Scope`, `## Out of Scope` and the documented policy: a Blocker breaks or does not meet those, or is a defect the PR itself introduced; an Out of scope finding is a valid observation the story does not own (behaviour the PR did not add or change, an edge case outside the acceptance criteria, or an Epic/Feature requirement the story did not take on); when in doubt it is a Blocker
   - an Out of scope finding is one of two kinds:
     - **catch-all kind**: a finding about code the PR did not add or change (a defect or behaviour in a file or lines outside the PR's own change). It gets a catch-all issue, the reply lines in its thread, and its thread is resolved by `hpw resolve-catch-all-thread`. No `Type: decision` waiver blocker is declared for it
     - **waiver kind**: an edge case outside the story's acceptance criteria, or an Epic/Feature requirement the story did not take on. It follows the waiver path below: a reply without resolving the thread, a `Type: decision` blocker, the PO's waiver, and the PO resolves the thread
     - when in doubt whether an Out of scope finding is catch-all kind, it is waiver kind; when in doubt whether it is Out of scope at all, it is a Blocker; the classification is this session's judgement, and the helper does not recompute it from the diff
   - classifying is not a waiver: only the PO waives a finding; never resolve the thread of a waiver-kind finding, and resolve the thread of a catch-all-kind finding only with `hpw resolve-catch-all-thread`, never any other way
   - for each Out of scope finding, do not start a fix and make no commit for it: link an existing open catch-all issue that covers it (`hpw catch-all-issue link <n> --repo <owner/repo>`), or create one with `hpw catch-all-issue create --repo <owner/repo> --found-under <the story's Epic number> --origin <link to the finding> --title <text> --reason <text>` (`--kind bug` for a defect; where `gh` is unavailable the GitHub plugin creates it with the same lines and the Session Summary says the helper could not run); reply on the finding thread with the classification, the reason and the catch-all issue link, without resolving it
   - for a catch-all-kind finding, the reply instead starts with exactly these two lines, copied verbatim and never paraphrased, before any free text, with `<n>` the catch-all issue number:

     ```text
     Classification: Out of scope — code the PR did not add or change
     Catch-all issue: #<n>
     ```

     then run `hpw resolve-catch-all-thread <pr> --thread <thread-node-id> --issue <n> --head <current-head-sha> --repo LauraMardones/headless-pr-workflow` right after the reply; it resolves the thread only after fresh checks of the PR, its head, the thread, the reply lines and the catch-all issue. If the helper exits non-zero or cannot run (`gh` or GraphQL unavailable), the finding counts as waiver kind for this round: its thread stays unresolved, the waiver bullets below apply to it, and a `--deviation` line names the failed check
   - when the round leaves at least one waiver-kind Out of scope finding unwaived (counting a catch-all-kind finding whose helper call failed), declare one `Type: decision` blocker on the story (Question: waive the listed findings for this PR; Options: A) waive and track in the follow-up issues, B) bring them into this story's scope and fix them here; Recommendation: A); name the Out of scope findings and follow-up issues in a `--deviation` line of the Session Summary, not in `--blockers`
   - for each Blocker, fix the defect class, not only the reported line: name the class in one phrase (the general mistake, not the line, e.g. "phase marked done before its evidence comment is posted"); search the PR's own change for every other instance of that class: all files and hunks in `git diff <base>...HEAD`, plus the functions, scripts and command steps that the changed code calls or that call it, where the same pattern can recur; fix every instance that lies in code or text the PR added or changed, in the same round, before pushing; an instance found only in code the PR did not add or change is **Out of scope** under the rule above (follow-up issue, reply, no fix in this PR), not a reason to widen the PR; where a deterministic test can catch the class, the test added for the finding covers the other fixed instances too, where practical
   - the fix-round Session Summary records one `--deviation` line per Blocker finding in the form `Class check <finding link>: class "<class>"; checked <locations>; also fixed <locations | none>; out of scope <follow-up link | none>`; a finding with no other instance still gets the line, with `also fixed none`
   - fix Blockers as usual; after a round of fixes for Codex findings on an `executor:claude-code-*` story, post exactly one new review request (a PR conversation comment whose first line is the same trigger phrase), after the round's last push and after the PR is Ready for review again; when the round also leaves a waiver-kind Out of scope finding unwaived, post no review request after the push: a clean Codex result on the new head would mark the old-head thread superseded and let the story reach merge past the PO waiver, so the single review request waits for the post-waiver step below
   - catch-all-kind findings need no waiver: when every Out of scope finding of the round is catch-all kind and the helper confirmed each of its threads resolved, declare no waiver blocker and post the round's single review request after the round's last push (with no Blocker to fix, nothing is pushed and the request is for the current head); as everywhere, only for an `executor:claude-code-*` story, none for an `executor:codex` story; name each finding, its catch-all issue and its resolved thread in a `--deviation` line, and generate the handoff with `--next review`
   - when the round has only Out of scope findings and at least one of them is an unwaived waiver-kind finding, push nothing and post no review request; the story waits for the PO's decision, and the round's handoff generates with `--next implementation`
   - the waived thread is resolved by the PO, not by this session: the PO's waiver names the finding and its follow-up issue, and the PO resolves that thread on GitHub (a Codex thread on the current head is not superseded by a later review of the same head, so it stays unresolved until the PO resolves it)
   - after a PO waiver (a PR comment naming each waived finding and its follow-up issue) and `/unblock` on the story, there is nothing left to fix; for an `executor:claude-code-*` story post exactly one new review request for the current head, and for an `executor:codex` story post none (step 16 forbids it) because its paired Claude `/review` session reviews the head; if a waived thread is still unresolved, record in a `--deviation` line that the PO must resolve it before merge

Do not merge the PR.
Do not perform review approval.
Record workflow-relevant facts in GitHub, not only in chat.

---

## Required GitHub Output — Must Not Be Skipped

**Draft PR created early** using the available GitHub operation fallback (draft: true), linked to the issue.
**Handoff comment posted on the PR** at completion using the available GitHub operation fallback.

Generate the handoff with `scripts/session-summary.sh --command implement` and all required phase arguments, then post the exact stdout. Do not add prose or sections after the generated block.
