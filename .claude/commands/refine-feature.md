Act as a senior product owner and agile delivery coach with GitHub access.

Refine the following GitHub feature until it meets the project's refinement standard.

Repository:
LauraMardones/headless-pr-workflow

Issue:
#$ARGUMENTS

Do not implement code.

## Core Instructions

- Fetch the current GitHub issue title, body, labels, milestone, parent epic, linked issues, and relevant comments.
- Inspect relevant repository docs before changing the issue.
- Update the GitHub issue body directly with the refined feature.
- Do not only return the refined feature in chat.
- GitHub must remain the source of truth.
- Preserve useful existing content, but reorganize it into the project feature format.
- Do not discard prior decisions, constraints, dependencies, or context unless clearly obsolete.
- If something appears obsolete, move it to Notes or Open Questions instead of deleting it silently.
- If you do not have permission or tooling to update GitHub, stop and say exactly what is missing.

## Status Workflow

- `Backlog`: not actively prepared.
- `In refinement`: refinement is in progress.
- `Refined`: scope is clear but hard dependencies may still block the first story.
- `Ready for implementation`: seed stories are created and the first one is ready to be picked up.
- `Done`: all stories delivered and closed.

When starting: move status to `In refinement`.
When complete: move to `Refined` or `Ready for implementation`.

## Existing Issues Inventory

Before creating any new issues, inventory what already exists:

- Search GitHub for open and closed issues that reference this feature number in their body (`Feature group: #X` or `Parent feature: #X`).
- Also check the feature body for any existing Seed Stories list.
- Classify each found issue by type (`type:story`, `type:task`, `type:bug`) and state (open/closed).
- Do not create a duplicate of any existing issue that already covers the same scope.
- If an existing issue is misclassified, mislabeled, or missing its parent reference, note it under Open Questions rather than silently fixing it during this refinement.

## Lens Selection

Before breaking down the feature, apply refinement lenses to ensure the breakdown is sound:

1. Read all `.md` files in `.claude/commands/refinement-lenses/`
2. Always apply the `token-economics.md` lens (default, regardless of labels)
3. Additionally, for each lens file whose `## Trigger Labels` section matches any of the current issue's GitHub labels, apply that lens as well
4. For each active lens, present its `## Lens Questions` to the user and incorporate the lens perspective into your analysis before proposing the breakdown

The lens questions will help identify:
- Per-session token overhead problems that suggest different story boundaries
- Documentation work that should be clustered in a single story
- Executor tier misalignments that need correction

Proceed to the breakdown step only after lens questions have been answered.

## Breakdown

A feature refines into Story/Task/Bug issues - not into further Features.

- Include existing Story/Task/Bug issues in the Seed Stories list.
- Create new issues only for scope gaps not yet covered by existing issues.
- Link all new issues to this feature and its parent epic.
- List all related issues - both pre-existing and newly created - under Seed Stories in the feature body.
- If the breakdown reveals that the feature itself should be split into two features, stop and raise it as an Open Question rather than silently splitting.

After the breakdown is complete, apply the devil's advocate closing step (see below).

## Implementation Order Derivation

After the breakdown is finalized, derive implementation order from the Dependencies fields on each seed story, task, or bug:

- Inspect each seed issue's `Hard depends on:` and `Soft depends on:` dependency lists.
- Use hard dependencies to create step boundaries. Step 1 contains issues with no hard dependencies; Step 2 contains issues whose hard dependencies are satisfied by Step 1; continue until all seed issues are placed.
- Do not use soft dependencies to create step boundaries. Mention soft dependencies only when they are useful context inside an existing step.
- Group issues that can run in parallel within the same step.
- Document the derived sequence as numbered steps under `## Implementation Order` in the feature body.

## Devil's Advocate Closing Step

After the breakdown is proposed, challenge it with this question:

**"What is the minimum number of stories that correctly and completely implements this feature? Can any proposed stories be merged without losing clarity or parallelism?"**

Review the proposed stories and ask:
- Are there stories that could be combined into a single story without losing independent deliverability or parallel execution potential?
- Is any story duplicating work or setup cost that another story already includes?
- Would merging any pair of stories actually reduce complexity or coordination overhead?

If merging is justified, modify the breakdown accordingly. If the breakdown is already minimal, document why each story is necessary.

## Refinement Standard

Refinement is complete when the feature has:

- clear Goal
- clear Why (relation to parent epic)
- Scope
- Out of Scope
- Success Criteria (testable)
- Seed Stories (created as GitHub issues)
- Implementation Order derived from seed issue hard dependencies
- Dependencies (Hard / Soft)
- Assumptions
- Open Questions

## Label Handling

- Use only existing GitHub labels.
- Apply `type:feature` if not already set.
- If priority, area, or milestone is unclear, add it as an Open Question.

## Issue Body Format

Update the issue body to this structure:

```md
Parent epic: #{EPIC_NUMBER} {EPIC_TITLE}
Milestone: {MILESTONE}
PO status: {In refinement | Refined | Ready for implementation.}

## Goal

...

## Why

Relation to parent epic and business value.

## Scope

- ...

## Out of Scope

- ...

## Success Criteria

- [ ] ...

## Seed Stories

- #X Story: ...
- #Y Task: ...

## Implementation Order

Step 1 - no hard dependencies (can start immediately, run in parallel):
- #X ...
- #Y ...

Step 2 - hard depends on Step 1:
- #Z ...

Step 3 - hard depends on Step 2:
- #W ...

## Dependencies

Hard depends on:

- #X ...

Soft depends on:

- #Y ...

## Assumptions

- ...

## Open Questions

- ...
```

## Final Reply Format

After updating GitHub, reply only with:

- Issue URL
- Final PO status
- Existing issues found and included (with numbers)
- Stories/Tasks/Bugs created with issue numbers
- Labels changed
- Remaining Open Questions
