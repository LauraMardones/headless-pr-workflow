# Epic #160: Autonomous story-level execution dispatcher

**Epic issue:** #160
**Milestone:** 6 — Autonomous Execution

## Goal

A story that is refined and has no open hard dependency is implemented, reviewed, merged and cleaned up without the PO, apart from decision blockers, closure confirmations, Red flow-health alerts and the PO's own merge of a PR that changes decision documents (ADR-011), and within the usage limits.

Today every step has a command, but the PO still starts each one by hand and relays work between them. The PO can make product judgments but cannot do code review, so that relay is the main bottleneck. The dispatcher foundation is delivered; what still needs the PO in the story cycle is review, approval, merge and cleanup.

## Scope

In, delivered:

- A dispatcher that starts implementation for ready stories without the PO, within a WIP limit of two parallel stories with no file overlap.
- Slack notifications for decision blockers, closure confirmation requests, Red flow health, a reached budget cap and dispatcher errors.
- One Slack notification when an item becomes ready for refinement. It stays in use until Epic #306 makes refinement start without the PO.
- Daily budget caps per executor type, and a pause switch (`DISPATCHER_ENABLED`).
- Automatic resume after the PO answers a decision blocker, and a check against documented decisions before a blocker is raised.
- The test suite runs in CI on every PR.
- The dispatcher skips items that are not stories.

In, remaining:

