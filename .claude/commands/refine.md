Act as a senior product owner and agile delivery coach with GitHub access.

Refine GitHub issue #$ARGUMENTS in LauraMardones/headless-pr-workflow by identifying its type and following the correct refinement standard.

Do not implement code.

## GitHub operation fallback

- Prefer the GitHub plugin/MCP integration for GitHub reads and mutations when it is available.
- If the plugin/MCP integration is unavailable, use the authenticated `gh` CLI for required reads and mutations.
- Use a direct GitHub API request only when neither the plugin/MCP integration nor `gh` supports the required operation.
- Before any mutation, verify the target repository and issue or PR number. For review- or merge-related mutations, also verify the current head SHA where applicable.
- Never expose, print, log, persist, or commit GitHub credentials.
- The fallback changes only the transport. It never bypasses workflow gates, and it must produce the same durable GitHub evidence as the preferred integration.

## Step 0 — Check for PO usage feedback

Before identifying the issue type, check whether recent implementations under the same Feature or Epic have attracted PO usage feedback that should inform this refinement.

**Determine the parent Feature or Epic:**
- Read the issue body for a `Parent epic:`, `Feature group:`, or `Closes #` reference.
- If found, record the parent issue number.
- If not found, log a note ("Parent Feature/Epic could not be determined — skipping usage-feedback check") and proceed immediately to Step 2.

**Fetch recently closed/merged work under the parent (14-day lookback):**
- Use `mcp__github__list_issues` with `state: closed` and a `since` filter set to 14 days ago, scoped to the repository `LauraMardones/headless-pr-workflow`.
- Filter results to items whose body references the parent issue number (e.g. `Parent epic: #N` or `Feature group: #N`), or search closed PRs via `mcp__github__list_pull_requests` with `state: closed` and the same date window.
- If no recent items exist, proceed immediately to Step 2 without prompting the PO.

**Check for PO usage feedback comments:**
- For each recently closed issue or merged PR found, use `mcp__github__issue_read` (method: `get_comments`) to fetch its comments.
- Look for PO comments (author: LauraMardones or comments describing usage observations, unexpected behaviour, or product feedback).
- "Usage feedback" means observations from actually using the delivered product — not refinement discussion, implementation notes, or approval comments.

**Act on findings:**

- **No feedback found:** Proceed immediately to Step 2. Do not prompt the PO.
- **Parent undetermined:** Log a note and proceed immediately to Step 2. Do not prompt the PO.
- **Feedback found:** Summarise the relevant comments (issue/PR number, author, date, key observation) and ask the PO:
  > "Usage feedback was found on recent implementations under this Feature/Epic. Would you like to incorporate it before proceeding with refinement, or continue with Step 2 now?"
  >
  > Wait for the PO's response before continuing.

## Step 2 — Identify issue type

Fetch the issue from GitHub. Check its labels for exactly one of:

- `type:epic` → follow `.claude/commands/refine-epic.md`
- `type:feature` → follow `.claude/commands/refine-feature.md`
- `type:story` → follow `.claude/commands/refine-story.md`
- `type:task` → follow `.claude/commands/refine-task.md`
- `type:bug` → follow `.claude/commands/refine-bug.md`

Before adding any Open Question that requires PO input, search existing repository documentation, including `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections. If an existing answer is found, record and follow it instead of escalating the question to the PO.

## Step 3 — If no type label is set

Inspect the issue title for a recognized prefix:

- Starts with `Epic:` or `[Epic]` → type is `type:epic`
- Starts with `Feature:` or `[Feature]` → type is `type:feature`
- Starts with `Story:` or `[Story]` → type is `type:story`
- Starts with `Task:` or `[Task]` → type is `type:task`
- Starts with `Bug:` or `[Bug]` → type is `type:bug`

If the type is identified from the title:
- Add the correct `type:X` label to the issue on GitHub before proceeding.
- Then follow the corresponding sub-prompt.

## Step 4 — If type is still unclear

Stop. Do not guess. Reply with:

- Issue URL
- Current labels
- Issue title
- Why the type could not be determined
- What label the user should add to proceed

Never silently pick a type or apply a wrong label.

---

## Required GitHub Output — Must Not Be Skipped

**Issue body updated on GitHub** with refined content and **PO status updated** to `Refined` or `Ready for implementation` using the available GitHub operation fallback.

After refining, post a comment on the issue using this structure (omit inapplicable fields):

```
## Session Summary
Command: refine
Issue / PR: #<issue-number>
Checks run: none — refinement only
Blockers: <list, or "none">
Next action: <"Ready for implementation" or "Needs PO decision">
```
