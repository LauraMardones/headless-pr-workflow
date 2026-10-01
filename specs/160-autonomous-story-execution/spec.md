# Epic #160: Autonomous story-level execution dispatcher

**Epic issue:** #160
**Milestone:** 6 — Autonomous Execution

## Goal

A story that is refined and has no open hard dependency is implemented, reviewed, merged and cleaned up without the PO, apart from decision blockers, closure confirmations and Red flow-health alerts, and within the usage limits.

Today every step has a command, but the PO still starts each one by hand and relays work between them. The PO can make product judgments but cannot do code review, so that relay is the main bottleneck. The dispatcher foundation is delivered; what still needs the PO in the story cycle is review, approval, merge and cleanup.

## Scope

In, delivered:

- A dispatcher that starts implementation for ready stories without the PO, within a WIP limit of two parallel stories with no file overlap.
- Slack notifications for decision blockers, closure confirmation requests, Red flow health, a reached budget cap and dispatcher errors.
- One Slack notification when an item becomes ready for refinement. It stays in use until the new Epic makes refinement start without the PO.
- Daily budget caps per executor type, and a pause switch (`DISPATCHER_ENABLED`).
- Automatic resume after the PO answers a decision blocker, and a check against documented decisions before a blocker is raised.
- The test suite runs in CI on every PR as a required check.
- The dispatcher skips items that are not stories.

In, remaining:

- Review without the PO: one Codex review of finished work, and an approval gate satisfied by independent review on the current head (ADR-006, ADR-007).
- Merge and cleanup without the PO and without a model call.
- A decision, recorded in an ADR, on how implementation and refinement sessions are started (#289).
- Notifications that need PO action are not lost when a delivery fails.
- A decision blocker can be decided from the notification: it states the question, the options and a recommendation.
- One end-to-end proof in this repository, from a ready story to cleanup.

Out:

- Everything before a story is ready to implement. It moved to Epic #<new Epic number>: the spec merge as start signal (ADR-008), refinement by the Tech Lead and the rewritten `/refine*` commands (ADR-009), the scheduler and board status derived from facts (ADR-010), and issues #258 and #283.
- Removing decision blockers, closure confirmations or Red flow-health alerts as human touchpoints.
- Replacing the manually invoked command contracts (`/implement`, `/review`, `/merge`, `/cleanup`).
- Real-time dashboards or custom UIs.
- A Slack bot or two-way Slack interaction.
- Dispatch latency under five minutes.
- Open-source model integration.
- An owned or persistent runner host, and rolling-window budget accounting with failover (ADR-005, not adopted).
- A second human approver.
- A Codex review of every push.
- Approval through a Codex review for PRs with no linked story.

## Acceptance Criteria

- **E1:** <An outcome the PO can verify at Epic closure.>
  - **Verified by:** <How the PO checks it at closure, e.g. a usage scenario, a measurement, or a named test.>
- **E2:** <…>
  - **Verified by:** <…>

## Decisions

### Split the Epic where a story becomes ready to implement — 2026-10-01

**Chosen:** #160 keeps the dispatcher foundation and the story cycle: a story that is ready to implement is delivered without the PO. Everything before that point moves to a new Epic, specified in its own `/spec` session: the spec merge as start signal (ADR-008), refinement by the Tech Lead (ADR-009), and scheduling and board status derived from facts (ADR-010). #258 and #283 move with it. Until the new Epic delivers, the current dispatcher and status rules apply, as each of those ADRs states. The PO accepts that refinement stays manual until then, and that the end-to-end proof for #160 starts at a ready story.
**Rejected:** Keep everything in #160 — the Epic would close only when the full chain from spec merge to cleanup runs, which puts closure much further out and makes the spec about twice as large.

### Goal of #160 after the split — 2026-10-01

**Chosen:** A story that is refined and has no open hard dependency is implemented, reviewed, merged and cleaned up without the PO, apart from decision blockers, closure confirmations and Red flow-health alerts, and within the usage limits.
**Rejected:** "From spec merge to delivered Epic without the PO" — that is the combined goal of #160 and the new Epic, and it is not reachable by #160 alone after the split.

### Spike #289 stays in #160 — 2026-10-01

**Chosen:** #289 (how sessions are started) stays in #160, with the scope ADR-009 gave it: it covers refinement sessions as well as implementation sessions. The new Epic depends on its result.
**Rejected:** Move #289 to the new Epic — the story cycle in #160 needs its answer first.

### The spec covers the whole Epic — 2026-10-01

**Chosen:** The spec covers delivered and remaining work, so that Epic closure verifies everything #160 promised.
**Rejected:** Cover only the remaining work — the delivered first wave would then never be checked against an Epic criterion.

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
