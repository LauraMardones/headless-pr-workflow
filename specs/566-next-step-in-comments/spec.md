# Epic #566: Every comment that needs the PO names the next command

**Epic issue:** #566
**Milestone:** <milestone>

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

- **E1:** <An outcome the PO can verify at Epic closure.>
  - **Verified by:** <How the PO checks it at closure, e.g. a usage scenario, a measurement, or a named test.>
- **E2:** <…>
  - **Verified by:** <…>

## Decisions

### <Decision title>

**Chosen:** <…>
**Rejected:** <alternative> — <why>

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
