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

Out:

- The catch-all issue creation helper and the command wording (#516), the waiver and thread rules (#517), the sort before Feature breakdown (#514) and the closure list (#515).
- Changing how the scheduler orders work, or reserving ready-buffer slots for catch-all issues.
- Follow-ups that existed before #504's mechanism (spec #504, 2026-11-08).
- A general mechanism for more than one standing Epic.

## Acceptance Criteria

This Epic never closes, so its criteria are standing outcomes. Each is verified at a closure the PO confirms: #504's, or that of the second Feature under this Epic.

- **E1:** A catch-all issue filed with the lines spec #504 decides is scheduled like any other issue. It is never skipped as `no-feature` or `no-parent`.
  - **Verified by:** #504's closure evidence: a catch-all issue under this Epic's catch-all Feature refined and implemented by the scheduler, and the scheduler log for it with no `no-feature` or `no-parent` skip.
- **E2:** A catch-all finding is refined without the PO only when it is allowed maintenance (M1–M10 under Decisions). Every other finding reaches the PO.
  - **Verified by:** #504's closure evidence (its E2 proof): one allowed finding refined with no PO action, and one finding that is not allowed, such as a refactoring on its own, raised as a decision blocker.
- **E3:** The PO never gets a closure request or a closure verification for this Epic or its catch-all Feature, even when the catch-all is empty.
  - **Verified by:** the second Feature's closure evidence: the catch-all Feature with every issue closed, no `feature_closure_confirmation` or `epic_closure_approval` notification for it or for this Epic, and `/verify-closure 530` refusing with a stated reason.

## Decisions

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

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
