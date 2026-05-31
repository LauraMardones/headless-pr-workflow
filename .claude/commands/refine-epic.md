Act as a senior product owner and agile delivery coach with GitHub access.

Refine the following GitHub epic until it meets the project's refinement standard.

Repository:
LauraMardones/headless-pr-workflow

Issue:
#$ARGUMENTS

Do not implement code.

## Core Instructions

- Fetch the current GitHub issue title, body, labels, milestone, linked issues, and relevant comments.
- Inspect relevant repository docs and roadmap before changing the issue.
- Update the GitHub issue body directly with the refined epic.
- Do not only return the refined epic in chat.
- GitHub must remain the source of truth.
- Preserve useful existing content, but reorganize it into the project epic format.
- Do not discard prior decisions, constraints, dependencies, or context unless clearly obsolete.
- If something appears obsolete, move it to Notes or Open Questions instead of deleting it silently.
- If you do not have permission or tooling to update GitHub, stop and say exactly what is missing.

## Status Workflow

- `Backlog`: not actively prepared.
- `In refinement`: refinement is in progress.
- `Refined`: scope is clear but delivery planning or hard dependencies remain open.
- `Ready for implementation`: seed stories/features are created and the first one is ready to be picked up.
- `Done`: all success criteria delivered and closed.

When starting: move status to `In refinement`.
When complete: move status to `Refined` or `Ready for implementation` depending on whether blockers remain.

## Feature Assessment

Before breaking an epic into stories, assess whether a Feature level is warranted:

**Create Features if:**
- The epic contains more than ~6 stories, OR
- There are clearly distinct capability clusters that can be delivered and closed independently.

**Skip Features and create stories directly if:**
- The epic is small enough that all work is one coherent delivery.
- A Feature would be a 1:1 wrapper around the epic with no grouping benefit.

If Features are warranted:
- Create one GitHub issue per Feature with label `type:feature`, linked to this epic.
- Do not create Story/Task/Bug issues yet — that is the job of `refine-feature`.
- List the created Feature issues under Seed Features in the epic body.

If Features are not warranted:
- Create Story/Task/Bug issues directly, linked to this epic.
- List them under Seed Stories in the epic body.

## Refinement Standard

Refinement is complete when the epic has:

- clear Goal
- clear Why / Background
- Scope
- Non-goals
- Success Criteria (testable)
- Seed Features or Seed Stories (created as GitHub issues)
- Dependencies (Hard / Soft)
- Open Questions
- Related epics or milestones

## Label Handling

- Use only existing GitHub labels.
- Do not invent new labels.
- Apply `type:epic` if not already set.
- If the correct label for priority, area, or milestone is unclear, add it as an Open Question.

## Issue Body Format

Update the issue body to this structure:

```md
Milestone: {MILESTONE}
PO status: {In refinement | Refined | Ready for implementation.}

## Goal

...

## Why / Background

...

## Scope

- ...

## Non-goals

- ...

## Success Criteria

- [ ] ...

## Seed Features

If features are warranted:

- #X Feature: ...
- #Y Feature: ...

## Seed Stories

If no features needed:

- #X Story: ...
- #Y Task: ...

## Dependencies

Hard depends on:

- #X ...

Soft depends on:

- #Y ...

## Open Questions

- ...

## Related

- ...
```

## Final Reply Format

After updating GitHub, reply only with:

- Issue URL
- Final PO status
- Features created (if any) with issue numbers
- Stories/Tasks created (if any) with issue numbers
- Labels changed
- Milestone set or left open
- Remaining Open Questions
