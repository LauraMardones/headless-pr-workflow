# Epic #306: Tech Lead: from spec merge to ready stories without the PO

**Epic issue:** #306
**Milestone:** 6 — Autonomous Execution

## Goal

After the PO merges a spec PR, the Epic is broken into Features and stories, and stories become ready to implement, with no PO action apart from decision blockers. The Tech Lead's scheduler derives every next action in the chain, and the board status, from GitHub facts. The board no longer starts any work.

Today the PO starts every refinement by hand, the refine commands ask the PO product questions mid-session, and moving a board card is enough to start work. ADR-008 to ADR-010 decided the change on 2026-09-28, but apart from #258 and #283 no issue tracks it, so those ADRs are accepted and not in effect.

## Scope

In:

- Start signal: the Tech Lead notices a merged spec PR, for a new spec or a scope change, and starts the Epic-to-Feature breakdown with no PO action.
- Rewritten `/refine*` commands: a tech-lead role that reads the spec at a known commit, makes no product decisions, raises a decision blocker for a specification defect instead of asking the PO, records the spec commit it refined against, and moves no board card.
- Traced acceptance criteria: Feature criteria name the Epic criteria they serve, and story criteria name their Feature criteria. A script checks that every Epic criterion is covered and that no criterion lacks a parent.
- Just-in-time story refinement: the Tech Lead keeps 3–5 stories ready to implement and refines no further ahead. The big-bang exception in `docs/REFINEMENT-PIPELINE.md` is removed.
- Order between Epics: the PO sets a priority label on an Epic and the scheduler follows it. Equal priority falls back to the earlier spec merge.
- Re-refinement after a scope change: affected Features and stories are re-refined before any of them is implemented further.
- The scheduler: a script, with no model, chooses the next action from hard dependencies, priority, queue limits, the WIP limit of two and file overlap. It replaces the board trigger in the dispatcher, including for starting implementation.
- The board shows facts: only the scheduler writes status, it overwrites manual moves, and the intent statuses are removed or renamed. `/implement`, `/review`, `/merge` and `/cleanup` stop moving cards.
- The Epic issue as index: the Tech Lead keeps it linked to the spec and showing progress.
- No refinement under an Epic with no merged spec. Stories already refined under such an Epic can still be implemented.
- A usage finding that affects scope or an acceptance criterion becomes a decision blocker. Any other finding is noted.
- `/verify-closure` checks an Epic against its spec's Epic criteria. For the checks only the PO can perform, it gives the PO the exact steps and what to expect.
- The `/refine*` commands stay invocable by hand, under the same rules.
- `docs/PROJECT-STATUS.md` (fact and intent, transitions, recovery) and `docs/REFINEMENT-PIPELINE.md` (pipeline shape, escalation triggers) are brought in line.
- #258 and #283 are re-refined against ADR-010.
- The "ready for refinement" Slack notification is retired.
- Refinement sessions respect the usage limit, the reserve and the pause switch from #160.
- An end-to-end proof from a spec merge to ready stories.

Out:

