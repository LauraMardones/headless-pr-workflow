# Refinement Pipeline

## Token-saving conventions

Use the repository helper scripts for stable, repeated workflow fields instead of reconstructing those fields in freeform prose. In particular, implementation, review, and merge handoffs use `scripts/session-summary.sh`; merge decisions retain the separate one-line output from `scripts/merge-gate-summary`; and self-review may use focused helpers such as `scripts/ac-summary.sh` and `scripts/dispatcher-change-check.sh`.

Reserve prose for information that the generated fields cannot safely replace: blockers, deviations, residual risk, and decisions. Do not repeat the PR body, implementation summary, or AC/DoD coverage after a generated Session Summary. Helper output is generate-only; the acting executor remains responsible for posting it through the approved GitHub operation transport.

Applicable handoffs must never omit the issue and/or PR number, the relevant fresh head SHA, checks, blockers, or next action. Compactness must not remove approval evidence, unresolved-thread results, merge-policy gates, or any other phase-specific safety requirement. A merge helper's `Merge gate:` line is separate evidence and must not be inserted as an unsupported field inside the generated Session Summary block.

This document defines the normative strategy for issue refinement in HPW-based AI-assisted development. It covers pipeline shape, buffer targets, the usage-feedback loop, parent-linking requirements, and escalation triggers.

---

## Normative Strategy: Rolling, Just-in-Time Refinement

Rolling, just-in-time refinement is the required strategy. Big-bang refinement — refining all issues for an Epic or Feature before implementation begins — is explicitly not the default.

**Why rolling refinement:**

- The bottleneck is refinement quality and human review bandwidth, not implementation speed.
- Usage feedback from early implementations contains product signal that cannot be anticipated upfront. Refining ahead of that signal produces stale, over-specified requirements.
- Rolling refinement keeps the ready buffer small and fresh, reducing the cost of changing direction.
- Big-bang refinement creates a false sense of completeness and defers product learning to the end of delivery.

---

## Buffer Target: 3–5 Issues Refined and Ready

At any point, 3–5 issues should be in the **Ready for implementation** status ahead of the current implementation front.

**Why not fewer than 3:**

- A buffer of 0–2 creates pipeline stalls: if refinement is slow or a Human Approver decision is needed, implementation blocks waiting for ready work.

**Why not more than 5:**

- A buffer larger than 5 increases the risk of refining against stale product understanding. Usage feedback from recent implementations may invalidate over-refined issues before they are pulled.
- Large buffers create a hidden backlog of in-flight refinement work that is difficult to prune without waste.

The PO may document a buffer adjustment as a project decision if the scope or cadence requires it.

---

## Usage-Feedback Loop

Usage findings from recently implemented stories are first-class inputs to the next refinements. They must be checked before each refinement session.

### When to check

Before beginning refinement on the next issue in a Feature or Epic, check for PO usage findings on recently implemented issues under the same Feature or Epic.

### What to look for

- GitHub comments posted by the Product Validator on issues or PRs closed or merged within the last 14 days under the same Feature or Epic.
- Comments that identify a behavior gap, a misaligned assumption, or a product direction change.

The 14-day lookback window is the default. The PO may adjust this as a documented project decision.

### How to incorporate findings

If a usage finding is present:
1. Assess whether it affects acceptance criteria or scope for the issue about to be refined.
2. If affected: incorporate the finding into the refinement before creating or updating the issue.
3. If not affected: proceed with refinement and note in the issue that recent usage feedback was reviewed and found not applicable.

Usage findings posted by the Product Validator carry the same weight as review blockers. They are valid workflow inputs that must be addressed before a story closes.

---

## Pipeline Shape

The full discovery-driven flow, from intake to feedback:

```
spec-kit
  │
  ▼
gh issue create          ← normative upstream intake; epic/feature issues created here
  │
  ▼
/refine-epic or
/refine-feature          ← sub-issues created, parent links set
  │
  ▼
/refine-story /
/refine-task             ← acceptance criteria, scope, files affected, ACs finalized
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
PO usage findings        ← Product Validator tests delivery, posts GitHub comments
  │
  ▼
/refine next             ← findings feed into next refinement cycle (see Usage-Feedback Loop)
```

`spec-kit → gh issue create` is the normative upstream intake pattern. Issues created outside this path must still be triaged against the same refinement standards before entering the ready buffer.

---

## Parent-Linking Requirement

`/refine-epic` and `/refine-feature` must set the GitHub parent link on all sub-issues they create. This is a normative pipeline requirement, not an optional convention.

**Why:** GitHub's native sub-issue relationship is the mechanism that allows the usage-feedback loop to scope its lookback correctly. Without parent links, the pipeline cannot reliably find related usage findings when refining the next issue under the same Feature or Epic.

Implementation of this requirement in the `/refine-epic` and `/refine-feature` commands is tracked separately from this document.

---

## Escalation Triggers

The following conditions warrant interrupting the Human Approver. Do not resolve these conditions autonomously.

| Condition | Action |
|---|---|
| A PO usage finding contradicts an existing acceptance criterion on an open story | Stop. Post the conflict as a GitHub comment. Escalate to Human Approver for resolution. |
| Refinement of a story would change the scope of a story already in implementation | Stop. Report the overlap. Escalate to Human Approver before updating either story. |
| A dependency decision required for refinement has not been made and cannot be deferred | Stop. Document the open decision in the issue. Escalate to Human Approver. |
| The ready buffer drops to zero and no issue can be refined without an unresolved dependency | Stop. Report the pipeline stall. Escalate to Human Approver. |
| A usage finding indicates a product direction change that affects multiple open issues | Stop. Do not refine further under the affected Feature or Epic until Human Approver confirms the new direction. |
| An acceptance criterion cannot be determined without product, legal, or release judgment | Stop. Document the open question. Escalate to Human Approver. |

---

## When Big-Bang Refinement May Be Appropriate

Big-bang refinement — refining the full set of stories for a Feature or Epic before implementation begins — is appropriate only when:

- The Feature or Epic is small (≤5 stories) and unlikely to generate usage feedback that would change scope before implementation completes.
- The team has explicit prior knowledge of the domain that makes upfront refinement low-risk.
- A hard external deadline requires full scope visibility before work starts.

**Trade-offs:**

- Big-bang refinement produces a complete picture of scope upfront, which can aid planning and communication.
- It increases the risk of refining against stale product understanding. The larger the Feature or Epic, the higher this risk.
- It defers the usage-feedback loop until all stories are delivered, removing the option to incorporate early findings.

When big-bang refinement is chosen, document it as a project decision with the rationale. The usage-feedback loop still applies: check for findings before the next big-bang cycle.
