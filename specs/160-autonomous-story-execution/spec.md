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
- All autonomous model work runs on the PO's existing subscriptions. No metered API usage remains.
- A usage limit the PO sets, including a reserve of the PO's subscription allowance for the PO's own use. The PO can change it without a code change.
- Notifications that need PO action are not lost when a delivery fails.
- A decision blocker can be decided from the notification: it states the question, the options and a recommendation.
- An end-to-end proof in this repository: three consecutive stories, each from ready to cleanup.

Out:

- Everything before a story is ready to implement. It moved to Epic #306: the spec merge as start signal (ADR-008), refinement by the Tech Lead and the rewritten `/refine*` commands (ADR-009), the scheduler and board status derived from facts (ADR-010), and issues #258 and #283.
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

- **E1:** A story that is ready to implement is implemented, reviewed, merged and cleaned up with no PO action, when no blocker arises.
  - **Verified by:** three consecutive stories in this repository whose issue and PR timelines show no PO comment, command or approval between the story becoming ready and its branch being deleted.
- **E2:** No PR merges without an independent review on its current head and green required checks, and the PO never supplies a review or approval.
  - **Verified by:** each proof PR from E1 shows an independent review on the merged head SHA, and the merge-gate tests for the fail-closed cases pass in CI.
- **E3:** The PO is notified in Slack within five minutes when a decision blocker, a closure confirmation request, a Red flow-health event, a reached usage limit or an item ready for refinement occurs. Each event is notified once, and a notification that needs PO action still arrives when a delivery attempt fails.
  - **Verified by:** a provoked decision blocker with a timestamp comparison, and a simulated failed delivery followed by a delivered notification.
- **E4:** The PO can decide a decision blocker from the notification alone, answer with a GitHub comment, and the work resumes without any further PO action.
  - **Verified by:** a provoked blocker whose notification states the question, options and recommendation, answered by one comment, after which the story continues on the next poll.
- **E5:** Autonomous work stays within limits: at most two stories in implementation with no file overlap; a usage limit the PO sets, which pauses work and notifies the PO; a reserve of the PO's five-hour and weekly subscription allowance below which no new session starts; and a switch that stops new work. The PO can change the usage limit and the reserve without a code change.
  - **Verified by:** setting `DISPATCHER_ENABLED=false` and seeing no session start on the next poll; setting the usage limit below current usage and receiving the pause notification; and changing the reserve in the repository settings and seeing a session start refused below the new value.
- **E6:** Autonomous sessions start without the PO and run on the PO's existing subscriptions. No autonomous work uses a metered API, and a session that hits a provider limit pauses cleanly.
  - **Verified by:** the ADR from #289 is on `main` with `Status: Accepted`; the three proof stories from E1 were started by its mechanism; and the API-key secrets are removed from the repository, with no API usage on the provider billing pages for the proof period.
- **E7:** Merge and cleanup use no model.
  - **Verified by:** the run logs of the three proof stories show no model invocation for merge and cleanup.

## Decisions

### Split the Epic where a story becomes ready to implement — 2026-10-01

**Chosen:** #160 keeps the dispatcher foundation and the story cycle: a story that is ready to implement is delivered without the PO. Everything before that point moves to Epic #306, specified in its own `/spec` session: the spec merge as start signal (ADR-008), refinement by the Tech Lead (ADR-009), and scheduling and board status derived from facts (ADR-010). #258 and #283 move with it. Until the new Epic delivers, the current dispatcher and status rules apply, as each of those ADRs states. The PO accepts that refinement stays manual until then, and that the end-to-end proof for #160 starts at a ready story.
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

### The end-to-end proof is three consecutive stories — 2026-10-01

**Chosen:** The Epic closes on three consecutive stories that each go from ready to cleanup with no PO action.
**Rejected:** One story — a single run can succeed by luck.

### No metered API usage — 2026-10-01

**Chosen:** All autonomous model work runs on the PO's existing subscriptions, and metered API usage ends with this Epic. This includes the dispatcher's review of Codex-implemented stories. For #289, "runs on the subscription" is a requirement, not a preference.
**Rejected:** Keep the API for some paths, or leave it to #289 to weigh — the PO wants to be rid of API costs altogether.

### Usage limits: only the outcome is specified — 2026-10-01

**Chosen:** The spec requires that a limit the PO sets pauses work and notifies the PO. The unit and granularity of that limit are delegated to the Tech Lead and decided after #289. This replaces the decision "Token budget granularity" (2026-06-02).
**Rejected:** Keep the daily token cap per executor type — it is tied to model tiers and to token estimates per story size, and it is probably the wrong unit once sessions run on the subscription.

### A reserve of the subscription allowance, set by the PO — 2026-10-01

**Chosen:** Autonomous work leaves a reserve of the five-hour and weekly subscription allowance for the PO's own use. No new autonomous session starts when less than the reserve remains. The PO can change the reserve easily, without a code change.
**Rejected:** "The dispatcher does not consume the PO's chat allowance" (the earlier criterion) — Claude chat and Claude Code share one allowance, so it cannot hold once sessions run on the subscription. A reserve fixed in code — the PO wants to adjust it.

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