- Review without the PO: one Codex review of finished work, and an approval gate satisfied by independent review on the current head (ADR-006, ADR-007).
- Merge and cleanup without the PO and without a model call.
- The test check is required for merge. Today it runs but does not block: `docs/required-check-policy.json` still declares required checks absent.
- A decision, recorded in an ADR, on how implementation and refinement sessions are started (#289).
- All autonomous model work runs on the PO's existing subscriptions. No metered API usage remains.
- A usage limit the PO sets, including a reserve of the PO's subscription allowance for the PO's own use. The PO can change it without a code change.
- Notifications that need PO action are not lost when a delivery fails.
- A decision blocker can be decided from the notification: it states the question, the options and a recommendation.
- An end-to-end proof in this repository: three consecutive stories, each from ready to cleanup.
- A stop on repeated failed sessions: the dispatcher starts no new session, tells the PO once, and starts again only after a PO action (#503).
- After the PO merges a PR that changes decision documents, the story reaches `Done` and cleanup with no further PO action (#505).
- A story whose `/implement` session left a draft PR behind continues on its existing branch and draft PR, and does not hold a WIP place with no session at work (#578). Two cases: a session that failed, continued after the PO's `/unblock`; and a session that stopped silently after its push, continued after a set time with a green required test check and no hand-off.

Out:

- Everything before a story is ready to implement. It moved to Epic #306: the spec merge as start signal (ADR-008), refinement by the Tech Lead and the rewritten `/refine*` commands (ADR-009), the scheduler and board status derived from facts (ADR-010), and issues #258 and #283.
- Removing decision blockers, closure confirmations or Red flow-health alerts as human touchpoints.
- Replacing the manually invoked command contracts (`/implement`, `/review`, `/merge`, `/cleanup`).
- Real-time dashboards or custom UIs.
- A Slack bot or two-way Slack interaction.
- Dispatch latency under five minutes.
- Open-source model integration.
- An owned or persistent runner host, and failover to another provider when a limit is reached (ADR-005, not adopted).
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
  - **Verified by:** setting `DISPATCHER_ENABLED=false` and seeing no session start on the next poll; setting the usage limit below current usage and receiving the pause notification; and changing the reserve without a code change and seeing a session start refused below the new value.
- **E6:** Autonomous sessions start without the PO and run on the PO's existing subscriptions. No autonomous work uses a metered API, and a session that hits a provider limit pauses cleanly.
  - **Verified by:** the ADR from #289 is on `main` with `Status: Accepted`; the three proof stories from E1 were started by its mechanism; and the API-key secrets are removed from the repository, with no API usage on the provider billing pages for the proof period.
- **E7:** Merge and cleanup use no model.
  - **Verified by:** the run logs of the three proof stories show no model invocation for merge and cleanup.
- **E8:** After repeated failed sessions, the dispatcher starts no new session and sends the PO one Slack message that names the failed sessions, their stories and links to their runs. It starts again only after a PO action, and never on its own, also not when its saved state is lost. The PO can change how many failures stop it without a code change. A session stopped by a provider usage limit does not count as a failure.
  - **Verified by:** provoked failed sessions up to the threshold, after which the next poll starts no session and the PO receives exactly one message with the sessions, stories and run links; a further poll with the dispatcher's saved state removed still starts no session; the PO's action, after which the next poll starts a session; and changing the threshold without a code change and seeing the new value take effect.
- **E9:** When the PO has merged a PR that changes decision documents, its story reaches `Done` and is cleaned up with no further blocker and no further PO action.
  - **Verified by:** a story whose PR changes a decision document and is merged by the PO in GitHub; its issue and PR timelines show no blocker and no PO comment or `/unblock` between the PO's merge and the deletion of its branch.
- **E10:** A story whose `/implement` session left a draft PR behind is continued by the dispatcher on its existing branch and draft PR, with no PO action beyond what the case already needs: after a failed session, the PO's `/unblock`; after a session that stopped silently after its push, none. The draft PR never keeps a WIP place with no session at work. A continued session that fails counts toward the stop in E8.
  - **Verified by:** two cases in this repository or a provoked copy of them. First, a story whose `/implement` session failed and left a draft PR: after the PO's `/unblock`, the next poll starts `/implement` on the same branch and PR, not as new work. Second, a draft PR with a green required test check and no hand-off after the set time: the next poll starts `/implement` on it with no PO action. In both, the timelines show no new branch or PR and no PO command other than the `/unblock` of the first case; a failed continued session is counted by the E8 stop.

## Decisions

### Split the Epic where a story becomes ready to implement — 2026-10-01

**Chosen:** #160 keeps the dispatcher foundation and the story cycle: a story that is ready to implement is delivered without the PO. Everything before that point moves to Epic #306, specified in its own `/spec` session: the spec merge as start signal (ADR-008), refinement by the Tech Lead (ADR-009), and scheduling and board status derived from facts (ADR-010). #258 and #283 move with it. Until the new Epic delivers, the current dispatcher and status rules apply, as each of those ADRs states. The PO accepts that refinement stays manual until then, and that the end-to-end proof for #160 starts at a ready story.
**Rejected:** Keep everything in #160 — the Epic would close only when the full chain from spec merge to cleanup runs, which puts closure much further out and makes the spec about twice as large.

### Goal of #160 after the split — 2026-10-01

**Chosen:** A story that is refined and has no open hard dependency is implemented, reviewed, merged and cleaned up without the PO, apart from decision blockers, closure confirmations and Red flow-health alerts, and within the usage limits.
**Rejected:** "From spec merge to delivered Epic without the PO" — that is the combined goal of #160 and the new Epic, and it is not reachable by #160 alone after the split.

### Spike #289 stays in #160 — 2026-10-01

**Chosen:** #289 (how sessions are started) stays in #160, with the scope ADR-009 gave it: it covers refinement sessions as well as implementation sessions. Epic #306 depends on its result.
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

**Chosen:** Autonomous work leaves a reserve of the five-hour and weekly subscription allowance for the PO's own use. No new autonomous session starts when less than the reserve remains. The starting values are 10% of the five-hour limit and 20% of the weekly limit. The PO can change them easily, without a code change.
**Rejected:** "The dispatcher does not consume the PO's chat allowance" (the earlier criterion) — Claude chat and Claude Code share one allowance, so it cannot hold once sessions run on the subscription. A reserve fixed in code — the PO wants to adjust it.

### Open-source model compatibility is not an Epic criterion — 2026-10-01

**Chosen:** The earlier criterion "all OSS compatibility invariants from Epic #64 are preserved" is left out of the spec. The invariants stay working rules in `docs/PROJECT-STATUS.md` and `docs/ADAPTERS.md`, and review checks PRs against those documents.
**Rejected:** Keep it as an Epic criterion — the PO could not verify it at closure.

### Out of scope, reconfirmed — 2026-10-01

**Chosen:** The ten earlier non-goals stay out of scope, as listed under Scope → Out. One is reworded: "rolling-window budget accounting" is no longer excluded, because the reserve on the five-hour limit needs it; failover to another provider stays excluded.
**Rejected:** Bring any of them into this Epic — the PO wants none of them from #160.

### Trigger model — 2026-06-02

**Chosen:** Self-starting: work starts without the PO issuing a run command. The board status stays the trigger only until Epic #306 replaces it (ADR-010). Reconfirmed by the PO on 2026-10-01.
**Rejected:** Manual kickstart per session — it still needs PO attention for every story cycle.

### Dispatch trigger mechanism — 2026-06-02

**Chosen:** Scheduled polling every five minutes. What the poll reads changes with Epic #306. Reconfirmed by the PO on 2026-10-01.
**Rejected:** Project-event webhook — GitHub project webhooks are less reliable and harder to debug, and five minutes of latency is acceptable.

### Runner — 2026-06-02

**Chosen:** GitHub Actions runs the dispatcher (ADR-002). Where sessions run is decided by #289. Reconfirmed by the PO on 2026-10-01.
**Rejected:** An external runner for the dispatcher — it needs infrastructure the PO would have to host.

### Notification channel — 2026-06-02

**Chosen:** Slack incoming webhook. The PO answers with a GitHub comment, which is the durable record and the signal the dispatcher acts on. Reconfirmed by the PO on 2026-10-01.
**Rejected:** Slack bot — it adds OAuth and infrastructure without removing a PO step.

### Pause mechanism — 2026-06-02

**Chosen:** The repository variable `DISPATCHER_ENABLED` (`true`/`false`), checked at the start of every run. Reconfirmed by the PO on 2026-10-01.
**Rejected:** A board label — less reliable as a control surface. A Slack command — it needs a Slack bot.

### Escalations must be decidable from the notification and must not be lost — 2026-09-27

**Chosen:** A decision blocker states the question, the options and a recommendation, and a notification that needs PO action is retried when delivery fails. The response channel stays a GitHub comment. Which Feature holds which story is left to refinement. Reconfirmed by the PO on 2026-10-01.
**Rejected:** A separate Feature for this work — a structural choice that now belongs to the Tech Lead.

### Codex reviews only finished work — 2026-09-27

**Chosen:** One Codex review request when a Claude-implemented PR is marked ready, plus one per round of fixes for Codex findings, with Codex automatic review off. Reconfirmed by the PO on 2026-10-01.
**Rejected:** A review request after every push — it spends the Codex allowance on unfinished work.

### PRs with no linked story cannot clear the gate through a Codex review — 2026-09-27

**Chosen:** The approval gate reads who implemented a PR from the linked story's `executor:` label. Without a linked story, a clean Codex review does not count, and the PR needs a separate-session review or a formal approval. Reconfirmed by the PO on 2026-10-01.
**Rejected:** Reading the implementer from commit trailers — weaker, text-based evidence. A policy-only exclusion with no tested gate rule.

### ADR-005 is not adopted — 2026-09-27

**Chosen:** No owned or persistent runner host, and no failover to another provider. Reconfirmed by the PO on 2026-10-01. Two parts of ADR-005 return in another form through decisions above: sessions run on the subscription ("No metered API usage"), and the five-hour window is accounted for ("A reserve of the subscription allowance, set by the PO").
**Rejected:** ADR-005's owned Raspberry Pi host with subscription login on it and rolling-window failover — infrastructure the PO would own and maintain.

### A stop on repeated failed sessions belongs in this Epic — 2026-11-08

**Chosen:** A failure-based stop (#503) is part of #160. The PO first chose this on 2026-11-08 (recorded on #503, option A). Reconfirmed by the PO on 2026-11-08 in this spec session.
**Rejected:** The catch-all Epic, or another Epic — the stop protects the autonomous story cycle that this Epic delivers.

### The failure-based stop is a new criterion, E8 — 2026-11-08

**Chosen:** A new criterion E8. E5 stays as it is and stays delivered (Feature #308 is closed).
**Rejected:** Extend E5 — E5 would no longer count as delivered, and E5 is about limits on healthy work, not a stop on failures.

### What the failure-based stop must do — 2026-11-08

**Chosen:** After repeated failed sessions no new session starts, and the PO gets one Slack message with the failed sessions, their stories and their run links. The dispatcher starts again only after a PO action, never on its own, and a lost saved state does not restart it. The PO can change the threshold without a code change. A session stopped by a provider usage limit does not count as a failure.
**Rejected:** A threshold fixed in code — the PO wants to adjust it, as with the usage limit and the reserve. A stop that clears itself after a time or when its state is lost — the stop is there because something is wrong that needs the PO. Counting provider usage-limit stops — those are healthy pauses that E5 and E6 already handle.

### The PO's merge of a decision document is a touchpoint, and the story continues after it — 2026-11-08

**Chosen:** The Goal names the PO's own merge of a PR that changes decision documents (ADR-011) as a fourth PO touchpoint. A new criterion, E9, requires that the story then reaches `Done` and cleanup with no further blocker and no further PO action (#505).
**Rejected:** Extend E1 — E1 is proven on three ordinary stories with no PO action, and a decision-document story always needs the PO's merge. No Epic criterion, only a story under #160 — then the Goal could never hold for such a story, and nothing would check it at closure.

### A draft PR left by `/implement` is continued, not only reported — 2026-11-22

**Chosen:** The dispatcher starts `/implement` again on the existing branch and draft PR in two cases: after the PO's `/unblock` when the session failed, and after a set time when the session stopped silently after its push with a green required test check and no hand-off (#578). The PO first chose this on 2026-11-22 as Decision A on #578, and confirmed it in this spec session. It matches E4: work resumes after the PO's answer with no further PO action, and in the silent case with none at all.
**Rejected:** Report only (option b on #578) — the PO continues by hand, and the stalled draft PR keeps its WIP place and blocks stories with file overlap, as #606 blocked #503, #507 and #607 for about 16 hours. Report and free the WIP slot (option c) — it changes the documented fact that an open draft PR is In implementation and still leaves the work to the PO.

### The continuation is a new criterion, E10 — 2026-11-22

**Chosen:** A new criterion E10, placed in this Epic. E1 and E8 stay as they are.
**Rejected:** Extend E1 — E1 is proven on three ordinary stories with no PO action and no failure. Extend E8 — E8 is the stop after repeated failed sessions, not the continuation of one.

### The wait before a silent stop is continued is the Tech Lead's to set — 2026-11-22

**Chosen:** The spec requires only that a set time exists and that the PO can change it without a code change, as for the usage limit and the reserve. The value, and what counts as "no hand-off", are delegated to the Tech Lead.
**Rejected:** The PO sets the number in this session — it is a technical value that depends on how long `/implement` normally runs, which refinement can read from the run logs.

### A continued session that fails counts toward the stop in E8 — 2026-11-22

**Chosen:** A session started by the continuation counts as any other session for the stop on repeated failed sessions. A provider usage-limit stop still does not count.
**Rejected:** Leave continued sessions out of the count — a session that always fails would then restart without limit and never reach the stop.

## Checked Against

- ADR-001 (runner integration) — no conflict; superseded by ADR-002.
- ADR-002 (GitHub Actions runner) — no conflict for the dispatcher and the five-minute poll. Conflict: it has the dispatcher invoke executors through API calls, against "No metered API usage". Resolved by the ADR that #289 produces, which amends ADR-002 in its own ADR PR. Its dispatch trigger is amended by ADR-010, which takes effect with Epic #306.
- ADR-003 (interim executor session boundary) — conflict: the embedded API loop, against "No metered API usage". ADR-003 is interim and waits for #259, which was closed as not planned. Resolved by the ADR that #289 produces, which supersedes ADR-003 in its own ADR PR.
- ADR-006 (Codex Cloud review) — no conflict for Claude-implemented PRs. Conflict: it leaves the Claude Opus review of Codex-implemented stories on the dispatcher's API path. Resolved by the ADR that #289 produces, which must cover how that review is started. The ADR still says `Status: Proposed` on `main`; ADR-011 item 3 requires `Accepted`, and the change is part of the approval-gate work.
- ADR-007 (approval gate means independent review) — no conflict. Same status note as ADR-006.
- ADR-008 (Epic spec in the repository) — no conflict. This spec is the specification of #160. Using the spec merge as start signal is implemented by Epic #306.
- ADR-009 (refinement is Tech Lead work) — it amends #160's decision "Refinement execution model" (2026-06-03), its non-goal "Headless automatic Epic or Feature refinement" and its refinement touchpoint. None of the three is carried into this spec; the topic moved to Epic #306. Its widening of #289 to refinement sessions is kept.
- ADR-010 (the board shows current state) — conflict: the delivered dispatcher is triggered by board status. Resolved by the ADR's own "Effective when" clause: #160 keeps the current trigger until Epic #306 implements ADR-010. #258 and #283 moved to #306. The two #160 decisions on the Epic's board status (2026-09-27) are not carried.
- ADR-011 (decision documents approved by the PO's merge) — no conflict. This spec PR is a decision document. Item 5 (only the PO merges decision documents) is why the Goal names that merge as a PO touchpoint, and E9 covers what follows it.
- `docs/PROJECT-STATUS.md` → OSS Compatibility Invariants, and `docs/ADAPTERS.md` → Executor Routing — no conflict. "Capacity Is Abstracted For Metered And Unmetered Executors" agrees with specifying the usage limit as an outcome.
- `docs/PROJECT-STATUS.md` → Blocked Status Protocol and WIP Pre-Flight Check — no conflict.
- `docs/ADAPTERS.md` → Cross-Provider Review Pairing — the same conflict as under ADR-006, with the same resolution.
- `docs/MERGE-POLICY.md` and `docs/required-check-policy.json` — no conflict. Required checks are still declared absent for `main`; making the test check required is remaining scope.
- `docs/DISPATCHER-CONFIG.md` — it describes the daily token caps per executor type and the API-key secrets. Both are replaced by decisions in this spec, and the document is updated when that work is implemented.
- `docs/DISPATCHER-CONFIG.md` → Usage limit (checked 2026-11-08) — no conflict with E8. `USAGE_LIMIT_SESSIONS_PER_DAY` counts every session and stays. Its count resets when the cache entry is lost; E8 requires that a lost saved state does not restart the dispatcher, so E8's stop cannot rely on that cache alone.
- #503 (Story, 2026-11-08) — its wanted outcome and its placement in #160 are carried into E8 after the PO confirmed them in this session. Its questions for refinement are delegated to the Tech Lead.
- `docs/DISPATCHER-CONFIG.md` → the merged-PR path (checked 2026-11-08) — conflict with E9: a merged PR with no `Merge gate:` confirmation from the dispatcher counts `merge-confirmation-missing` and, after 3 runs, posts a blocker that needs a PO `/unblock`. A PR the PO merged by hand has no such confirmation. Resolved by E9; the document is updated when #505 is implemented.
- #505 (Story, 2026-11-08) — its wanted outcome is carried into E9 after the PO confirmed it in this session.
- #578 (Bug, 2026-11-22) and its widening comment (#606, silent stop after push) — the PO's Decision A is carried into E10 after the PO confirmed it in this session. The Tech Lead's Blocked Declaration on #578 cites "Spec #160 → F1 Out of Scope" as saying that a session leaving a draft PR keeps its draft-PR blocker. No such line exists in this spec on `main`, which does not mention draft PRs; nothing is narrowed.
- `docs/PROJECT-STATUS.md` (an open linked draft PR is the fact for In implementation; WIP pre-flight) and `docs/DISPATCHER-DECISIONS.md` (`wip_limit` / `target-in-implementation`, `draft_ready_signal`) — conflict with E10: a draft PR holds its WIP place and the in-flight rule never starts the story again (`in_flight.py`, `if draft: continue`). Resolved by E10; the documents and the rule are updated when #578 is implemented. The fact itself stays: a draft PR with a session at work is In implementation.
- `docs/DISPATCHER-CONFIG.md` → the `--in-implementation` lists and the review step — the same conflict, with the same resolution.
- E1 and E4 — no conflict. A failed session's blocker needing the PO's `/unblock` is existing dispatcher behaviour; E10 adds no PO touchpoint to the Goal. E8's stop is extended to continued sessions by a decision above.
- #160 decision "Token budget granularity" (2026-06-02) — replaced by "Usage limits: only the outcome is specified".
- #160 decisions "Feature #270 after the Raspberry Pi migration was dropped" and "Shared-file ordering never makes runnable work wait" (2026-09-27) — not carried as decisions; Feature structure and ordering are delegated to the Tech Lead.
- #160 success criterion on OSS compatibility invariants — left out; see Decisions.
- Epic #64 decisions (WIP limit of two with no file overlap; only the PO resolves decision blockers) — no conflict.

## Delegated to the Tech Lead

- Slicing into Features and stories, including whether the existing Features (#267, #268, #269, #270, #288) and their issues are kept, regrouped or closed.
- Ordering, including shared-file ordering.
- The technical approach for each scope item.
- The unit and granularity of the usage limit, after #289.
- Where the usage limit and the reserve are configured, provided the PO can change them without a code change.
- Executor and model choice per issue.
- For E8: what counts as a failed session (apart from the provider usage-limit stop, which never counts), whether failures are counted in a row or within a time window, the starting threshold, where the setting and the failure count are kept, which PO action starts the dispatcher again, and whether failed `/merge` and `/cleanup` scripts count.
- For E9: how the dispatcher tells the PO's merge of a decision-document PR apart from other merges it did not confirm.
- For E10: the wait before a silent stop is continued, where it is set so the PO can change it without a code change, what counts as "no hand-off", how the dispatcher tells a draft PR with a session at work from one left behind, whether the continued session resumes on the same branch by a new `/implement` or by another command, and how the WIP count treats a draft PR left behind until it is continued.

Not delegated: if #289 finds that a script cannot read the remaining subscription allowance, or that no mechanism meets E5 and E6 together, that is a decision blocker for the PO.

## Dependencies

- Epic #64 (status model, command contracts) — complete.
- External: the PO's Claude and ChatGPT subscriptions, Codex Cloud review, GitHub Actions and the Slack incoming webhook.
- Epic #306 depends on #289 from this Epic. #160 does not depend on #306.

## Open Questions

None.
