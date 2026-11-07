# Refinement Pipeline

## Token-saving conventions

Use the repository helper scripts for stable, repeated workflow fields instead of reconstructing those fields in freeform prose. In particular, implementation, review, and merge handoffs use `scripts/session-summary.sh`; merge decisions retain the separate one-line output from `scripts/merge-gate-summary`; and self-review may use focused helpers such as `scripts/ac-summary.sh` and `scripts/dispatcher-change-check.sh`.

Reserve prose for information that the generated fields cannot safely replace: blockers, deviations, residual risk, and decisions. Do not repeat the PR body, implementation summary, or AC/DoD coverage after a generated Session Summary. Helper output is generate-only; the acting executor remains responsible for posting it through the approved GitHub operation transport.

Applicable handoffs must never omit the issue and/or PR number, the relevant fresh head SHA, checks, blockers, or next action. Compactness must not remove approval evidence, unresolved-thread results, merge-policy gates, or any other phase-specific safety requirement. A merge helper's `Merge gate:` line is separate evidence and must not be inserted as an unsupported field inside the generated Session Summary block.

This document defines the normative strategy for issue refinement in HPW-based AI-assisted development. It covers pipeline shape, buffer targets, the usage-feedback loop, parent-linking requirements, and escalation triggers.

---

## Refinement completeness gate

