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

## Existing Issues Inventory

Before creating any new issues, inventory what already exists:

- Search GitHub for open and closed issues that reference this epic number in their body (`Parent epic: #X`).
- Also check the epic body for any existing Seed Features or Seed Stories lists.
- Classify each found issue by type (`type:feature`, `type:story`, `type:task`, `type:bug`) and state (open/closed).
- Do not create a duplicate of any existing issue that already covers the same scope.
- If an existing issue is misclassified, mislabeled, or missing its parent epic reference, note it under Open Questions rather than silently fixing it during this refinement.

## Lens Selection

Before starting the breakdown, apply refinement lenses:

**Token Economics** (always applied — load `.claude/commands/refinement-lenses/token-economics.md`):
- Identify documentation clusters at the feature level: features whose stories would all write to the same files with no hard dependencies → flag as merge candidates within features.
- Verify model tier assignments for any direct stories: haiku = docs/config/boilerplate, sonnet = logic/integration, opus = architecture/review.
- Ask: "Are the proposed features sized to deliver meaningfully independent value, or would they collapse into a single coherent delivery?"

**Domain-specific lenses** (label-triggered):
- For each label on the issue, check if `.claude/commands/refinement-lenses/{label-slug}.md` exists.
- If it does, load and apply that lens before proceeding.
- Lens questions must be answered before creating any issues.

## Feature Assessment

Before breaking an epic into stories, assess whether a Feature level is warranted:

**Create Features if:**
- The epic contains more than ~6 stories (counting existing ones), OR
- There are clearly distinct capability clusters that can be delivered and closed independently.

**Skip Features and create stories directly if:**
- The epic is small enough that all work is one coherent delivery.
- A Feature would be a 1:1 wrapper around the epic with no grouping benefit.

If Features are warranted:
- Include existing `type:feature` issues in the Seed Features list.
- Create new Feature issues only for capability clusters not yet covered.
- Do not create Story/Task/Bug issues yet — that is the job of `refine-feature`.

If Features are not warranted:
- Include existing Story/Task/Bug issues in the Seed Stories list.
- Create new issues only for scope gaps not yet covered by existing issues.

Always list all related issues in the epic body — both pre-existing and newly created.

## Devil's Advocate

After completing the initial breakdown, challenge it before finalising:

- "What is the minimum number of features that correctly and completely delivers this epic?"
- "Can any proposed features be merged without losing independent deliverability?"
- "Are any features so small they should just be stories directly under the epic?"
- "Are any features so large they need their own sub-feature level?"

If merging reduces feature count without sacrificing clarity: merge them and update the breakdown.

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
- Existing issues found and included (with numbers)
- Features created (if any) with issue numbers
- Stories/Tasks created (if any) with issue numbers
- Labels changed
- Milestone set or left open
- Remaining Open Questions
