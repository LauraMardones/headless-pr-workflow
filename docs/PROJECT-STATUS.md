# Project Status Model

This document is the canonical reference for the project status model used in this repository. It covers all statuses, their semantics, the fact vs intent distinction, and all allowed transitions.

Downstream stories (#117, #119, #120) depend on this document. Do not add executor routing, handoff templates, blocked-status protocol detail, feature/epic closure protocol, or OSS invariant content here — those belong in their respective stories.

---

## Statuses

### Backlog

The item has been created but has not entered refinement. It is not yet well-defined and is not available for implementation.

### In refinement

The item is actively being refined. Acceptance criteria, scope, dependencies, and files affected are being clarified by the PO. The item is not yet ready for an executor to pull.

### Refined

The item is well-defined and meets the definition of ready, but has at least one unresolved hard dependency. **Refined is a waiting room, not a terminal state.** Executors must not pull items in Refined; they pull from Ready for implementation only.

### Ready for implementation

The item is refined and all hard dependencies are resolved. This is the executor-pull trigger: an executor may pick up this item and begin implementation. When an item moves into this status, the appropriate `executor:` label is applied.

### In implementation

An executor has pulled the item and implementation is actively underway. A branch and (draft) PR exist or are being created. This is a fact: work is in progress.

### Needs rework

A reviewer has requested changes. The PR has been returned to the implementer. The item remains associated with the open PR. Once the implementer addresses the feedback and pushes a new commit, the story moves back to In review.

### Blocked

Progress on the item has stopped due to an external dependency, a decision that has not been made, a resource constraint, a conflict, or another blocking condition. The blocker must be declared and owned. This status can apply to an item at any point from Refined onward.

### In review

The implementation is complete and the PR is open and ready for review. A reviewer (not the implementer) is expected to assess the PR. This is a fact: the implementation is submitted and awaiting review.

### Ready to merge

The PR has been reviewed and approved. All required checks have passed. The item is waiting only for the merge action. This is an intent signal: it declares that all review conditions are satisfied and a merge is authorized.

### Done

The PR has been merged. The item is complete. This is a fact.

---

## Fact vs Intent Distinction

The status model is a **hybrid model**: some statuses are facts and some are intent signals.

| Type | Meaning |
|---|---|
| **Fact** | The status reflects the current real-world state of the work. Executors set fact statuses when an event occurs (implementation starts, PR opens, PR merges). |
| **Intent signal** | The status declares that a condition has been met and authorizes the next action. It is a signal to the next actor, not a description of what is happening right now. |

**Intent signal statuses** — "Ready for X" pattern:

| Status | Intent declared |
|---|---|
| Ready for implementation | Dependencies are resolved; an executor may pull this item |
| Ready to merge | Review is approved and checks pass; a merge is authorized |

**Fact statuses** — all others:

| Status | Fact described |
|---|---|
| Backlog | Item exists, not yet refined |
| In refinement | Refinement is actively underway |
| Refined | Item is well-defined; at least one hard dependency is unresolved |
| In implementation | An executor is actively working on this item |
| Needs rework | Reviewer has requested changes; item is back with the implementer |
| Blocked | Progress has stopped due to a declared blocker |
| In review | PR is open and submitted for review |
| Done | PR has been merged; work is complete |

### Example

- **"Ready for implementation"** — intent signal. It does not mean someone is implementing; it means all prerequisites are met and an executor is authorized to start.
- **"In implementation"** — fact. An executor has started; a branch exists.
- **"Ready to merge"** — intent signal. It does not mean the PR is being merged; it means a merge is authorized.
- **"In review"** — fact. The PR is open and submitted; a reviewer is expected to act.

---

## Refined vs Ready for Implementation

These two statuses are frequently confused. The distinction is critical for correct executor routing.

| | Refined | Ready for implementation |
|---|---|---|
| Item is well-defined | Yes | Yes |
| Acceptance criteria are clear | Yes | Yes |
| All hard dependencies resolved | **No** | **Yes** |
| Executor may pull | **No** | **Yes** |
| Role | Dependency waiting room | Executor-pull trigger |

An item moves from Refined to Ready for implementation only when every hard dependency listed in its `## Dependencies` section is closed or confirmed resolved. The PO or a dependency-tracking process makes this transition; executors do not self-promote items from Refined.

---

## Allowed Transitions

All transitions and their triggering events are listed below. Transitions not listed here are not permitted.

| From | To | Triggering action or event |
|---|---|---|
| Backlog | In refinement | PO begins refining the item |
| In refinement | Refined | Item meets definition of ready; at least one hard dependency remains unresolved |
| In refinement | Backlog | Refinement is abandoned or deferred |
| Refined | Ready for implementation | All hard dependencies are resolved |
| Refined | Blocked | A blocking condition is declared on this item |
| Ready for implementation | In implementation | An executor pulls the item and begins work (branch created, draft PR opened) |
| In implementation | In review | Executor submits PR for review (PR marked ready for review) |
| In implementation | Blocked | A blocking condition is declared while implementation is underway |
| In review | Ready to merge | Reviewer approves the PR and all required checks pass |
| In review | Needs rework | Reviewer requests changes; PR is returned to the implementer |
| In review | Blocked | A blocking condition surfaces during review |
| Needs rework | In implementation | Executor picks up the rework on the existing branch |
| Ready to merge | Done | PR is merged |
| Ready to merge | In review | Approval becomes stale (new commit pushed after approval); item returns to In review |
| Blocked | Refined | Blocker resolved; item has unresolved hard dependencies remaining |
| Blocked | Ready for implementation | Blocker resolved; all hard dependencies are now resolved |
| Blocked | In implementation | Blocker resolved; implementation resumes on an active branch |
| Any | Blocked | A blocking condition is declared at any point from Refined onward |

### Notes on specific transitions

**In review → Ready to merge**: Both conditions must be true simultaneously — at least one approval from a reviewer who is not the implementer, and all required CI checks passing.

**Ready to merge → In review**: If a new commit is pushed to the PR after approval (for example, to address a last-minute issue), the approval is stale. The item returns to In review and must be re-reviewed.

**Blocked resolution**: When a blocker is resolved, the item returns to the status it held before Blocked, or to Ready for implementation if the blocker was also the last unresolved dependency.

---

## Status Summary Table

| Status | Type | Executor may pull? | Notes |
|---|---|---|---|
| Backlog | Fact | No | Not yet refined |
| In refinement | Fact | No | Refinement in progress |
| Refined | Fact | No | Dependency waiting room |
| Ready for implementation | Intent | Yes | Executor-pull trigger |
| In implementation | Fact | — | Work in progress |
| Needs rework | Fact | — | Back with implementer |
| Blocked | Fact | No | Blocker must be declared |
| In review | Fact | — | Awaiting reviewer |
| Ready to merge | Intent | — | Merge authorized |
| Done | Fact | — | Merged and complete |
