Act as the Tech Lead (`docs/ROLES.md`) with GitHub access.

Refine the following GitHub bug issue until it meets the project's refinement standard.

Repository:
LauraMardones/headless-pr-workflow

Issue:
#$ARGUMENTS

Do not implement code.

Follow the `## Tech Lead rules` in `.claude/commands/refine.md`: spec lookup and check, no refinement without a merged spec, the spec record, decision blockers instead of questions to the PO, resume after `/unblock`, lens questions and usage findings. Follow them before any issue write. Ask the PO nothing in the session.

## Core Instructions

- Fetch the current GitHub issue title, body, labels, milestone, linked epic, task lists, dependencies, and relevant comments.
- Inspect relevant repository docs before changing the issue.
- Update the GitHub issue body directly with the refined bug report.
- Do not only return the refined report in chat.
- GitHub must remain the source of truth.
- Preserve useful existing issue content, but reorganize it into the project bug format.
- Do not discard prior decisions, constraints, dependencies, or context unless clearly obsolete.
- If something appears obsolete, move it to Notes instead of deleting it silently.
- If you do not have permission or tooling to update GitHub, stop and say exactly what is missing.

## Outcome

Refinement writes no board status and no status line in the issue body. The outcome is a fact in the Session Summary, as `Next action:`:

- `Next action: Ready for implementation`: the contract is clear, `refine-check` passed and no hard dependency is open.
- `Next action: Refined; blocked by #X`: the contract is clear and `refine-check` passed, but hard dependencies are still open.
- `Next action: Needs PO decision`: a decision blocker is declared on the issue.

Never report `Ready for implementation` while hard dependencies are still unresolved.

## Refinement Standard

Refinement is complete when the issue has:

- clear Goal (what the fix must achieve)
- clear Defect Description with steps to reproduce, expected behavior, and actual behavior
- Severity and Impact assessment
- Environment information
- Scope of the fix
- Out of Scope
- testable Acceptance Criteria (the bug no longer reproduces + regression test exists)
- Epic and Feature Requirements Mapping (see the section of that name below)
- the spec record line and a `Feature group: #<n>` line
- Dependencies split into Hard depends on and Soft depends on
- Assumptions
- Risks / Blockers
- Open Questions, if any remain
- Technical Notes with relevant files, commands, or policy surfaces
- Test Expectations including regression test requirement
- Documentation Impact
- Definition of Done
- clear command surface if the bug affects the CLI
- clear JSON output, human-readable output, and exit-code behavior if it affects a command

## Epic and Feature Requirements Mapping

Every refined Bug carries a `## Epic and Feature Requirements Mapping` section, so the implementer builds and the reviewer judges the same scope.

- Read the parent Epic's merged `specs/<n>-*/spec.md` (success criteria, requirements) as the approved scope (ADR-008), and the parent Feature's issue body. Use the Epic issue body only as a legacy fallback when the Epic has no spec; never take requirements from it when a spec exists, because it is only an index.
- List each Epic or Feature requirement that applies to the bug, each as exactly one of:
  - covered by an acceptance criterion: reference the criterion's number (`AC 2`);
  - an entry under `Not in this bug` with a reason: `deferred to #N` when another issue will deliver it, or `not applicable` with the reason.
- Trace both ways (ADR-009): every acceptance criterion of the bug must appear in the mapping and name the Feature criterion it serves (`F1.1`); a bug criterion without a Feature parent, or mapped directly to an Epic criterion, adds scope and is not allowed. Epic criteria are reached through the Feature criteria.
- Point to the requirement in one line; do not restate the Epic spec in the bug.
- Record a deliberately uncovered Epic requirement under `Not in this bug`, so a reviewer does not rediscover it as a blocker.
- A deferred requirement must link an existing follow-up issue (`deferred to #N`). If none exists, create the follow-up issue; if you cannot, raise a `Type: decision` Blocked Declaration. The follow-up issue must be linked before the bug moves to `Ready for implementation`.
- Cross-cutting check: time bounds, delivery guarantees and similar statements an executor needs must be copied into the bug's Scope or Acceptance Criteria, not only referenced in the mapping.
- Split check: when the bug adds a new script, a workflow step and docs together, record a `Split decision:` line in `## Decisions` (`Split decision: split into #A, #B` or `Split decision: keep together because ...`).

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

- Inspect existing repository labels first.
- Use only existing GitHub labels from the repository.
- Do not invent new labels.
- Apply existing labels for type (bug), priority, area, flow, and status when the correct label is clear.
- If the correct existing label is unclear, the Tech Lead chooses among the existing labels and records the choice under `## Decisions`; a choice that needs a product decision becomes a `Type: decision` Blocked Declaration.