`scripts/refine-check --issue <n> [--repo <owner/repo>] [--json]` is the last step of every refinement (`.claude/commands/refine.md` and the five `refine-*.md` files), after the board write. It reads the issue, the repository label list, the open hard dependencies and the board Status live, and reports one line per check: `type_label`, `executor_label` (story, task and bug only, the dispatcher's executable types), `prio_label`, `po_status_board` (body `PO status:` equals the board Status), `hard_dependencies` (never `Ready for implementation` while one is open; `Refined; blocked by #X` otherwise), `required_sections` (per type, non-empty), `open_questions` (no `### ` question when ready), `parent_refs` (when ready), `spec_record` (the body's `Spec:` line names `specs/<epic>-*/spec.md` for the issue's Epic, and the file exists at the recorded commit) and `feature_criteria` (`type:feature` only: a `Criterion prefix: F<k>` line, and every Success Criteria item written `**F<k>.<m>** (serves E<n>, …)`).

Exit `0`: every applicable check passes. Exit `1`: a check fails and is named. Exit `2`: something could not be read, or the arguments are invalid; never a pass. Only exit `0` permits reporting `Refined` or `Ready for implementation`. After a non-zero exit the refine session moves the board and the body back to `In refinement` and lists the failing checks as blockers. The refine Session Summary carries the result in `Checks run:`. The script is not an `hpw` command, so `docs/PR-AUTOMATION-MAP.md` does not list it.

## Normative Strategy: Rolling, Just-in-Time Refinement

Rolling, just-in-time refinement is the required strategy. Story refinement is always just-in-time against the ready buffer; there is no big-bang refinement, in which all stories of an Epic or Feature are refined before implementation begins. A small Epic simply has all its stories in the buffer at once (ADR-009; Epic #306 → Decisions "No big-bang refinement").

**Why rolling refinement:**

- The bottleneck is refinement quality and human review bandwidth, not implementation speed.
- Usage feedback from early implementations contains product signal that cannot be anticipated upfront. Refining ahead of that signal produces stale, over-specified requirements.
- Rolling refinement keeps the ready buffer small and fresh, reducing the cost of changing direction.

---

## Buffer Target: 3–5 Issues Refined and Ready

At any point, 3–5 issues should be in the **Ready for implementation** status ahead of the current implementation front.

The range is set by the repository variables `READY_BUFFER_MIN` (currently 3) and `READY_BUFFER_MAX` (currently 5); see [WIP and queue limits](DISPATCHER-CONFIG.md#wip-and-queue-limits). The numbers in this section state the current policy; the variables are what the scripts read.

**Why not fewer than 3:**

- A buffer of 0–2 creates pipeline stalls: if refinement is slow or a decision blocker is open, implementation blocks waiting for ready work.

**Why not more than 5:**

- A buffer larger than 5 increases the risk of refining against stale product understanding. Usage feedback from recent implementations may invalidate over-refined issues before they are pulled.
- Large buffers create a hidden backlog of in-flight refinement work that is difficult to prune without waste.

The buffer is changed through the repository variables, not through a project decision.

---

## Usage-Feedback Loop

Usage findings from recently implemented stories are first-class inputs to the next refinements. They must be checked before each refinement session.

### When to check

Before beginning refinement on the next issue in a Feature or Epic, check for PO usage findings on recently implemented issues under the same Feature or Epic.

### What to look for

- GitHub comments posted by the PO on issues or PRs closed or merged within the last 14 days under the same Feature or Epic.
- Comments that identify a behavior gap, a misaligned assumption, or a product direction change.

The 14-day lookback window is the default. The PO may adjust this as a documented project decision.

### How to incorporate findings

The Tech Lead classifies each usage finding (`.claude/commands/refine.md` → Step 0):

- A finding that affects scope or an acceptance criterion becomes a `Type: decision` Blocked Declaration on the issue being refined; the affected criterion stays unchanged until the PO answers.
- Any other finding is noted under the issue's `## Decisions` and refinement continues.

A finding is never incorporated into the refinement directly: one that changes scope is a product decision, and only the PO makes it. Usage findings posted by the PO carry the same weight as review blockers.

---

## Pipeline Shape

The flow, from the merged spec to feedback:

```
/spec                    ← the Analyst and the PO write the spec
  │
  ▼
spec PR merged by the PO ← the merge is the PO's approval of the spec
  │
  ▼
spec check               ← scripts/spec-check; a failure is a decision blocker
  │
  ▼
/refine-epic             ← Feature slices, parent links set
  │
  ▼
/refine-feature          ← Feature criteria, Story / Task / Bug seeds
  │
  ▼
/refine-story /
/refine-task /
/refine-bug              ← acceptance criteria, scope, files affected, mapping to Feature criteria
  │
  ▼
Ready for implementation ← buffer maintained at 3–5 issues
  │
  ▼
/implement               ← executor pulls from buffer
  │
  ▼
/review                  ← separate review session
  │
  ▼
/merge                   ← merge owner merges approved SHA
  │
  ▼
usage findings           ← the PO tests the delivery and posts GitHub comments
  │
  ▼
next refinement          ← findings feed into it (see Usage-Feedback Loop)
```

Refinement starts only from a merged spec (`.claude/commands/refine.md` → Tech Lead rules). An Epic without one is not refined.

### `/refine-story`, `/refine-task` and `/refine-bug`: Epic and Feature Requirements Mapping

`/refine-story`, `/refine-task` and `/refine-bug` write a `## Epic and Feature Requirements Mapping` section into every refined Story, Task and Bug; `scripts/refine-check` fails `required_sections` for all three types when it is missing or empty. The refiner reads the parent Epic's merged spec (the approved scope, ADR-008; the Epic issue body only as a legacy fallback when no spec exists) and the parent Feature's body, and lists each requirement that applies to the issue as exactly one of: an acceptance criterion (by number), or an entry under `Not in this story` (`Not in this task`, `Not in this bug`) with a reason — `deferred to #N` or `not applicable`. A deferred requirement must link an existing follow-up issue before the story moves to `Ready for implementation`; the refiner creates it, or raises a `Type: decision` Blocked Declaration when it cannot. Time bounds, delivery guarantees and similar statements an executor needs must be copied into the story's Scope or Acceptance Criteria, not only referenced. When a story adds a new script, a workflow step and docs together, the refiner records a `Split decision:` line in `## Decisions`. Every acceptance criterion of the story appears in the mapping as `AC <n> serves F<k>.<m>` and names the Feature criterion it serves (ADR-009); a criterion without a Feature parent is not allowed. The mapping points to requirements; it does not restate the Epic spec.

### Epic coverage check

`scripts/criteria-check --epic <n> [--repo <owner/repo>] [--ref <git-ref>] [--json]` reads the Epic's merged `specs/<n>-*/spec.md` (Epic criteria `E<n>`), the Feature criteria of each child `type:feature` issue (`**F<k>.<m>** (serves E<n>, …)`) and the Story, Task and Bug criteria of each child issue with their `AC <n> serves F<k>.<m>` mapping lines. It reports every Epic criterion no Feature criterion covers and every criterion that names no parent or an unknown one. Exit `0`: no finding. Exit `1`: one or more findings. Exit `2`: a read failed, the spec is missing or ambiguous, or the arguments are invalid; never a pass. The refine commands do not run it; `/verify-closure` does.

---

## Parent-Linking Requirement

`/refine-epic` and `/refine-feature` must set the GitHub parent link on all sub-issues they create. This is a normative pipeline requirement, not an optional convention.

**Why:** GitHub's native sub-issue relationship is the mechanism that allows the usage-feedback loop to scope its lookback correctly. Without parent links, the pipeline cannot reliably find related usage findings when refining the next issue under the same Feature or Epic.

Implementation of this requirement in the `/refine-epic` and `/refine-feature` commands is tracked separately from this document.

---

## Escalation Triggers

The following conditions need a decision from the PO. The Tech Lead does not resolve them and asks nothing in the session. Each action is a `Type: decision` Blocked Declaration on the issue being refined, in the [Blocked Status Protocol](PROJECT-STATUS.md#blocked-status-protocol) format, posted after the search for an existing answer in `docs/`, the ADRs and the parent `## Decisions`.

| Condition | Action |
|---|---|
| A PO usage finding contradicts an existing acceptance criterion on an open story | Stop. Post a `Type: decision` Blocked Declaration on the issue being refined naming the conflict. |
| Refinement of a story would change the scope of a story already in implementation | Stop. Post a `Type: decision` Blocked Declaration on the issue being refined naming the overlap; update neither story. |
| A dependency decision required for refinement has not been made and cannot be deferred | Stop. Post a `Type: decision` Blocked Declaration on the issue being refined naming the open decision. |
| The ready buffer drops to zero and no issue can be refined without an unresolved dependency | Stop. Post a `Type: decision` Blocked Declaration on the issue being refined naming the pipeline stall. |
| A usage finding indicates a product direction change that affects multiple open issues | Stop. Post a `Type: decision` Blocked Declaration on the issue being refined; refine nothing further under the affected Feature or Epic until the PO answers. |
| An acceptance criterion cannot be determined without product, legal, or release judgment | Stop. Post a `Type: decision` Blocked Declaration on the issue being refined naming the criterion. |
