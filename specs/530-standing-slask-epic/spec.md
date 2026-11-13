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

- **E1:** <An outcome the PO can verify at Epic closure.>
  - **Verified by:** <How the PO checks it at closure, e.g. a usage scenario, a measurement, or a named test.>
- **E2:** <…>
  - **Verified by:** <…>

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

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