## Milestone Handling

- Use an existing milestone only when delivery timing is clear.
- Otherwise leave milestone unchanged and record the milestone decision under `## Decisions`; a milestone that needs a product decision becomes a `Type: decision` Blocked Declaration.

## Dependency Handling

- Refinement may happen before hard dependencies are resolved.
- Hard dependencies block implementation, not necessarily refinement.
- If a dependency may change the command surface, output shape, or policy decision, state the assumption explicitly:
  `Dependency contract assumed: ...`
- If the story must be revalidated after a dependency closes, write:
  `Revalidate after dependency closes: yes`
- If a label, milestone, epic link or dependency is missing, the Tech Lead settles it and records it under `## Decisions`; a missing business decision becomes a `Type: decision` Blocked Declaration instead of a guess.

## Issue Body Format

Update the issue body to this structure:

```md
Parent epic: #{EPIC_NUMBER} {EPIC_TITLE}
Feature group: #{FEATURE_NUMBER} {FEATURE_TITLE}
Parallel lane: {LANE}
Critical path: yes/no
Severity: critical | high | medium | low
Spec: [`specs/{EPIC_NUMBER}-{slug}/spec.md`]({blob link at the commit}) at `{commit}`

## Goal

What the fix must achieve.

## Defect Description

**Steps to reproduce:**
1. ...
2. ...
3. ...

**Expected behavior:**
...

**Actual behavior:**
...

**Environment:**
- OS / platform: ...
- Version / commit: ...
- Relevant config or flags: ...

## Impact

Who and what is affected. Data loss, incorrect output, blocked workflow, etc.

## Scope

- What the fix covers.

## Out of Scope

- What should not change as part of this fix.

## Acceptance Criteria

- The defect no longer reproduces following the steps above.
- A regression test exists that would have caught this bug.
- ...

## Epic and Feature Requirements Mapping

- {Feature criterion or requirement, one line} → AC {n}
- AC {n} serves {Feature criterion, e.g. F1.1} (every AC is listed)

Not in this bug:

- {requirement} — deferred to #N: {reason}
- {requirement} — not applicable: {reason}

## Command Surface

If relevant:

- `hpw <command> <target> --repo <owner/repo>`
- flags:
  - `--json`
  - ...
- exit codes:
  - `0` when ...
  - non-zero when ...

## Output Contract

If relevant:

JSON output includes:

- ...

Human-readable output includes:

- ...

## Test Expectations

- Regression test that reproduces the original defect before the fix.
- ...

## Documentation Impact

- ...

## Dependencies

Hard depends on:

- #X ...

Soft depends on:

- #Y ...

## Dependency Contract Assumed

If relevant:

- ...

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
- Reuse existing helpers:
  - ...
- Avoid:
  - ...

## Definition of Done

- Bug no longer reproduces following the steps above.
- Regression test added and passing.
- No unintended side effects on related commands or output contracts.
- PR reviewed and merged.
- ...
```

## Refinement Completeness Gate (mandatory)

The last step of every refinement, is the deterministic check; the model is not the judge of its own completeness.

- Run `python3 scripts/refine-check --issue <n> --repo LauraMardones/headless-pr-workflow`. It re-reads the issue and checks the `type:`, `executor:` and `prio:` labels, open hard dependencies, the required body sections, open questions and parent references. It reads no board.
- Run it on every refinement, including a re-refinement of an issue that is already `Ready for implementation`.
- Only exit `0` permits reporting `Refined` or `Ready for implementation`. Exit `1` (a named check failed) or exit `2` (the issue, labels or dependencies could not be read) forbids it.
- On a non-zero exit: fix what can be fixed and re-run the check; if a failure remains, list the failing checks as blockers and do not report `Next action: Ready for implementation` or `Refined`.
- `executor:` label rule: a `type:story`, `type:task` or `type:bug` issue carries exactly one existing `executor:` label (`docs/PROJECT-STATUS.md`); a `type:epic` or `type:feature` issue needs none. Apply it before the check, choosing among the repository's existing labels.
- Any statement about labels in the reply or the Session Summary comes from the check output, not from memory or from the issue text.
- The Session Summary `Checks run:` line carries the result: `refine-check — pass`, or `refine-check — fail: <check-ids>` / `refine-check — unreadable`. `none` is not valid for a refinement that reports a status.

## Final Reply Format
After updating GitHub, reply only with:

- Issue URL
- Next action: Ready for implementation, Refined; blocked by #X, or Needs PO decision
- Summary of what changed
- Labels changed
- Milestone changed
- Remaining hard dependencies
- Remaining Open Questions
