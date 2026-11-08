# Epic #504: Slask: work found outside an Epic's scope

**Epic issue:** #504
**Milestone:** M6 Autonomous Execution

## Goal

Work that an implementation or review session finds outside its Epic's scope gets one place to land: the catch-all ("slask") Feature under the standing slask Epic. Before each Feature breakdown, the Tech Lead sorts what has landed there. A finding that fits an existing Feature criterion moves to that Feature. Maintenance that the slask spec allows is refined where it is. A finding that needs a new criterion reaches the PO as a decision blocker. What fits nowhere is listed to the PO when the Epic it came from closes, and the PO chooses a new spec or closing it.

Today each out-of-scope finding becomes a follow-up issue under its own Epic with no Feature, or under no Epic at all. The scheduler skips both (`no-feature`, `no-parent`). An open follow-up under an Epic also keeps `/verify-closure` from closing that Epic. So findings are either lost or block closure. Spec #306 left this to a separate Epic (2026-11-07), and the just-in-time Feature breakdown it waited for (#489) is now delivered.

## Scope

In:

- <…>

Out:

- <…>

## Acceptance Criteria

- **E1:** <An outcome the PO can verify at Epic closure.>
  - **Verified by:** <How the PO checks it at closure, e.g. a usage scenario, a measurement, or a named test.>
- **E2:** <…>
  - **Verified by:** <…>

## Decisions

### Two Epics: the mechanism and the standing slask Epic — 2026-11-08

**Chosen:** This Epic delivers the mechanism and closes as usual. The standing slask Epic is a separate Epic with its own spec, written in its own `/spec` session. It holds the slask Feature, lists the maintenance the Tech Lead may refine without asking the PO, and never closes.
**Rejected:** One Epic that is both the mechanism and the container — the mechanism would never be verified at an Epic closure, and this spec would need an exception from the Definition of Ready.

### The session that finds the work creates the slask issue — 2026-11-08

**Chosen:** `/implement`, `/review` and `/refine` create the issue when they find work outside the scope of what they work on, or link an existing open issue that covers it. The PO may add one by hand. The issue carries `Parent epic:` (the slask Epic), `Feature group:` (the slask Feature), `Found under: #<epic>` (the Epic where the work was found), `Origin:` (a link to the finding) and the label `type:bug` for a defect, otherwise `type:story`. It has no `prio:` label, no `executor:` label and no `PO status:` line; refinement sets what it needs.
**Rejected:** Keep the follow-up under the Epic where it was found — the scheduler skips it (`no-feature`) and it keeps that Epic from closing. Derive the Epic of origin from the `Origin:` link through the PR and the story — a chain of reads that breaks when one link is missing; a line in the issue is a fact that can be read directly.

### Only a finding in code the PR did not change passes without a PO waiver — 2026-11-08

**Chosen:** An out-of-scope finding about behaviour the PR did not add or change goes to the slask and no longer holds the PR. An edge case outside the story's criteria, and an Epic or Feature requirement the story did not take on, still wait for the PO's waiver, as today.
**Rejected:** Every finding waits for the waiver, as today — the PO decides on defects that are not the story's. No finding waits — the PO would no longer see gaps in the story's own delivery before it merges.

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
