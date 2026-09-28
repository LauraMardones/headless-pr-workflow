# ADR-008: Epic Design Lives as a Spec in the Repository; Its Merge Is the Start Signal

**Status:** Accepted
**Date:** 2026-09-28
**Related:** ADR-009, ADR-010, ADR-011, Epic #160
**Effective when:** the follow-up stories that update the refine commands and the decision pre-check are merged. Until then, the current rules apply.

## Context

Product thinking for an Epic happens in a design phase, where the PO reasons through the Epic with an LLM (SpecKit-style). Today the result lands in the Epic issue body, which agents also rewrite during refinement: #160's body carries five agent "passes" in which PO decisions and agent bookkeeping are interleaved. Nothing shows which scope the PO approved, or when, and an issue edit is not an approval, so a separate signal (a board status or label) is needed to start work.

ADR-009 makes refinement non-product work. That rule is only checkable if the approved design is a fixed, versioned artifact.

## Decision

1. **Location.** An Epic's design is `specs/<epic-number>-<slug>/spec.md`, optionally with `plan.md`, merged to `main` through a PR. The design phase stops at spec and plan; breaking the Epic down is refinement (ADR-009), so no `tasks.md` is committed.
2. **Readiness.** A spec PR is merged only when the spec meets the Definition of Ready in `specs/README.md`, including Epic-level acceptance criteria and no open questions.
3. **Start signal.** Merging a spec PR is the only signal that starts work on an Epic. The approved scope is the spec at that merge commit.
4. **Scope changes.** Any change to scope is a new spec PR. Nothing else may change scope, including an Epic issue edit or an answer on a decision blocker.
5. **Approval.** Spec PRs are decision documents and are approved as ADR-011 describes.
6. **The Epic issue is an index.** It links to the spec and shows progress. The orchestrator creates and maintains it, and it is never the source of scope.

## Consequences

- Refinement, implementation and review read the spec at a known commit from the checkout, next to `docs/decisions/`.
- The refine commands and the #265 decision pre-check read the spec in addition to issue bodies.
- A delivered Epic's spec stays in `specs/` as a record. Whether to keep specs as living documentation or archive them is decided when the first Epic closes under this ADR.
- Revisit if spec PRs are routinely created for trivial clarifications, which would mean the line in ADR-009 between scope changes and clarifications is drawn in the wrong place.
