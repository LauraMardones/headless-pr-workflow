# Epic #306: Tech Lead: from spec merge to ready stories without the PO

**Epic issue:** #306
**Milestone:** 6 — Autonomous Execution

## Goal

After the PO merges a spec PR, the Epic is broken into Features and stories, and stories become ready to implement, with no PO action apart from decision blockers. The Tech Lead's scheduler derives every next action in the chain, and the board status, from GitHub facts. The board no longer starts any work.

Today the PO starts every refinement by hand, the refine commands ask the PO product questions mid-session, and moving a board card is enough to start work. ADR-008 to ADR-010 decided the change on 2026-09-28, but apart from #258 and #283 no issue tracks it, so those ADRs are accepted and not in effect.

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

### Epics without a merged spec get no new refinement — 2026-10-02

**Chosen:** The Tech Lead refines nothing under an Epic that has no merged spec. This applies to the Epics created before ADR-008 (#173, #236, #272). Stories already refined under those Epics can still be implemented. To continue such an Epic, the PO runs `/spec <number>` for it.
**Rejected:** Refine old Epics from their issue body — the issue body would become a source of scope again, which ADR-008 rules out. Stop all work under old Epics, including refined stories — it would halt #236's refined stories until its spec is written.

### Epic closure against the spec is part of this Epic — 2026-10-02

**Chosen:** `/verify-closure` checks an Epic against its spec's Epic criteria (ADR-009), and that change is delivered by #306.
**Rejected:** Leave it to Epic #272 — #306 would deliver traced criteria that nothing checks at closure.

### A usage finding that affects scope or a criterion is a decision blocker — 2026-10-02

**Chosen:** When refinement meets a usage finding that affects scope or an acceptance criterion, it raises a decision blocker for the PO. An answer that changes scope goes into a spec PR. A finding that affects neither is noted and refinement continues.
**Rejected:** Refinement incorporates the finding itself — it would let refinement change what was specified, against ADR-009. Leave usage findings out of #306 — findings would reach no story unless the PO wrote a spec PR unprompted.

### Refinement can still be started by hand — 2026-10-02

**Chosen:** The `/refine*` commands stay invocable by hand, for example when the scheduler is paused. A manual run follows the same tech-lead rules and makes no product decisions.
**Rejected:** Only the Tech Lead starts refinement — the PO would have no fallback when the scheduler or session start is down.

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
