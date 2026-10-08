# Epic #306: Tech Lead: from spec merge to ready stories without the PO

**Epic issue:** #306
**Milestone:** 6 — Autonomous Execution

## Goal

After the PO merges a spec PR, the Epic is broken into Features and stories, and stories become ready to implement, with no PO action apart from decision blockers. The Tech Lead's scheduler derives every next action in the chain, and the board status, from GitHub facts. The board no longer starts any work.

Today the PO starts every refinement by hand, the refine commands ask the PO product questions mid-session, and moving a board card is enough to start work. ADR-008 to ADR-010 decided the change on 2026-09-28, but apart from #258 and #283 no issue tracks it, so those ADRs are accepted and not in effect.

## Scope

In:

- Start signal: the Tech Lead notices a merged spec PR, for a new spec or a scope change, and starts the Epic-to-Feature breakdown with no PO action.
- Rewritten `/refine*` commands: a tech-lead role that reads the spec at a known commit, makes no product decisions, raises a decision blocker for a specification defect instead of asking the PO, records the spec commit it refined against, and moves no board card.
- Traced acceptance criteria: Feature criteria name the Epic criteria they serve, and story criteria name their Feature criteria. A script checks that every Epic criterion is covered and that no criterion lacks a parent.
- Just-in-time story refinement: the Tech Lead keeps 3–5 stories ready to implement and refines no further ahead.
- Re-refinement after a scope change: affected Features and stories are re-refined before any of them is implemented further.
- The scheduler: a script, with no model, chooses the next action from hard dependencies, priority, queue limits, the WIP limit of two and file overlap. It replaces the board trigger in the dispatcher, including for starting implementation.
- The board shows facts: only the scheduler writes status, it overwrites manual moves, and the intent statuses are removed or renamed. `/implement`, `/review`, `/merge` and `/cleanup` stop moving cards.
- The Epic issue as index: the Tech Lead keeps it linked to the spec and showing progress.
- No refinement under an Epic with no merged spec. Stories already refined under such an Epic can still be implemented.
- A usage finding that affects scope or an acceptance criterion becomes a decision blocker. Any other finding is noted.
- `/verify-closure` checks an Epic against its spec's Epic criteria.
- The `/refine*` commands stay invocable by hand, under the same rules.
- `docs/PROJECT-STATUS.md` (fact and intent, transitions, recovery) and `docs/REFINEMENT-PIPELINE.md` (pipeline shape, escalation triggers) are brought in line.
- #258 and #283 are re-refined against ADR-010.
- The "ready for refinement" Slack notification is retired.
- Refinement sessions respect the usage limit, the reserve and the pause switch from #160.
- An end-to-end proof from a spec merge to ready stories.

Out:

- The cycle from a ready story to cleanup, which is #160. Only its trigger and its status display change here.
- How sessions are started (#289 in #160).
- The `/spec` command and the specification phase itself (#302 under #173).
- Writing the specs for #173, #236 and #272.
- Product decisions in refinement, and removing decision blockers as a human touchpoint.
- Changing the values of the ready buffer (3–5) or the WIP limit (two).
- A dashboard that replaces the board.
- Pre-delivery intake (#236).

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
