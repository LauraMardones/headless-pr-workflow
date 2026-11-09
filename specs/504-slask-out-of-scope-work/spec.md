# Epic #504: Slask: work found outside an Epic's scope

**Epic issue:** #504
**Milestone:** M6 Autonomous Execution

## Goal

Work that an implementation or review session finds outside its Epic's scope gets one place to land: the catch-all ("slask") Feature under the standing slask Epic. Before each Feature breakdown, the Tech Lead sorts what has landed there. A finding that fits an existing Feature criterion moves to that Feature. Maintenance that the slask spec allows is refined where it is. A finding that needs a new criterion reaches the PO as a decision blocker. What fits nowhere is listed to the PO when the Epic it came from closes, and the PO chooses a new spec or closing it.

Today each out-of-scope finding becomes a follow-up issue under its own Epic with no Feature, or under no Epic at all. The scheduler skips both (`no-feature`, `no-parent`). An open follow-up under an Epic also keeps `/verify-closure` from closing that Epic. So findings are either lost or block closure. Spec #306 left this to a separate Epic (2026-11-07), and the just-in-time Feature breakdown it waited for (#489) is now delivered.

## Scope

In:

- `/implement`, `/review` and `/refine` create a slask issue, or link an existing one, for work found outside their scope, with the lines and labels decided below.
- `/review` stops holding a PR for a finding in code the PR did not change. Other out-of-scope findings still wait for the PO's waiver.
- Before each Feature breakdown the Tech Lead sorts the slask: a finding that fits an existing Feature criterion moves there. Allowed maintenance is refined where it is. A bug that breaks a criterion of a closed Epic's spec is refined without the PO. Anything else becomes a decision blocker.
- `/verify-closure` lists to the PO the slask issues found under the Epic it closes. The PO chooses a new spec or closing each one.
- The scheduler no longer skips slask issues as `no-feature` or `no-parent`.

Out:

- The standing slask Epic, its spec and the list of allowed maintenance (own `/spec` session).
- Sorting follow-ups that existed before the mechanism.
- #399 and #400.
- Changing how the scheduler orders work.

## Acceptance Criteria

- **E1:** Work found outside a session's scope lands as a slask issue carrying `Found under:` and `Origin:`. A PR is not held for a finding in code the PR did not change.
  - **Verified by:** a proof PR whose review finds a defect in code it did not change: the PR merges without a PO waiver, and the slask issue links back to that finding.
- **E2:** Before each Feature breakdown, every slask issue is sorted. One that fits a Feature criterion is moved to that Feature. Allowed maintenance, and a bug that breaks a criterion of a closed Epic's spec, are refined without the PO. Anything else reaches the PO as a decision blocker.
  - **Verified by:** closure evidence showing one slask issue of each kind and what happened to it, with no PO action except the decision blocker.
- **E3:** An out-of-scope finding no longer stops its Epic from closing, and nothing found is lost: when an Epic closes, the PO gets a list of the open slask issues found under it and chooses a new spec or closing each one.
  - **Verified by:** the first Epic to close after this mechanism is in place closes while a slask issue found under it is still open, and its closure evidence lists that issue.

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

### The allowed maintenance is listed in the standing slask Epic's spec — 2026-11-08

**Chosen:** The kinds of maintenance the Tech Lead may refine without asking the PO, and where the line goes, are decided in the standing slask Epic's spec. A finding that is no such maintenance and fits no existing Feature criterion goes to the PO as this spec describes.
**Rejected:** List the maintenance in this spec — the list belongs with the Epic that holds the work, and the PO changes it there with a scope-change spec PR.

### A bug that breaks a criterion of a closed Epic is fixed without the PO — 2026-11-08

**Chosen:** A bug in delivered work whose Epic is closed is refined in the slask without asking the PO when it breaks an acceptance criterion of that Epic's merged spec: the fix restores behaviour the PO already approved. A bug that needs new behaviour reaches the PO at once as a decision blocker, because no closure of its Epic will list it.
**Rejected:** Every such bug goes to the PO — the PO would approve again what the spec already says. Reopen the closed Epic — it undoes a closure the PO confirmed.

### Only new findings; existing follow-ups are not sorted — 2026-11-08

**Chosen:** The mechanism handles findings made after it is in place. Follow-up issues that already exist without a Feature or an Epic are not sorted or listed by this Epic; the PO places them by hand, as today. On 2026-11-08 the PO placed #507 under #160, #508 under #306, kept #367 and #369 under #418, and left #399 and #400 for a later Epic of their own.
**Rejected:** A one-time sort of every open follow-up without a Feature or an Epic when the mechanism is delivered — it adds a criterion for a handful of issues the PO can place in one sitting.

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
