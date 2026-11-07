Act as the Tech Lead (`docs/ROLES.md`) with GitHub access.

Refine the following GitHub task issue until it meets the project's refinement standard.

Repository:
LauraMardones/headless-pr-workflow

Issue:
#$ARGUMENTS

Do not implement code.

Follow the `## Tech Lead rules` in `.claude/commands/refine.md`: spec lookup and check, no refinement without a merged spec, the spec record, decision blockers instead of questions to the PO, resume after `/unblock`, lens questions and usage findings. Follow them before any issue write. Ask the PO nothing in the session.

## Core Instructions

- Fetch the current GitHub issue title, body, labels, milestone, parent epic or feature, and relevant comments.
- Inspect relevant repository docs before changing the issue.
- Update the GitHub issue body directly with the refined task.
- Do not only return the refined task in chat.
- GitHub must remain the source of truth.
- Preserve useful existing content, but reorganize it into the project task format.
- Do not discard prior decisions, constraints, dependencies, or context unless clearly obsolete.
- If something appears obsolete, move it to Notes instead of deleting it silently.
- If you do not have permission or tooling to update GitHub, stop and say exactly what is missing.

## Status Workflow

- `Backlog`: not actively prepared.
- `In refinement`: refinement is in progress.
- `Refined`: task is clear but hard dependencies may still block implementation.
- `Ready for implementation`: an implementer can pick it up immediately.
- `Done`: delivered and closed.

When starting: move status to `In refinement`.
When complete: move to `Refined` or `Ready for implementation`.

## Refinement Standard

A task is technical work without a direct user-facing story format. It does not require a User Story or Business Value section.

Refinement is complete when the task has:

- clear Goal (what must be done and why)
- Scope
- Out of Scope
- Acceptance Criteria (technical and verifiable)
- Epic and Feature Requirements Mapping (see the section of that name below)
- the spec record line and a `Feature group: #<n>` line
- Dependencies (Hard / Soft)
- Assumptions
- Risks / Blockers
- Open Questions, if any
- Technical Notes with relevant files, commands, or conventions
- Test Expectations (if the task affects testable behavior)
- Documentation Impact
- Definition of Done
- clear command surface if the task affects the CLI

## Epic and Feature Requirements Mapping

Every refined Task carries a `## Epic and Feature Requirements Mapping` section, so the implementer builds and the reviewer judges the same scope.

- Read the parent Epic's merged `specs/<n>-*/spec.md` (success criteria, requirements) as the approved scope (ADR-008), and the parent Feature's issue body. Use the Epic issue body only as a legacy fallback when the Epic has no spec; never take requirements from it when a spec exists, because it is only an index.
- List each Epic or Feature requirement that applies to the task, each as exactly one of:
  - covered by an acceptance criterion: reference the criterion's number (`AC 2`);
  - an entry under `Not in this task` with a reason: `deferred to #N` when another issue will deliver it, or `not applicable` with the reason.
- Trace both ways (ADR-009): every acceptance criterion of the task must appear in the mapping and name the Feature criterion it serves (`F1.1`); a task criterion without a Feature parent, or mapped directly to an Epic criterion, adds scope and is not allowed. Epic criteria are reached through the Feature criteria.
- Point to the requirement in one line; do not restate the Epic spec in the task.
- Record a deliberately uncovered Epic requirement under `Not in this task`, so a reviewer does not rediscover it as a blocker.
- A deferred requirement must link an existing follow-up issue (`deferred to #N`). If none exists, create the follow-up issue; if you cannot, raise a `Type: decision` Blocked Declaration. The follow-up issue must be linked before the task moves to `Ready for implementation`.
- Cross-cutting check: time bounds, delivery guarantees and similar statements an executor needs must be copied into the task's Scope or Acceptance Criteria, not only referenced in the mapping.
- Split check: when the task adds a new script, a workflow step and docs together, record a `Split decision:` line in `## Decisions` (`Split decision: split into #A, #B` or `Split decision: keep together because ...`).

## Open Questions Format

Questions for the PO are not written here for the PO to answer in the session. A product question becomes a `Type: decision` Blocked Declaration posted on the issue (`.claude/commands/refine.md` → Tech Lead rules → Decision blockers), after the search for an existing answer in `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections. If an existing answer is found, record and follow it instead of escalating. The affected acceptance criterion stays unchanged while the blocker is open.

The `## Open Questions` section of the issue body lists the link to each blocker comment while the issue is blocked, and reads `None.` before it moves to `Ready for implementation` (`scripts/refine-check` fails `open_questions` otherwise).

## Decisions Format

When a decision blocker is answered by the PO's `/unblock` reply, or the Tech Lead settles a technical or delegated choice (label, milestone, executor, slicing, technical approach), record it under Decisions with the chosen option, rejected alternatives, date and comment link (when answered by the PO). Do not delete rejected options — they are part of the record. If a decision reverses a prior decision, add a new entry rather than editing the old one.

