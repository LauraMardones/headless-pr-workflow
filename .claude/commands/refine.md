Act as a senior product owner and agile delivery coach with GitHub access.

Refine GitHub issue #$ARGUMENTS in LauraMardones/headless-pr-workflow by identifying its type and following the correct refinement standard.

Do not implement code.

## Step 1 — Identify issue type

Fetch the issue from GitHub. Check its labels for exactly one of:

- `type:epic` → follow `.claude/commands/refine-epic.md`
- `type:feature` → follow `.claude/commands/refine-feature.md`
- `type:story` → follow `.claude/commands/refine-story.md`
- `type:task` → follow `.claude/commands/refine-task.md`
- `type:bug` → follow `.claude/commands/refine-bug.md`

## Step 2 — If no type label is set

Inspect the issue title for a recognized prefix:

- Starts with `Epic:` or `[Epic]` → type is `type:epic`
- Starts with `Feature:` or `[Feature]` → type is `type:feature`
- Starts with `Story:` or `[Story]` → type is `type:story`
- Starts with `Task:` or `[Task]` → type is `type:task`
- Starts with `Bug:` or `[Bug]` → type is `type:bug`

If the type is identified from the title:
- Add the correct `type:X` label to the issue on GitHub before proceeding.
- Then follow the corresponding sub-prompt.

## Step 3 — If type is still unclear

Stop. Do not guess. Reply with:

- Issue URL
- Current labels
- Issue title
- Why the type could not be determined
- What label the user should add to proceed

Never silently pick a type or apply a wrong label.

---

## Required GitHub Output — Must Not Be Skipped

**Issue body updated on GitHub** with refined content and **PO status updated** to `Refined` or `Ready for implementation` using `mcp__github__issue_write`.

After refining, post a comment on the issue using this structure (omit inapplicable fields):

```
## Session Summary
Command: refine
Issue / PR: #<issue-number>
Checks run: none — refinement only
Blockers: <list, or "none">
Next action: <"Ready for implementation" or "Needs PO decision">
```
