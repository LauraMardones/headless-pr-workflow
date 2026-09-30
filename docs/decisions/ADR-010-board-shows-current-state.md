# ADR-010: The Project Board Shows Current State; It Never Triggers Work

**Status:** Accepted
**Date:** 2026-09-28
**Related:** ADR-008, ADR-009, ADR-002, `docs/PROJECT-STATUS.md`
**Amends:** ADR-002's dispatch trigger ("polls the board … acts on items in Ready for refinement or Ready for implementation"); the Fact vs Intent model in `docs/PROJECT-STATUS.md`
**Effective when:** the Tech Lead derives next actions from facts and is the board's only writer. Until then, the current dispatcher and status rules apply.

## Context

`docs/PROJECT-STATUS.md` defines "Ready for refinement", "Ready for implementation" and "Ready to merge" as intent signals: a board status authorizes the next action. The board has several writers: the PO, the commands, stale recovery and the dispatcher. One manual drag is enough to start work. Most of the August bug wave comes from this:

- #256: the dispatcher crashes on a non-story item in "Ready for implementation".
- #258: Epics and Features sit in story-only statuses.
- Stale recovery rolled Epic #160 back to "Ready for implementation" three times.
- #257: notifications repeat because state is not remembered between runs.

Under ADR-009 the Tech Lead decides what happens next, so authorization must come from somewhere other than the board.

## Decision

1. **The board shows facts.** Every status describes the current state of the work. Intent-signal statuses are removed or renamed to describe state. From the board the PO can read which implementation issues are at which stage, and how much remains of each Feature and Epic.
2. **The board never triggers work.** Next actions are derived from GitHub facts, never from board status. Examples:
   - In specification: an open Epic issue whose spec folder is not yet on `main`.
   - Refined: a refinement record exists with no open decision blocker.
   - Ready to implement: refined, and every hard dependency is closed.
   - Mergeable: the merge gates in `docs/MERGE-POLICY.md` and ADR-007 pass on the current head.
   - Blocked: an open Blocked Declaration with no resolution.
3. **One writer.** Only the Tech Lead's scheduler writes board status. On each run it rewrites the status from facts and overwrites any drift, including manual moves.
4. **Scheduling is deterministic.** The choice of the next action uses hard dependencies, priority, per-stage queue limits (the refinement buffer, and WIP 2 for implementation), and file overlap. It is computed by a script, not a model. Models run only inside the sessions the Tech Lead starts.

## Consequences

- The `.claude/commands/*.md` contracts stop moving board cards as part of their work. They record facts (comments, PRs, labels) instead.
- `docs/PROJECT-STATUS.md` rewrites its Fact vs Intent section, its transition table, and the Recovery Protocol, which today rolls status back.
- `scripts/project-status-sync.sh` (prototype #152), which already derives status from repository facts, is the starting point.
- #258 and #283 assume the board is a trigger and are re-refined against this ADR before implementation.
- Queue limits and ordering rules are working rules. They belong in `docs/PROJECT-STATUS.md`, not in this ADR.
- Because the board only displays facts, a dedicated dashboard can replace it later without changing any workflow rule.
- Revisit if deriving a status needs stored state to stay correct. That would mean some state is not observable in GitHub, and it must be recorded as a fact rather than read from the board.
