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

### <Decision title>

**Chosen:** <…>
**Rejected:** <alternative> — <why>

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
