# Epic #530: Standing catch-all: maintenance and out-of-scope findings

**Epic issue:** #530
**Milestone:** None (standing Epic, never closes)

## Goal

Work found outside an Epic's scope needs a home that outlives every Epic. Epic #504 delivers the mechanism that files such work as catch-all issues; this Epic is where they land. It holds the catch-all Feature, lists the maintenance the Tech Lead refines there without asking the PO, and never closes. Until it exists, #504's F1 (#513) and F2 (#514) cannot start: every catch-all issue needs this Epic's and its catch-all Feature's numbers, and the sort needs the maintenance list. For the PO, technical debt that is maintenance is paid as it is found, ahead of other Epic work, and only a finding that needs a new criterion reaches the PO.

Spec #504 calls the catch-all "slask"; the two words name the same thing.

## Scope

In:

- The catch-all Feature: the one Feature under this Epic where catch-all issues land, with the lines and labels decided in spec #504 (2026-11-08). Its number and this Epic's number are constants in `src/headless_pr_workflow/catch_all.py`.
- The allowed-maintenance list: which kinds of catch-all finding the Tech Lead may refine without the PO, and where the line goes.
- The standing exception: the dispatcher's closure notifications and `/verify-closure` never act on this Epic or its catch-all Feature, read from the constants in `src/headless_pr_workflow/catch_all.py`. A second Feature under this Epic delivers it and closes as usual.
- This Epic's priority (P1) and that it has no milestone.
- The account of the automation (scope change, 2026-11-21, narrowed 2026-11-27): the dispatcher, board-status and the sessions the dispatcher starts run under their own GitHub App for everything except the Projects v2 board, so they spend fewer of the 5000 API calls per hour that `PROJECT_TOKEN` shares with the PO's sessions (issue #626). Every read and write of the board stays on `PROJECT_TOKEN`.

Out:

- The App reading or writing the Projects v2 board (scope change, 2026-11-27): the proof of 2026-11-26 and its re-run of 2026-11-27 show the App cannot see a user-owned board.
- The 5-minute dispatcher schedule.
- Reading less per run in the dispatcher and board-status: the Tech Lead does it in #626 without a spec.
- The edits to `docs/DISPATCHER-CONFIG.md` and ADR-012 that follow from the account decision: a separate PR (see Dependencies).
- The catch-all issue creation helper and the command wording (#516), the waiver and thread rules (#517), the sort before Feature breakdown (#514) and the closure list (#515).
- Changing how the scheduler orders work, or reserving ready-buffer slots for catch-all issues.
- Follow-ups that existed before #504's mechanism (spec #504, 2026-11-08).
- A general mechanism for more than one standing Epic.

## Acceptance Criteria

This Epic never closes, so its criteria are standing outcomes. Each is verified at a closure the PO confirms: #504's, or that of a Feature under this Epic that closes (the second Feature for E3, the Feature that delivers the account for E4).

- **E1:** A catch-all issue filed with the lines spec #504 decides is scheduled like any other issue. It is never skipped as `no-feature` or `no-parent`.
  - **Verified by:** #504's closure evidence: a catch-all issue under this Epic's catch-all Feature refined and implemented by the scheduler, and the scheduler log for it with no `no-feature` or `no-parent` skip.
- **E2:** A catch-all finding is refined without the PO only when it is allowed maintenance (M1–M10 under Decisions). Every other finding reaches the PO.
  - **Verified by:** #504's closure evidence (its E2 proof): one allowed finding refined with no PO action, and one finding that is not allowed, such as a refactoring on its own, raised as a decision blocker.
- **E3:** The PO never gets a closure request or a closure verification for this Epic or its catch-all Feature, even when the catch-all is empty.
  - **Verified by:** the second Feature's closure evidence: the catch-all Feature with every issue closed, no `feature_closure_confirmation` or `epic_closure_approval` notification for it or for this Epic, and `/verify-closure 530` refusing with a stated reason.
- **E4:** Everything except the board runs under the App's token, and dispatcher and board-status runs fail less often on the shared API rate limit than before the switch.
  - **Verified by:** the closure evidence of the Feature that delivers the account: the dispatcher and board-status runs over seven days after the switch, with the share failed on `could not read the work in flight` or `API rate limit exceeded` lower than the baseline of 11 failed runs in 40 (#626, 2026-11-21); a session the dispatcher started that pushed with the App's token; and a `scripts/merge-gate-summary` run under the App that reports its checks, not `required-unavailable`.

## Decisions

### The board stays on `PROJECT_TOKEN`, the App takes the rest — 2026-11-27

Scope change. Replaces the board part of "Own account for the automation: a GitHub App — 2026-11-21" (its board write, and its App permission "board (project) write"). The rest of that entry stands. That entry said that if the App cannot write the board, the PO decides again by a scope change. This is that decision.

Evidence: the App token proof (#664, run 45903090636, 2026-11-26) printed `board-read: refused` and `board-write: refused` with `no Projects v2 board is visible on LauraMardones/headless-pr-workflow`, and `branch-protection: ok` and `review-requests: ok`. On 2026-11-27 the PO accepted the App's new Projects permission, confirmed the board is linked to the repository, and ran the proof again: the same result.

**Chosen:**
- Every read and write of the Projects v2 board runs with `PROJECT_TOKEN`: board-status, and the dispatcher's reads of the work in flight and its board writes.
- Everything else runs under the App, as decided on 2026-11-21: the sessions the dispatcher starts (git pushes included), the merge gate's reads (branch protection, review requests) and the dispatcher's other GitHub calls.
- The saving is smaller than the 2026-11-21 decision expected: the board reads still spend `PROJECT_TOKEN`'s calls. E4 is reworded to measure a lower failure share, not none.

**Rejected:**
- Fix the App's access and re-run the proof — done twice, same result.
- Move the project to an organization — not chosen. It stays a possible later scope change.
- A bot account with its own token — rejected on 2026-11-21, not reopened.
- Keep E4 as "no failed run in a week" — it may not hold while the board reads share the limit with the PO's sessions.
- Remove E4 — the account change would have no measure of use.

### Own account for the automation: a GitHub App — 2026-11-21

Scope change. Reconfirms and extends the PO's answer on #626 (option b, `/unblock`, 2026-11-21). No earlier decision is reversed.

**Chosen:**
- The dispatcher, board-status and the sessions the dispatcher starts run under their own GitHub App, not `PROJECT_TOKEN`. `PROJECT_TOKEN` stays with the PO's own sessions. Reason: they share its 5000 calls per hour, and 11 of the last 40 dispatcher runs failed on the limit (#626, 2026-11-21).
- Sessions the dispatcher starts use the App's token for everything they do, git pushes included.
- The App's permissions: repository access (what the automation does today), board (project) write, and administration read, so the merge gate can read branch protection. The gate is not weakened: it still reports `checks=fail(required-unavailable)` when the read fails.
- Board write is not proven for a user-owned project. The Tech Lead proves it with a test call. If the App cannot write the board, this decision is not worked around: the PO decides again by a new scope change.
- `docs/DISPATCHER-CONFIG.md` and ADR-012 change to match, in a separate PR the PO merges (see Dependencies). The spec PR cannot carry them: a spec PR changes only the spec folder, and ADR-011 item 1.

**Rejected:**
- Keep `PROJECT_TOKEN` and only read less (#626 option a) — the limit stays shared with the PO's sessions, so a failure is rarer but still possible. Reading less still happens, as a Tech Lead task in #626 that needs no spec.
- A bot account (a second user with its own token) — #626 option b named it next to the App; the PO chose the App.
- The App for everything except board writes, which stay on `PROJECT_TOKEN` — board-status also failed on the limit, so it is a weaker fix.
- Moving the project to an organization now — not chosen. It is a possible later decision if the board-write proof fails.
- Changing the 5-minute dispatcher schedule — intentional (#626), not part of this decision.

### The standing exception is the constants in the catch-all module — 2026-11-13

**Chosen:** The dispatcher's closure notifications and `/verify-closure` skip this Epic and its catch-all Feature by the constants in `src/headless_pr_workflow/catch_all.py` that #516 and #517 also use. A second Feature under this Epic delivers the exception and closes as usual.
**Rejected:** A `standing` label on the Epic and the Feature — general enough for more standing Epics, but there is one. A list in the dispatcher configuration — it changes without a PR and can drift from the constants. A scope change to #504 so that #515 delivers it — #515 is the closure list, a different change.

### Priority P1 — 2026-11-13

**Chosen:** `prio:P1`. Maintenance is paid as it is found. The scheduler ranks candidates by Epic priority first, so catch-all work goes ahead of every P2 Epic. Among P1 Epics it loses ties to those whose specs merged earlier (#160, #306, #518).
**Rejected:** P2 — this spec merges after #418, #504 and #519, so catch-all work would come last among P2. P3 — catch-all work would run only when no other Epic has work. A reserved share of the ready buffer — a scheduler change, and catch-all issues taking buffer slots is what pays the debt at once.

### No milestone — 2026-11-13

**Chosen:** No milestone.
**Rejected:** M6 Autonomous Execution — a standing Epic would keep M6 open for good. A dedicated standing milestone — a milestone that never closes, for one Epic.

### English term "catch-all" replaces "slask" — 2026-11-13

**Chosen:** "catch-all": the catch-all Epic, the catch-all Feature, a catch-all issue, and the module `src/headless_pr_workflow/catch_all.py`. Spec #504 keeps "slask" as merged, and this spec maps the two words once. The Tech Lead renames the titles and bodies of #504 and #513 to #517 when refining them. The folder and branch slug keep `slask`, since a slug does not change once pushed.
**Rejected:** Keeping "slask" — a Swedish word in an English repository. "inbox" — work is refined and implemented there, not only held. "triage" — collides with the sort step (#514). "backlog" — collides with `PO status: Backlog`. "KTLO" or "sustaining" — jargon, and it does not cover findings that wait for the PO.

### Allowed maintenance and where the line goes — 2026-11-13

**Chosen:** A catch-all finding is allowed maintenance only if the fix changes no behaviour that a merged spec, an accepted ADR or a `docs/*.md` rule describes, or restores behaviour one of them already describes. A change to a command's output, exit codes, flags or contract counts as behaviour. Anything else reaches the PO as spec #504 describes. Applied to the kinds of finding expected:

- **M1** A bug that breaks an acceptance criterion of a closed Epic's merged spec — allowed (spec #504, 2026-11-08).
- **M2** A bug that breaks a rule in an accepted ADR or a `docs/*.md` rule that no open Epic owns — allowed.
- **M3** Flaky or broken tests, and test infrastructure — allowed.
- **M4** Documentation that contradicts an accepted ADR, a merged spec, or code that follows them — allowed; the documentation is corrected.
- **M5** Documentation that contradicts code when no ADR or spec settles which is right — to the PO.
- **M6** Patch and minor updates of dependencies and GitHub Actions — allowed.
- **M7** Major updates and new dependencies — to the PO.
- **M8** Refactoring — allowed only as part of the fix of an allowed finding that needs it; a refactoring on its own goes to the PO.
- **M9** Any change to `docs/MERGE-POLICY.md`, `docs/required-check-policy.json`, the approval gate, an ADR or a spec — never allowed, whatever M1–M8 say.
- **M10** A security fix that needs a major update — to the PO, as M7.

**Rejected:**
- A list of kinds without the general line — every kind not listed would need interpretation in refinement.
- M8: every refactoring allowed — at P1 it has no natural bound, "no behaviour change" cannot be checked before the work is done, and a broad refactoring holds back other stories through the scheduler's file-overlap rule. Every refactoring to the PO — a refactoring that an allowed fix needs would block that fix.
- M10: a security exception to M7 — catch-all work still queues behind the P1 Epics, so it saves little time, a major update is the change most likely to break CI, and the agent itself would judge what counts as a security fix. Fixes inside the pinned major version (`@v4`) already arrive with the moving tag.

### No periodic maintenance round for now — 2026-11-13

**Chosen:** No periodic or counted trigger for maintenance work. Refactorings and other findings that are not allowed maintenance reach the PO as spec #504 describes; the PO batches them by leaving the decision blockers unanswered until it suits, or by taking them at Epic closure (#504 E3), and starts a maintenance Epic with `/spec` when the list grows. An unanswered decision blocker is not refined and takes no ready-buffer slot.
**Rejected:** A maintenance round every fifth Epic or every third month, whichever comes first — a counted or timed trigger needs scheduler logic and stored state (ADR-010), and agents have no context-switch cost that batching the work would save. Letting refactorings wait without a decision blocker — changes spec #504's rule that every other finding reaches the PO as a decision blocker.
**Revisit:** if unanswered refactoring decision blockers grow many, or often stay open longer than one Epic, consider a periodic maintenance round as a scope change.

## Checked Against

- `specs/README.md` → Definition of Ready ("an outcome the PO can verify at Epic closure") and ADR-008 item 2 — conflict in wording: this Epic never closes. Resolved: each criterion is verified at a closure the PO confirms, #504's or the second Feature's. No change to `specs/README.md`.
- ADR-008 items 3, 5 and 7 — no conflict. The Epic issue was created before the spec file, scope changes only by spec PR, and the Epic issue is an index.
- ADR-009 item 3 (criteria traced to their parent) — no conflict. The catch-all Feature's criteria serve E1 and E2, the second Feature's serve E3. Catch-all stories trace to the catch-all Feature's criteria, bounded by M1–M10.
- ADR-009 item 4 (refinement makes no product decision) — no conflict. The line under Decisions makes the classification mechanical, and every finding outside it reaches the PO.
- ADR-009 item 7 (re-refinement after a scope change) — no conflict. Consequence: each scope change to this spec makes every refined catch-all issue stale, and the Tech Lead re-refines them.
- ADR-010 (status from facts, deterministic scheduling) — no conflict. P1 uses the scheduler's existing ordering, and no periodic trigger with stored state is added.
- ADR-011 (decision documents approved by the PO's merge) — no conflict. The PO merges this spec, and M9 keeps decision documents out of the catch-all.
- Spec #504 decisions of 2026-11-08 and 2026-11-09 — no conflict. M1 restates the closed-Epic bug decision, the allowed-maintenance list is written here as #504 decided, and "slask" is mapped to "catch-all".
- `.claude/commands/verify-closure.md` (evaluates any Epic or Feature it is given) — conflict. Resolved: the second Feature makes it refuse this Epic and its catch-all Feature.
- `scripts/dispatcher-invoke.sh` (`feature_closure_confirmation` and `epic_closure_approval` when every story or Feature is Done) — conflict. Resolved: the second Feature exempts this Epic and its catch-all Feature.
- `src/headless_pr_workflow/dispatcher/facts.py` and `scheduler.py` (`no-parent`, `no-feature`, Epic priority first, one ready buffer across Epics) — no conflict. Catch-all issues carry `Parent epic:` and `Feature group:`, and P1 follows the existing sort key.
- ADR-012 (Consequences: the session "holds `PROJECT_TOKEN`… until a narrower credential exists"), `docs/DISPATCHER-CONFIG.md` (GitHub Token — `PROJECT_TOKEN`; token of each step in a session; board status), ADR-013 and ADR-014 (the transport uses the token `gh` already uses, `PROJECT_TOKEN` in the workflow) — conflict. Resolved: the account decision (2026-11-21) supersedes that rule. A separate PR the PO merges changes ADR-012 and `docs/DISPATCHER-CONFIG.md`, and ADR-014's wording if needed (see Dependencies). Until it merges, the documents still name `PROJECT_TOKEN`.
- ADR-011 item 1 and the Definition of Ready — conflict with the PO's wish to change `docs/DISPATCHER-CONFIG.md` and ADR-012 in this spec PR. Resolved: a spec PR changes only the spec folder, so those changes go in a separate PR.
- ADR-011 item 5 — no conflict. Merges by the App's account are visible as a different account from the PO's, which makes the PO's own merge easier to tell apart. No change decided.
- `docs/MERGE-POLICY.md` and M9 — no conflict. The App's administration read keeps the merge gate whole; no gate is changed.
- #626 (`## Decisions`, 2026-11-21) — consistent. It records option b and keeps #626 blocked until this spec merges. Reading less (its ideas 2 and 3) stays a Tech Lead task.
- ADR-009 item 7 — no new consequence beyond the one already stated: #626 and any refined issue it affects are re-refined after this spec merges.
- Scope change of 2026-11-27: `docs/DISPATCHER-CONFIG.md`, ADR-012, ADR-014 and `.github/workflows/dispatcher.yml`, `board-status.yml` — conflict with the 2026-11-21 plan that board-status moves to the App. Resolved: the board stays on `PROJECT_TOKEN`; the decision-document PR names `PROJECT_TOKEN` for the board and the App for the rest. No ADR is superseded by this spec PR.
- #633 and its stories #664 and #665 (`Parent epic: #530`) — conflict. #665 ("The board writers run with the App's installation token") no longer matches the scope, and F3.2, F3.6 and F3.9 are narrowed. Resolved: the Tech Lead re-refines #633 and re-scopes or closes #665 after this spec merges (ADR-009 item 7).
- ADR-011 item 1 — no conflict. This PR changes only the spec folder.
- `docs/ROADMAP.md` milestones and `scripts/refine-check` — no conflict. `scripts/refine-check` does not check a milestone.

## Delegated to the Tech Lead

- Slicing the catch-all Feature and the second Feature, their Feature criteria, titles and criterion prefixes, and the order of their stories.
- Classifying each catch-all finding against M1–M10 by the line under Decisions.
- The `prio:` and `executor:` labels of each catch-all issue; `prio:` orders only among catch-all issues.
- How the dispatcher and `/verify-closure` read the constants, and the wording of the refusal.
- Renaming the titles and bodies of #504 and #513 to #517, and the module name `catch_all.py` in #516 and #517 (decided above).
- The account change: slicing it into a Feature (it closes, so E4 can be verified) and its stories, the App's exact permission names (including whether its Projects permission stays), how a job holds both tokens (the App's for everything but the board, `PROJECT_TOKEN` for the board), re-scoping or closing #665, and re-refining #626 and #633.

## Dependencies

- #504 — E1 and E2 are verified through its closure evidence. Order: this spec merges, the Tech Lead creates the catch-all Feature, #504's F1 (#513) and F2 (#514) can start, #504 closes, E1 and E2 are verified. A chain, not a cycle.
- #516 and #517 need this Epic's number (#530) and the catch-all Feature's number as constants in `src/headless_pr_workflow/catch_all.py`. The Feature's number exists only after the Epic refinement.
- The documents that follow from the account decision (2026-11-21): a separate PR changes `docs/DISPATCHER-CONFIG.md` and ADR-012 (and ADR-014's wording if needed). It is a decision-document PR that the PO merges (ADR-011 item 5). #626 stays blocked until this spec and that PR merge.
- The PO creates the GitHub App and stores its credential as a repository secret. An agent never holds or prints it.

## Open Questions

None.
