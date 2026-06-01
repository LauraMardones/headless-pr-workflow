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

---

## Handoff Protocol

When an executor must stop mid-story, it commits all in-progress work to the feature branch and writes a structured handoff note to GitHub before stopping. The handoff note is the durable record that enables any subsequent executor to resume without relying on session memory or terminal history.

See also: [State Model](HEADLESS-PR-WORKFLOW.md#state-model) — handoff notes are durable state; they must be stored in GitHub or committed workflow metadata, not in local execution context.

### Handoff note format

Handoff notes are plain structured markdown. No model-specific conventions, annotations, or front matter are used (OSS compatibility invariant).

Post the handoff note as a comment on the PR or issue. Use this template exactly — all six fields are required:

```
## Handoff Note
Story ref: #<issue-number>
Last action: <what was done in this session>
Next action: <what the next executor should do first>
Open questions: <unresolved questions, or "none">
Files touched: <list of files changed, or "none">
Token budget consumed: <estimated tokens used, or "unknown">
```

**Field definitions:**

| Field | Required content |
|---|---|
| Story ref | GitHub issue number for the story being implemented |
| Last action | The last concrete action taken — a completed step, a pushed commit, a decision made |
| Next action | The first concrete action the next executor must take to resume work correctly |
| Open questions | Any unresolved questions the next executor must answer before or during work; "none" if there are none |
| Files touched | All files modified or created so far in this story's branch; "none" if no files were changed |
| Token budget consumed | Approximate token count used in this session; helps the next executor plan scope |

### When to write a handoff note

Write a handoff note whenever:

- An executor stops mid-story for any reason (token limit, session end, external interrupt).
- The story moves to Blocked and the executor is disengaging.
- The story is being deliberately handed off to a different executor type (e.g., Claude Code → Codex).

A handoff note is not required when the story completes in a single session and moves to In review.

---

## Recovery Protocol

The recovery protocol applies when a story is found in **In implementation** status but shows signs of abandonment: no recent activity and no handoff note.

See also: [Lifecycle](HEADLESS-PR-WORKFLOW.md#lifecycle) — takeover between assistants, machines, and sessions must be recoverable from GitHub state plus explicit handoff notes.

### Stale detection

A story in **In implementation** is considered stale when **both** of the following are true:

1. No activity (commits, comments, PR updates) has occurred in **more than 2 hours**.
2. No handoff note is present on the PR or issue.

Both conditions must be present. Inactivity alone does not trigger recovery — a handoff note explaining the pause is sufficient. No other time-based heuristics apply beyond this stale trigger.

### Recovery action

When a stale story is detected on executor pre-flight:

1. The executor posts a **recovery comment** on the issue or PR explaining the stale detection.
2. The story status is rolled back to **Ready for implementation**.
3. No in-progress work is discarded — the branch and any commits remain intact.
4. The next executor may resume from the existing branch or start fresh, based on the state of the branch.

### Recovery comment format

```
## Recovery Comment
Detected: story in "In implementation" with no activity for >2h and no handoff note.
Action: status rolled back to "Ready for implementation".
Branch: <branch-name> — existing commits intact.
Next executor: review branch state before pulling.
```

### What recovery does not do

- Recovery does not delete commits or branches.
- Recovery does not reassign the executor label.
- Recovery does not trigger automatic re-implementation.

---

## Blocked Status Protocol

A story or task is moved to **Blocked** when progress has stopped due to a condition that cannot be resolved by the current executor alone.

See also: [Lifecycle](HEADLESS-PR-WORKFLOW.md#lifecycle) — blockers are recorded in GitHub review threads, comments, checks, or issue/PR metadata.

### Blocker types

Exactly five blocker types are recognised:

| Type | Definition |
|---|---|
| **dependency** | A hard dependency has not been completed or resolved. |
| **decision** | A decision required to proceed has not been made. Only the PO resolves decision blockers. |
| **external** | Progress depends on an action or output from outside the repository (e.g., a third-party API, an infra change, an external review). |
| **conflict** | A file conflict or WIP overlap with another active story prevents progress. |
| **resource** | A required resource (token budget, execution environment, tool access) is unavailable. |

### Declaration format

When declaring a blocker, post a structured GitHub comment on the issue or PR using this format exactly:

```
## Blocked Declaration
Type: <dependency | decision | external | conflict | resource>
Declared by: <executor name or GitHub handle>
Blocks: #<issue-number> — <story title>
Unblocked when: <specific, testable condition — not a general description>
Owner: <who is responsible for resolving this blocker>
State of in-progress work: <branch name and summary of work completed so far, or "none">
```

All fields are required. The **"Unblocked when"** field must state a specific, testable condition — not a vague description like "when the dependency is done".

### Decision blockers

Decision blockers require immediate PO involvement:

- Post the declaration comment and **@mention the PO** in the same comment.
- Only the PO may resolve a decision blocker.
- The executor must not attempt to unblock a decision blocker unilaterally.

### Monitoring and escalation

- The PO reviews the board weekly for blocked items.
- Executors scan for blocked stories on every pre-flight.
- **No story may remain Blocked for more than 1 week without an owner update.** If the owner has not posted an update within 1 week, the PO escalates.

### Resolution

When a blocker is resolved:

1. The owner posts an **"Unblocked"** comment on the issue or PR, referencing the original declaration.
2. The story status returns to **Refined** (if hard dependencies remain) or **Ready for implementation** (if all dependencies are resolved).
3. The executor label is reapplied as appropriate.

### Cascading blocked rules

Downstream stories in **Refined** status are not relabeled Blocked solely because an upstream story is blocked, unless the routing system would incorrectly pick them up as Ready for implementation. Relabel only when necessary to prevent incorrect executor routing.

---

## Closure Protocol

The closure protocol defines the roles and responsibilities at feature and epic closure. Two distinct types of success criteria require different judges.

See also: [Review Separation](HEADLESS-PR-WORKFLOW.md#review-separation) — the same separation-of-roles principle that governs PR review applies at feature and epic closure.

### Role split

| Criteria type | Definition | Judge |
|---|---|---|
| **Technical** | Tests pass, contract implemented correctly, files match the story declaration | **Codex** |
| **Product** | Is this the right thing? Does it match the vision? Is documentation clear? | **PO** |

**PO role at closure:** product judge only. A one-line confirmation is sufficient. The PO never writes the closing comment and is not expected to assess technical correctness.

**Codex role at closure:** technical verifier and closing comment author. Codex verifies technical criteria, writes the structured closing comment, and performs the close action.

### Feature closure flow

1. The implementing executor closes the last story in the feature and posts a signal comment on the feature issue.
2. Codex runs technical verification against all feature success criteria.
3. Codex writes a structured closing comment on the feature issue, summarising what was delivered vs. what was scoped.
4. The PO reads the summary and answers one question: *"Is this what I wanted?"*
5. The PO gives a brief one-line product confirmation.
6. Codex closes the feature issue.

### Epic closure flow

1. The last feature closes. Both executors (Claude Code and Codex) produce a joint delivery summary against the epic goal.
2. The PO reads the product-level summary and gives a brief approval.
3. Codex closes the epic issue with a formal closing comment.

### What the PO must not do

- Write the technical closing comment.
- Assess whether tests pass or contracts are correctly implemented.
- Perform the GitHub close action (Codex does this).

### What Codex must not do

- Make the product judgment ("Is this the right thing?").
- Require the PO to write a detailed technical summary.
- Wait for the PO before posting the structured closing comment — post it, then wait for the one-line product confirmation.

---

## Rolling Refinement

Rolling refinement is the practice of refining the next story while the current story is being implemented, so that the implementation queue never runs dry.

See also: [Lifecycle](HEADLESS-PR-WORKFLOW.md#lifecycle) — the workflow loops continuously; rolling refinement keeps the loop fed.

### Rule

Refine story N+1 while implementing story N.

**Never refine more than one sprint ahead.** Refining too far ahead wastes refinement work when decisions change during implementation.

### Gate for sequential dependencies

Do not begin refining story N+1 until story N's key decisions and interfaces are stable. "Stable" means:

- The story's acceptance criteria are finalised.
- The files it will touch are declared.
- Any decisions that downstream stories depend on have been made and recorded.

If story N is still in early implementation and key interfaces are unresolved, hold refinement of N+1 until those decisions are made.

### Parallel lanes

Refinement of stories in parallel lanes (no sequential dependency between them) may proceed independently of this gate. The gate applies only to stories that are sequentially dependent.
