# Epic #566: Every comment that needs the PO names the next command

**Epic issue:** #566
**Milestone:** M6 Autonomous Execution

## Goal

When the automatic flow stops and the PO must act by hand, the GitHub comment that says so names the PO's next step: the exact command with its issue or PR number, where to run it, and what happens afterwards. Today many such comments say what is wrong but not what to do. On #545 the Session Summary said "Needs PO decision" after the PO had decided, and the next step (a spec change) was not named. On #535 a blocker said "fixed by hand" without a command. The PO then asks an agent to read the issue, which happens very often. After this Epic, the PO acts on the comment alone.

## Scope

In:

- Every comment that asks the PO to act: `## Blocked Declaration` of all five types, whoever posts it (dispatcher, a session, the review bookkeeping job).
- The `Next action:` line of a `## Session Summary` when the next step is the PO's.
- Every Slack notification that asks the PO to act, for example `po_blocker`, `decision_blocker` and the closure requests.
- The `/verify-closure` comment that asks the PO to confirm.
- Each of these names three things: the command with its number, where to run it, and what happens afterwards.
- A decision blocker names the next step for each option.

Out:

- When the flow stops, which blocker types exist, and the merge and review gates.
- The flow acting on the PO's behalf. The comment tells the PO what to do; it does not do it.

## Acceptance Criteria

- **E1:** Every kind of comment in Scope names the PO's command with its issue or PR number, where to run it (Claude Code, Codex, or a reply on GitHub), and what happens afterwards.
  - **Verified by:** a list of every such comment kind with one real or test example of each, and a test per kind that fails when one of the three parts is missing.
- **E2:** A decision blocker names the next step for each of its options.
  - **Verified by:** the #545 case: option a) names `/spec 418` and where to run it.
- **E3:** When the PO has answered and the next step is still the PO's, the next Session Summary names that step instead of "Needs PO decision".
  - **Verified by:** the #545 case replayed: after `Decision a` and `/unblock`, the summary names `/spec 418`.
- **E4:** A new comment kind that asks the PO to act cannot be added without the three parts.
  - **Verified by:** a check in the test suite that fails on a new kind that lacks a part.
- **E5:** The PO handles manual stops from the comment alone, without asking an agent to read the issue.
  - **Verified by:** the PO's own check of the first 3 manual stops after the last Feature merges.

## Decisions

### Which comments are in scope — 2026-11-15

**Chosen:** Every comment and notification that asks the PO to act. Refinement orders the work, with blockers and `Next action:` first.
**Rejected:** Only blockers and `Next action:`, with Slack and other comments later — the PO meets the problem very often, in every kind of comment.

### What a next step names — 2026-11-15

**Chosen:** Three parts: the command with its issue or PR number, where to run it, and what happens afterwards.
**Rejected:** More parts — the PO judged the three enough.

### Decision blockers — 2026-11-15

**Chosen:** Each option names its own next step.
**Rejected:** One `Resume instruction` for the whole blocker, as today — it does not say what follows each answer (#545).

### How E5 is measured — 2026-11-15

**Chosen:** The first 3 manual stops after the last Feature merges.
**Rejected:** 5 or 10 stops; no usage criterion at all.

### Milestone — 2026-11-15

**Chosen:** M6 Autonomous Execution. A new milestone split is a separate decision in #569.
**Rejected:** Decide the milestone split in this spec — it affects many Epics outside this one.

## Checked Against

- ADR-010 — no conflict. Commands record facts in comments; the next step is text for the PO. No script reads `Next action:` to set a status (only `hpw` prints it).
- ADR-008, ADR-009, ADR-011 — no conflict.
- `docs/PROJECT-STATUS.md` → Declaration format — conflict: `Resume instruction` is "n/a" for four of the five types. Resolved: this Epic changes the format, and the Feature that does so updates the document.
- `docs/TAKEOVER-RULES.md` — no conflict. It is the source for the next step when the PO takes over a merge.
- `AGENTS.md` → Codex Review Requests — constraint: a next step never writes the Codex review trigger phrase in a comment. It names the command that posts it.
- Epic #519 and Epic #272 — no conflict in scope. #519 changes the same dispatcher scripts (see Dependencies).

## Delegated to the Tech Lead

- Slicing into Features and Stories, and their order.
- The list of comment kinds in scope, by this rule: a comment is in scope when the flow waits for the PO.
- Field names and exact wording, for example a new field or a changed `Resume instruction`.
- The technical approach, for example one shared source for next-step text, and how E4's check finds new comment kinds.

## Dependencies

- Hard: none.
- Soft: Epic #519 changes the same dispatcher scripts. The scheduler's file-overlap rule keeps the two from colliding.

## Open Questions

None.