- The cycle from a ready story to cleanup, which is #160. Only its trigger and its status display change here.
- How sessions are started (#289 in #160).
- The `/spec` command and the specification phase itself (#302 under #173).
- Writing the specs for #173, #236 and #272.
- Product decisions in refinement, and removing decision blockers as a human touchpoint.
- Changing the values of the ready buffer (3–5) or the WIP limit (two).
- A dashboard that replaces the board.
- Pre-delivery intake (#236).

## Acceptance Criteria

The proof Epic is the first Epic whose spec merges after this Epic's work is in place. "Closure evidence" is the evidence comment that `/verify-closure` posts for #306. It also gives the PO step-by-step instructions for the three checks the PO performs by hand (E3, E5, E7).

- **E1:** After a spec PR merges, the Epic's Features exist and 3–5 stories are ready to implement with no PO action, when no decision blocker arises. Nothing is refined under an Epic with no merged spec.
  - **Verified by:** closure evidence showing that the proof Epic's issue timelines contain no PO comment or command between the spec merge and the stories being ready, and that an Epic without a merged spec had no refinement activity in the same period, with the scheduler naming the missing spec as the reason.
- **E2:** Every Feature criterion and every story criterion names the parent criterion it serves, and every Epic criterion is covered.
  - **Verified by:** closure evidence with the coverage check's output for the proof Epic, showing no uncovered Epic criterion and no criterion without a parent. The PO follows one chain from a story criterion up to an Epic criterion.
- **E3:** Refinement makes no product decision. What the spec does not settle reaches the PO as a decision blocker, and refinement resumes after the PO's answer.
  - **Verified by:** the PO posts a usage finding that contradicts a criterion of a story not yet refined. The next refinement raises a decision blocker and leaves the criterion unchanged, and it continues after the PO's `/unblock` comment.
- **E4:** A story becomes ready to implement only when it is refined and every hard dependency is closed. The ready buffer holds 3–5 stories while unrefined work remains.
  - **Verified by:** closure evidence from the proof Epic showing a refined story becoming ready on the next scheduler run after its last hard dependency closed, and a buffer that never exceeded five and was refilled when it dropped below three.
- **E5:** Moving a board card starts no work, and the board is corrected. The pause switch stops new refinement.
  - **Verified by:** the PO drags an unrefined item to the ready status; no session starts and the next scheduler run restores the status. Closure evidence shows a scheduler run with `DISPATCHER_ENABLED=false` that started no refinement session.
- **E6:** The board and the Epic issue show the current state: the stage of each implementation issue, and how much of each Feature and Epic remains.
  - **Verified by:** closure evidence comparing the board status of the proof Epic's issues with the GitHub facts, with no difference. The proof Epic's issue links to its spec and shows progress.
- **E7:** After a scope-change spec PR merges, the affected Features and stories are re-refined before any of them is implemented further.
  - **Verified by:** the PO merges one scope-change spec PR on the proof Epic. The affected issues then show a refinement record naming the new spec commit, and none of them started implementation in between.
- **E8:** Epic closure is checked against the spec's Epic criteria.
  - **Verified by:** closure evidence for #306 that lists every criterion of this spec with its evidence.

## Decisions

### The whole scheduler belongs to this Epic — 2026-10-01

**Chosen:** #306 delivers the complete ADR-010 scheduler. It replaces the board as trigger for the entire chain, including starting implementation and showing the review, merge and done states. This matches the #160 spec, which keeps the board trigger only until #306 replaces it.
**Rejected:** Only up to "ready to implement" — implementation would keep the board trigger, the rest of ADR-010 would have no home, and the #160 spec would need a correction.

### Milestone — 2026-10-01

**Chosen:** Milestone 6 — Autonomous Execution, the milestone of #160, which this Epic was split from.
**Rejected:** M7 Pre-Delivery Intake — it is about what happens before delivery items exist. A new milestone — not needed.

### Epics without a merged spec get no new refinement — 2026-10-02

**Chosen:** The Tech Lead refines nothing under an Epic that has no merged spec. This applies to the Epics created before ADR-008 (#173, #236, #272). Stories already refined under those Epics can still be implemented. To continue such an Epic, the PO runs `/spec` with its issue number.
**Rejected:** Refine old Epics from their issue body — the issue body would become a source of scope again, which ADR-008 rules out. Stop all work under old Epics, including refined stories — it would halt #236's refined stories until its spec is written.

### Epic closure against the spec is part of this Epic — 2026-10-02

**Chosen:** `/verify-closure` checks an Epic against its spec's Epic criteria (ADR-009), and that change is delivered by #306.
**Rejected:** Leave it to Epic #272 — #306 would deliver traced criteria that nothing checks at closure.

### A usage finding that affects scope or a criterion is a decision blocker — 2026-10-02

**Chosen:** When refinement meets a usage finding that affects scope or an acceptance criterion, it raises a decision blocker for the PO. An answer that changes scope goes into a spec PR. A finding that affects neither is noted and refinement continues.
**Rejected:** Refinement incorporates the finding itself — it would let refinement change what was specified, against ADR-009. Leave usage findings out of #306 — findings would reach no story unless the PO wrote a spec PR unprompted.

### Refinement can still be started by hand — 2026-10-02

**Chosen:** The `/refine*` commands stay invocable by hand, for example when the scheduler is paused. A manual run follows the same tech-lead rules and makes no product decisions.
**Rejected:** Only the Tech Lead starts refinement — the PO would have no fallback when the scheduler or session start is down.

### The proof Epic is not named in advance — 2026-10-08

**Chosen:** The criteria are verified on the first Epic whose spec merges after this Epic's work is in place.
**Rejected:** Name #236 or #272 now — closure of #306 would depend on the PO writing that particular spec. A small purpose-made test Epic — it proves the chain on artificial scope and leaves throwaway issues.

### Closure verification guides the PO through the hands-on checks — 2026-10-08

**Chosen:** `/verify-closure` gives the PO step-by-step instructions for every check the PO performs by hand, including what to post and what to expect.
**Rejected:** Leave the PO to work the steps out from the **Verified by:** lines — the PO asked to be prompted.

### Eight Epic criteria, three of them checked by hand — 2026-10-08

**Chosen:** Eight criteria. The PO acts by hand for three (E3, E5, E7) and reads evidence for the rest. "No refinement without a spec" is part of E1, and the pause switch is part of E5. "The scheduler uses no model" and "refinement respects the usage limit and the reserve" stay in scope and are not Epic criteria: the first is an ADR-010 rule checked in review, and #160's E5 verifies the limits for every session.
**Rejected:** Ten criteria with five hands-on checks — too much for the PO to verify at closure.

### Status names are delegated — 2026-10-08

**Chosen:** The Tech Lead chooses the board's status names and the fact behind each, bound by ADR-010: every status describes a state, and the PO can read each issue's stage and what remains of each Feature and Epic.
**Rejected:** The PO decides the status list — the PO sees the result on the board and in `docs/PROJECT-STATUS.md` and does not need to choose the names.

### The PO orders Epics with a priority label — 2026-10-08

**Chosen:** When several Epics have a merged spec, the scheduler serves them by the priority label the PO sets on the Epic. Equal priority falls back to the earlier spec merge. A label is a fact, so this fits ADR-010.
**Rejected:** Earlier spec merge first, always — the PO could not let a later Epic overtake. Delegate the ordering between Epics to the Tech Lead — the PO would not steer between Epics.

### No big-bang refinement — 2026-10-08

**Chosen:** Story refinement is always just-in-time against the ready buffer of 3–5. The big-bang exception in `docs/REFINEMENT-PIPELINE.md` is removed. A small Epic simply has all its stories in the buffer at once.
**Rejected:** Keep big-bang refinement as a choice a spec can make — one more mode to build and test.

## Checked Against

- ADR-001 (runner integration) — no conflict; superseded by ADR-002.
- ADR-002 (GitHub Actions runner) — no conflict for the runner and the five-minute poll. Its dispatch trigger (the poll acts on board status) is amended by ADR-010, and this Epic implements that amendment.
- ADR-003 (interim executor session boundary) — not affected; how sessions run is decided by #289 in #160.
- ADR-006 and ADR-007 (Codex review, approval gate) — no conflict. The scheduler derives "mergeable" from these gates and does not change them.
- ADR-008 (Epic spec in the repository) — no conflict. This Epic delivers what its "Effective when" clause waits for: the refine commands and the decision pre-check read the spec, the spec merge is the start signal (item 4), and the Epic issue becomes an index (item 7). Whether delivered specs are kept as living documentation is not decided here.
- ADR-009 (refinement is Tech Lead work) — no conflict. This Epic delivers its items 1–7 and the `/verify-closure` consequence. Its "Effective when" clause also waits for #289.
- ADR-010 (the board shows current state) — no conflict. This Epic implements it in full. The ADR leaves the exact fact behind each status, and how Epics created before ADR-008 are shown, to `docs/PROJECT-STATUS.md`; the decision "Epics without a merged spec get no new refinement" settles the behaviour, and the display is delegated.
- ADR-011 (decision documents approved by the PO's merge) — no conflict. This spec PR is a decision document.
- `docs/PROJECT-STATUS.md` — conflict in several sections, all resolved by rewriting them in this Epic: Fact vs Intent and the transition table (intent statuses authorize work); Recovery Protocol (rolls status back); Blocked Status Protocol → Resolution (status moved back by hand, and `/unblock` returns a story to a trigger status); "In refinement" and File Ownership Tagging (refinement done by the PO); Rolling Refinement ("refine story N+1 while implementing story N"), which ADR-009 item 6 replaces with the ready buffer. The decision-blocker rules, the WIP limit of two and the file-overlap rule are kept.
- `docs/REFINEMENT-PIPELINE.md` — conflict: the pipeline starts at `spec-kit → gh issue create`, the escalation triggers go to the Human Approver in the session, and big-bang refinement is allowed. Resolved by rewriting the document in this Epic: the pipeline starts at the spec merge, the triggers become decision blockers, and the big-bang exception is removed (see Decisions). The buffer of 3–5 and the usage-feedback loop are kept.
- `docs/ADAPTERS.md` → Executor Routing — conflict: the `executor:` label is applied when a status changes. Resolved in this Epic: the label is set in refinement, which is delegated.
- `docs/DISPATCHER-CONFIG.md` and `docs/commands/project-status-sync.md` — they describe the poll reading board status and a status sync that only reports. Both are updated when the scheduler is implemented.
- `docs/ROLES.md` → Tech Lead, and `specs/README.md` → Definition of Ready and Acceptance Criteria Levels — no conflict.
- `docs/MERGE-POLICY.md`, `docs/HEADLESS-PR-WORKFLOW.md`, `docs/WORKTREE-MODEL.md` and `docs/DELIVERY-BASELINE.md` — no conflict. The delivery baseline's states are already separate from board status.
- `AGENTS.md` → GitHub Operations — it describes board status writes by the commands as best-effort. Updated when the commands stop writing status.
- #160 spec, decisions "Trigger model", "Dispatch trigger mechanism", "Pause mechanism" and "Notification channel" — no conflict. They name #306 as the point where the trigger and what the poll reads change.
- #160 spec, Scope and E3 — the "ready for refinement" notification stays until #306 retires it. If #160 is still open at that point, its E3 names an event that no longer occurs, and #160 needs a scope-change spec PR.
- #160 spec, Out: "Replacing the manually invoked command contracts" — no conflict. This Epic changes what the commands write; they stay invocable by hand.
- Epic #64 decisions (WIP limit of two with no file overlap; only the PO resolves decision blockers) — no conflict.
- Epic #173 (rolling, just-in-time refinement as normative; Product Validator findings as workflow inputs) — no conflict. The big-bang exception that came with it is removed by a decision in this spec.
- #258 and #283 — both assume the board is a trigger, and #283 cites ADR-005, which was not adopted. Their bodies are input only; both are re-refined against ADR-010 and this spec.
- #257 and PR #299 (de-duplication of the "ready for refinement" notification) — not affected until this Epic retires that notification.

## Delegated to the Tech Lead

- Slicing into Features and stories, and whether #258 and #283 are kept, rewritten or closed after re-refinement.
- Ordering within the Epic, including shared-file ordering.
- The technical approach for each scope item, including whether `scripts/dispatcher-poll.sh` and `scripts/project-status-sync.sh` are extended or replaced.
- The board's status names and the fact behind each, including how Epics, Features and Epics without a merged spec are shown, within ADR-010.
- The scheduler's working rules other than the Epic priority label, the ready buffer of 3–5 and the WIP limit of two: how priority is applied within an Epic, and tie-breaks.
- How a spec merge and a scope change are detected, and how "affected" Features and stories are determined for re-refinement.
- The form of the refinement record, where the spec commit is recorded, and how a criterion names its parent.
- The form of the Epic issue as index.
- What becomes of the refinement lenses, which today put questions to the PO.
- The form of the step-by-step instructions `/verify-closure` gives the PO.
- Executor and model choice per issue and per refinement session.

Not delegated: if a status cannot be derived from GitHub facts without stored state (the "Revisit" case in ADR-010), or if the mechanism from #289 cannot start refinement sessions, that is a decision blocker for the PO.

## Dependencies

- #289 in Epic #160 (how sessions are started). Starting refinement without the PO depends on its result. The rewritten commands, the scheduler and the board do not.
- Epic #160: the decision-blocker protocol, `/unblock` resume, the Slack notifications, the usage limit, the reserve and the pause switch. #160 does not depend on #306.
- The `/spec` command (#302 under Epic #173) — delivered.
- The proof needs one further Epic spec, written by the PO with `/spec` after this Epic's work is in place.
- External: the GitHub Projects v2 API and a token that can write the board (`PROJECT_TOKEN`).

## Open Questions

None.
