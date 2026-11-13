Act as the Tech Lead (`docs/ROLES.md`) with GitHub access. Refinement makes no product decision (ADR-009 item 4).

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

Before identifying the issue type, check whether recent implementations under the same Feature or Epic have attracted PO usage feedback that should inform this refinement. Put no question to the PO in the session.

**Determine the parent Feature or Epic:**
- Read the issue body for a `Parent epic:`, `Feature group:`, or `Closes #` reference.
- If found, record the parent issue number.
- If not found, log a note ("Parent Feature/Epic could not be determined — skipping usage-feedback check") and proceed immediately to Step 2.

**Fetch recently closed/merged work under the parent (14-day lookback):**
- Use `mcp__github__list_issues` with `state: closed` and a `since` filter set to 14 days ago, scoped to the repository `LauraMardones/headless-pr-workflow`.
- Filter results to items whose body references the parent issue number (e.g. `Parent epic: #N` or `Feature group: #N`), or search closed PRs via `mcp__github__list_pull_requests` with `state: closed` and the same date window.
- If no recent items exist, proceed immediately to Step 2.

**Check for PO usage feedback comments:**
- For each recently closed issue or merged PR found, use `mcp__github__issue_read` (method: `get_comments`) to fetch its comments.
- Look for PO comments (author: LauraMardones or comments describing usage observations, unexpected behaviour, or product feedback).
- "Usage feedback" means observations from actually using the delivered product — not refinement discussion, implementation notes, or approval comments.

**Classify each usage finding (usage-finding rule):**

- **Affects scope or an acceptance criterion:** it becomes a `Type: decision` Blocked Declaration (see Tech Lead rules → Decision blockers). The affected criterion stays unchanged.
- **Any other finding:** note it in the refined issue under `## Decisions` (issue/PR, date, observation) and continue refinement.
- **No feedback found, or parent undetermined:** proceed immediately to Step 2.

## Tech Lead rules

These rules apply to every refinement. `refine-epic.md` and `refine-feature.md` point here and follow them.

- **Role:** the Tech Lead (`docs/ROLES.md`), not a product owner. Refinement makes no product decision (ADR-009 item 4) and asks the PO nothing inside the session.
- **Spec lookup:** the spec is the one folder `specs/<epic>-*/spec.md` on `main`, where `<epic>` is the issue's own number for a `type:epic` issue and the `Parent epic: #N` number otherwise.
- **No merged spec:** when that folder does not exist on `main`, or more than one matches, stop before any write. Edit no issue body, label or comment, and name the missing or ambiguous spec as the reason in the reply. Issues already refined under that Epic are not touched.
- **Spec check:** before the spec is used, fetch `main` so the commit exists locally, then run `python3 scripts/spec-check <spec-path> --ref <commit>`, with `<commit>` the current `main` commit of the spec. Exit `1`: post a `Type: decision` Blocked Declaration whose `Question` names each failing check id from the output, change no acceptance criterion, and leave the issue body unchanged. Exit `2`: stop, write nothing, and report the spec as unreadable with the script's reason.
- **Spec record:** the issue body carries exactly one line ``Spec: [`<spec-path>`](<blob link at the commit>) at `<commit>` `` with an optional note after it; `<commit>` is 7–40 hex characters. `scripts/refine-check` verifies it (`spec_record`).
- **Decision blockers:** what the spec does not settle becomes a `## Blocked Declaration` with `Type: decision` in the format of `docs/PROJECT-STATUS.md` → Blocked Status Protocol, posted on the issue being refined; the affected criterion stays unchanged and the issue body stays unchanged. Before declaring one, search for an existing answer as described in Step 2; the pre-check of existing decisions stays.
- **Resume after `/unblock`:** the next run reads the PO's reply that contains a `/unblock` line, records the answer under the issue's `## Decisions` with its date and comment link, and continues from it. An answer that changes scope is recorded, the affected criterion stays unchanged, and the issue stays blocked until a spec PR with the change merges (ADR-009 item 5).
- **Lens questions:** the Tech Lead answers each active lens's `## Lens Questions` in the session and records the answers under `## Decisions`. A lens question that needs a product choice becomes a decision blocker.
- **Usage findings:** classified in Step 0.

## Step 2 — Identify issue type

Fetch the issue from GitHub. Check its labels for exactly one of:

- `type:epic` → follow `.claude/commands/refine-epic.md`
- `type:feature` → follow `.claude/commands/refine-feature.md`
- `type:story` → follow `.claude/commands/refine-story.md`
- `type:task` → follow `.claude/commands/refine-task.md`
- `type:bug` → follow `.claude/commands/refine-bug.md`

Before declaring a decision blocker, search existing repository documentation, including `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections. If an existing answer is found, record and follow it instead of escalating it.

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

**Issue body updated on GitHub** with refined content and **Session Summary posted** with `Next action: Ready for implementation`, `Next action: Refined; blocked by #X` or `Next action: Needs PO decision`, using the available GitHub operation fallback.

Refinement is not complete until the sub-prompt's `## Refinement Completeness Gate (mandatory)` has run: `python3 scripts/refine-check --issue $ARGUMENTS --repo LauraMardones/headless-pr-workflow` as the last step. Only exit `0` permits reporting `Refined` or `Ready for implementation`; on exit `1` or `2` the failing checks are listed as blockers. A re-refinement of an issue already `Ready for implementation` runs the same check. A `type:story`, `type:task` or `type:bug` issue needs exactly one existing `executor:` label. Statements about labels come from the check output.

After refining, post a comment on the issue using this structure (omit inapplicable fields):

```
## Session Summary
Command: refine
Issue / PR: #<issue-number>
Checks run: refine-check — <pass | fail: <check-ids> | unreadable>
Blockers: <list, or "none">
Next action: <"Ready for implementation" or "Needs PO decision">
```