Each decision must be structured as:

```md
### [Decision title] — YYYY-MM-DD
**Chosen:** [Option name] — [brief reason in plain language]
**Rejected:** [Option name] — [brief reason why not]
```

## Label Handling

- Use only existing GitHub labels.
- Apply `type:task` if not already set.
- If priority, area, or milestone is unclear, the Tech Lead chooses among the existing values and records the choice under `## Decisions`; a choice that needs a product decision becomes a `Type: decision` Blocked Declaration.

## Milestone Handling

- Use an existing milestone only when delivery timing is clear.
- Otherwise leave milestone unchanged and record the decision under `## Decisions`; a milestone that needs a product decision becomes a `Type: decision` Blocked Declaration.

## Dependency Handling

- Hard dependencies block implementation, not necessarily refinement.
- If a dependency may change scope or technical approach, state the assumption explicitly.
- If revalidation is needed after a dependency closes, write: `Revalidate after dependency closes: yes`

## Issue Body Format

Update the issue body to this structure:

```md
Parent epic: #{EPIC_NUMBER} {EPIC_TITLE}
Feature group: #{FEATURE_NUMBER} {FEATURE_TITLE}
Critical path: yes/no
PO status: {In refinement | Refined; blocked by #X | Ready for implementation.}
Spec: [`specs/{EPIC_NUMBER}-{slug}/spec.md`]({blob link at the commit}) at `{commit}`

## Goal

What must be done and why.

## Scope

- ...

## Out of Scope

- ...

## Acceptance Criteria

- [ ] ...

## Epic and Feature Requirements Mapping

- {Feature criterion or requirement, one line} → AC {n}
- AC {n} serves {Feature criterion, e.g. F1.1} (every AC is listed)

Not in this task:

- {requirement} — deferred to #N: {reason}
- {requirement} — not applicable: {reason}

## Command Surface

If relevant:

- `hpw <command> <target> --repo <owner/repo>`
- flags: ...
- exit codes: ...

## Output Contract

If relevant:

JSON output includes: ...
Human-readable output includes: ...

## Test Expectations

- ...

## Documentation Impact

- ...

## Dependencies

Hard depends on:

- #X ...

Soft depends on:

- #Y ...

## Dependency Contract Assumed

If relevant: ...
Revalidate after dependency closes: yes/no

## Assumptions

- ...

## Risks / Blockers

- ...

## Open Questions

- <link to each decision blocker comment, or `None.`>

## Decisions

...

## Technical Notes

- Relevant files:
  - `src/...`
  - `tests/...`
- Reuse existing helpers: ...
- Avoid: ...

## Definition of Done

- ...
```

## Refinement Completeness Gate (mandatory)

The last step of every refinement, after the board write, is the deterministic check; the model is not the judge of its own completeness.

- Write the board status with `scripts/set-project-status --issue <n> --status "<Status name>" --repo LauraMardones/headless-pr-workflow` and require exit `0`: that exit is the board read-back. A non-zero exit means the board was not confirmed; report it, never claim the status.
- Then run `python3 scripts/refine-check --issue <n> --repo LauraMardones/headless-pr-workflow`. It re-reads the issue and the board and checks the `type:`, `executor:` and `prio:` labels, the body `PO status:` against the board Status, open hard dependencies, the required body sections, open questions and parent references.
- Run it on every refinement, including a re-refinement of an issue that is already `Ready for implementation`.
- Only exit `0` permits reporting `Refined` or `Ready for implementation`. Exit `1` (a named check failed) or exit `2` (the issue, labels, dependencies or board could not be read) forbids it.
- On a non-zero exit after the board write: fix what can be fixed and re-run the check; if a failure remains, move the board back with `scripts/set-project-status --issue <n> --status "In refinement" --repo LauraMardones/headless-pr-workflow`, set `PO status: In refinement` in the issue body, and list the failing checks as blockers, so the body and the board never claim a status the check did not confirm.
- `executor:` label rule: a `type:story`, `type:task` or `type:bug` issue carries exactly one existing `executor:` label (`docs/PROJECT-STATUS.md`); a `type:epic` or `type:feature` issue needs none. Apply it before the check, choosing among the repository's existing labels.
- Any statement about labels or board status in the reply or the Session Summary comes from the check output, not from memory or from the issue text.
- The Session Summary `Checks run:` line carries the result: `refine-check — pass`, or `refine-check — fail: <check-ids>` / `refine-check — unreadable`. `none` is not valid for a refinement that reports a status.

## Final Reply Format

After updating GitHub, reply only with:

- Issue URL
- Final PO status: Refined, Ready for implementation, or blocked/not ready
- Summary of what changed
- Labels changed
- Milestone changed
- Remaining hard dependencies
- Remaining Open Questions
