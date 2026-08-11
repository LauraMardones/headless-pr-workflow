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

## Parent-Link Assignment

After each new Story, Task, or Bug issue is created and added to GitHub Projects, immediately set its parent link so the Feature → Story hierarchy is visible on the board. Run this step for every newly created child issue before moving on.

### How to set the parent link

**Step 1 — Locate project item IDs**

List all items in project #3 and find the relevant IDs:

```
gh project item-list 3 --owner LauraMardones --format json
```

Filter the output by `content.number` to find:
- The Feature's project item ID (the `id` field matching the Feature's issue number)
- The newly created child issue's project item ID

If the Feature is not present in the project output, skip to Step 4.

**Step 2 — Attempt `updateProjectV2ItemFieldValue`**

Try setting the parent field using the Feature's project item ID:

```
gh api graphql -f query='
  mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $parentId: String!) {
    updateProjectV2ItemFieldValue(input: {
      projectId: $projectId
      itemId: $itemId
      fieldId: $fieldId
      value: { text: $parentId }
    }) {
      projectV2Item { id }
    }
  }
' -f projectId="PVT_kwHOBBqYlM4BV_cG" \
  -f itemId="CHILD_PROJECT_ITEM_ID" \
  -f fieldId="PVTF_lAHOBBqYlM4BV_cGzhRXJTY" \
  -f parentId="FEATURE_PROJECT_ITEM_ID"
```

If this mutation succeeds, the parent link is set — proceed to the next child issue.

**Step 3 — Fallback: `addSubIssue` mutation**

If Step 2 fails (the built-in PARENT_ISSUE field may not accept `updateProjectV2ItemFieldValue`), fall back to setting the relationship at the issue level. First obtain the issue node IDs:

```
gh issue view FEATURE_NUMBER --json id --jq '.id'
gh issue view CHILD_NUMBER --json id --jq '.id'
```

Then run:

```
gh api graphql -f query='
  mutation($parentId: ID!, $childId: ID!) {
    addSubIssue(input: { issueId: $parentId, subIssueId: $childId }) {
      issue { number }
      subIssue { number }
    }
  }
' -f parentId="FEATURE_ISSUE_NODE_ID" -f childId="CHILD_ISSUE_NODE_ID"
```

**Step 4 — Graceful failure**

If the Feature item is not found in project #3, or if all mutation attempts fail, emit a warning and continue without blocking refinement:

> Warning: Feature #NUMBER not found in project #3 — parent link not set for issue #NUMBER. Continuing refinement.

Do not fail or halt the refinement session if parent-link assignment cannot be completed.

## Implementation Order Derivation

After the breakdown is finalized, derive implementation order from the Dependencies fields on each seed story, task, or bug:

- Inspect each seed issue's `Hard depends on:` and `Soft depends on:` dependency lists.
- Use hard dependencies to create step boundaries. Step 1 contains issues with no hard dependencies; Step 2 contains issues whose hard dependencies are satisfied by Step 1; continue until all placeable seed issues are placed.
- Do not use soft dependencies to create step boundaries. Mention soft dependencies only when they are useful context inside an existing step.
- Group issues that can run in parallel within the same step.
- If a seed issue has an unresolved hard dependency outside the seed set, do not place it in a runnable step. List it under `Blocked / Unresolved Hard Dependencies` with the external dependency.
- If the seed issue hard-dependency graph has a cycle, do not invent an order for the cyclic issues. List the cycle under `Blocked / Unresolved Hard Dependencies`.
- If any seed issue is blocked by an external unresolved hard dependency or a cycle, leave final PO status as `Refined`, not `Ready for implementation`, until those hard dependencies are resolved.
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
- Blocked / Unresolved Hard Dependencies when any seed issue cannot be ordered
- Dependencies (Hard / Soft)
- Assumptions
- Open Questions

## Open Questions Format

Before adding an Open Question that requires PO input, search existing repository documentation, including `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections. If an existing answer is found, record and follow it instead of escalating the question to the PO.

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

Blocked / Unresolved Hard Dependencies:
- #A blocked by external hard dependency #B ...
- #C blocked by cycle: #C -> #D -> #C

## Dependencies

Hard depends on:

- #X ...

Soft depends on:

- #Y ...

## Assumptions

- ...

## Open Questions

- ...

## Decisions

...
```

## Final Reply Format

After updating GitHub, reply only with:

- Issue URL
- Final PO status
- Existing issues found and included (with numbers)
- Stories/Tasks/Bugs created with issue numbers
- Labels changed
- Remaining Open Questions
