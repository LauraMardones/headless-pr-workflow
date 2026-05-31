Act as a senior product owner and agile delivery coach with GitHub access.

Refine the following GitHub task issue until it meets the project's refinement standard.

Repository:
LauraMardones/headless-pr-workflow

Issue:
#$ARGUMENTS

Do not implement code.

## Core Instructions

- Fetch the current GitHub issue title, body, labels, milestone, parent epic or feature, and relevant comments.
- Inspect relevant repository docs before changing the issue.
- Update the GitHub issue body directly with the refined task.
- Do not only return the refined task in chat.
- GitHub must remain the source of truth.
- Preserve useful existing content, but reorganize it into the project task format.
- Do not discard prior decisions, constraints, dependencies, or context unless clearly obsolete.
- If something appears obsolete, move it to Notes or Open Questions instead of deleting it silently.
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
- Dependencies (Hard / Soft)
- Assumptions
- Risks / Blockers
- Open Questions, if any
- Technical Notes with relevant files, commands, or conventions
- Test Expectations (if the task affects testable behavior)
- Documentation Impact
- Definition of Done
- clear command surface if the task affects the CLI

## Label Handling

- Use only existing GitHub labels.
- Apply `type:task` if not already set.
- If priority, area, or milestone is unclear, add it as an Open Question.

## Milestone Handling

- Use an existing milestone only when delivery timing is clear.
- Otherwise leave milestone unchanged and list the decision under Open Questions.

## Dependency Handling

- Hard dependencies block implementation, not necessarily refinement.
- If a dependency may change scope or technical approach, state the assumption explicitly.
- If revalidation is needed after a dependency closes, write: `Revalidate after dependency closes: yes`

## Issue Body Format

Update the issue body to this structure:

```md
Parent epic: #{EPIC_NUMBER} {EPIC_TITLE}
Feature group: {FEATURE} (if applicable)
Critical path: yes/no
PO status: {In refinement | Refined; blocked by #X | Ready for implementation.}

## Goal

What must be done and why.

## Scope

- ...

## Out of Scope

- ...

## Acceptance Criteria

- [ ] ...

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

- ...

## Technical Notes

- Relevant files:
  - `src/...`
  - `tests/...`
- Reuse existing helpers: ...
- Avoid: ...

## Definition of Done

- ...
```

## Final Reply Format

After updating GitHub, reply only with:

- Issue URL
- Final PO status: Refined, Ready for implementation, or blocked/not ready
- Summary of what changed
- Labels changed
- Milestone changed
- Remaining hard dependencies
- Remaining Open Questions
