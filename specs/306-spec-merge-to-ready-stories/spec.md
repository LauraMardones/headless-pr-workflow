# Epic #306: Tech Lead: from spec merge to ready stories without the PO

**Epic issue:** #306
**Milestone:** 6 — Autonomous Execution

## Goal

<What changes, for whom, and why now. A few sentences.>

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

### The whole scheduler belongs to this Epic — 2026-10-01

**Chosen:** #306 delivers the complete ADR-010 scheduler. It replaces the board as trigger for the entire chain, including starting implementation and showing the review, merge and done states. This matches the #160 spec, which keeps the board trigger only until #306 replaces it.
**Rejected:** Only up to "ready to implement" — implementation would keep the board trigger, the rest of ADR-010 would have no home, and the #160 spec would need a correction.

### Milestone — 2026-10-01

**Chosen:** Milestone 6 — Autonomous Execution, the milestone of #160, which this Epic was split from.
**Rejected:** M7 Pre-Delivery Intake — it is about what happens before delivery items exist. A new milestone — not needed.

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
