Act as a senior product owner and agile delivery coach with GitHub access.

Refine the following GitHub issue until it meets the project's refinement standard.

Repository:
LauraMardones/headless-pr-workflow

Issue:
#$ARGUMENTS

Do not implement code.

## Core Instructions

- Fetch the current GitHub issue title, body, labels, milestone, linked epic, task lists, dependencies, and relevant comments.
- Inspect relevant repository docs before changing the issue.
- Update the GitHub issue body directly with the refined story.
- Do not only return the refined story in chat.
- GitHub must remain the source of truth.
- Preserve useful existing issue content, but reorganize it into the project story format.
- Do not discard prior decisions, constraints, dependencies, or context unless clearly obsolete.
- If something appears obsolete, move it to Notes or Open Questions instead of deleting it silently.
- If you do not have permission or tooling to update GitHub, stop and say exactly what is missing.

## Status Workflow

Use these statuses consistently:

- `Backlog`: not actively prepared.
- `In refinement`: refinement is in progress.
- `Refined`: the story contract is clear, but hard dependencies may still block implementation.
- `Ready for implementation`: an implementer can pick it up immediately.
- `Done`: delivered/closed.

When starting refinement:

- Move the GitHub Project/status field to `In refinement`.
- If the real GitHub Project status cannot be updated but issue-body edits are possible, update the issue body with:
  `PO status: In refinement`
- Report clearly if the real Project status could not be changed.

When refinement is complete:

- If the issue is clear but open hard dependencies still block implementation:
  - Move GitHub status to `Refined`.
  - Set the issue body to include:
    `PO status: Refined; blocked by #X`
  - List the exact hard dependencies that block implementation.

- If the issue is clear and no hard dependencies block implementation:
  - Move GitHub status to `Ready for implementation`.
  - Set the issue body to include:
    `PO status: Ready for implementation.`

- Never move an issue to `Ready for implementation` while hard dependencies are still unresolved.

## Refinement Standard

Refinement is complete when the issue has:

- clear Goal
- clear Why
- User Story or problem statement where relevant
- Business Value where relevant
- Scope
- Out of Scope
- testable Acceptance Criteria
- Dependencies split into Hard depends on and Soft depends on
- Assumptions
- Risks / blockers
- Open Questions, if any remain
- Technical Notes with relevant files, commands, or policy surfaces
- Test Expectations
- Documentation Impact
- Definition of Done
- clear command surface if the issue affects the CLI
- clear JSON output, human-readable output, and exit-code behavior if it affects a command

## Open Questions Format

Each open question must be structured as:

```md
### [Question title]
**Context:** [One sentence explaining why this matters]

**Option A: [Name]**
- Pros: ...
- Cons: ...

**Option B: [Name]**
- Pros: ...
- Cons: ...

**Recommendation:** Option A — [brief reason in plain language, no jargon]
```

Rules:
- Language must be non-technical — assume the reader has no engineering background
- Each option must have at least one pro and one con
- A recommendation is required; if genuinely too close to call, state why and what information is needed to decide
- If there are more than two options, include all of them

## Decisions Format

When a PO closes an open question during or after refinement, move it from Open Questions to Decisions with the chosen option, rejected alternatives, and date. Do not delete rejected options — they are part of the record. If a decision reverses a prior decision, add a new entry rather than editing the old one.

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
- Apply existing labels for type, priority, area, flow, and status only when the correct label is clear.
- If the correct existing label is unclear, leave labels unchanged and list it under Open Questions.

## Milestone Handling

- Use an existing milestone only when delivery timing is clear.
- Otherwise leave milestone unchanged and list the milestone decision under Open Questions.

## Dependency Handling

- Refinement may happen before hard dependencies are resolved.
- Hard dependencies block implementation, not necessarily refinement.
- If a dependency may change the command surface, output shape, or policy decision, state the assumption explicitly:
  `Dependency contract assumed: ...`
- If the story must be revalidated after a dependency closes, write:
  `Revalidate after dependency closes: yes`
- If required labels, milestone, epic link, dependency information, or business decisions are missing, add them as Open Questions instead of guessing.

## Issue Body Format

Update the issue body to this structure:

```md
Parent epic: #{EPIC_NUMBER} {EPIC_TITLE}
Feature group: {FEATURE_GROUP}
Parallel lane: {LANE}
Critical path: yes/no
PO status: {In refinement | Refined; blocked by #X | Ready for implementation.}

## Goal

...

## User Story / Problem Statement

As a ..., I want ..., so that ...

## Business Value

...

## Why

...

## Scope

- ...

## Out of Scope

- ...

## Acceptance Criteria

- ...

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

- ...

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
