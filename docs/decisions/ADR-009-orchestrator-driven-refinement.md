# ADR-009: Refinement Is Orchestrator Work and Makes No Product Decisions

**Status:** Proposed
**Date:** 2026-09-28
**Related:** ADR-008, ADR-010, Epic #160, `docs/REFINEMENT-PIPELINE.md`
**Amends:** Epic #160 decision "Refinement execution model" (2026-06-03), its non-goal "Headless automatic Epic or Feature refinement", and the Epic/Feature refinement touchpoint

## Context

Today the dispatcher notifies the PO when an item reaches "Ready for refinement", and the PO runs `/refine` by hand. #160 rejected headless refinement because refinement raises open questions that need a pause, notify and resume protocol. That protocol now exists: decision blockers (`docs/PROJECT-STATUS.md`), `/unblock` resume (#262), the documented-decision pre-check (#265) and decidable blocker content (#296).

With product design moved into the spec (ADR-008), what remains of refinement is tech-lead and scrum-master work: labels, milestone, dependencies, a check against existing decisions, and breaking the Epic into Features and implementation issues. It no longer needs the PO at the keyboard.

## Decision

1. **Who.** The orchestrator starts every refinement after a spec merges (ADR-008), with no PO action. This covers Epic to Features, and Features to Stories, Tasks and Bugs.
2. **What refinement may do.** It sets labels, milestone and dependencies, runs the decision pre-check, slices the work and writes acceptance criteria. Every acceptance criterion must trace to the merged spec.
3. **What refinement may not do.** It does not make product decisions. A gap, a contradiction or a conflict with an existing decision is a design defect. Refinement declares a decision blocker for it and does not fill the gap itself.
4. **Answering blockers.** A PO answer that changes scope goes into a spec PR (ADR-008). An answer that clarifies without changing scope is recorded under the issue's `## Decisions`, as today.
5. **Timing.** Epic-to-Feature breakdown happens at once. Feature-to-Story breakdown is just-in-time against the "Ready for implementation" buffer of 3–5 in `docs/REFINEMENT-PIPELINE.md`, so that usage findings from the Product Validator can still reach later stories.

## Consequences

- The PO's human touchpoints become: design (spec PR), decision blockers, Feature closure confirmation, Epic closure approval, and Red flow-health alerts. Epic/Feature refinement leaves the touchpoint table in #160.
- The `/refine*` commands are rewritten from a "senior product owner" role to a tech-lead role, with the traceability rule and the decision-blocker rule above.
- Usage findings that change product direction amend the spec. They do not edit stories directly.
- Refinement sessions start by the same mechanism as implementation sessions, so #289's scope widens to cover both.
- Revisit if decision blockers from refinement become frequent. That would mean specs leave too much open, and the fix belongs in the design phase, not in refinement.
