Implement issue #$ARGUMENTS in LauraMardones/headless-pr-workflow using GitHub as source of truth.

You are acting only as implementer in this session.
Do not review or approve the same PR head SHA that you implement in this session.

## GitHub operation fallback

- Prefer the GitHub plugin/MCP integration for GitHub reads and mutations when it is available.
- If the plugin/MCP integration is unavailable, use the authenticated `gh` CLI for required reads and mutations.
- Use a direct GitHub API request only when neither the plugin/MCP integration nor `gh` supports the required operation.
- Before any mutation, verify the target repository and issue or PR number. For review- or merge-related mutations, also verify the current head SHA where applicable.
- Never expose, print, log, persist, or commit GitHub credentials.
- The fallback changes only the transport. It never bypasses workflow gates, and it must produce the same durable GitHub evidence as the preferred integration.

Follow core workflow policy and keep repo-specific or governance rules out of core commands.

Workflow status goals:
- Create and link a PR as early as possible.
- PR linked to issue -> story status should be "In implementation".
- When implementation is ready for review -> story status should be "In review".
- If you are fixing review blockers on an existing PR, keep the story in "In implementation" while fixes are underway, then move it back to "In review" only when the new head SHA is ready for review.

Required behavior:
1. Read the GitHub issue, acceptance criteria, linked context, and any existing PR state first.
2. Run a pre-flight environment check before implementation begins:
   - confirm `git status` shows no unexpected staged or uncommitted changes
   - confirm the current branch or note that a fresh branch will be created
   - confirm GitHub API access by fetching the issue or repo metadata
   - stop and report if any pre-flight check fails
3. Fetch and pull the latest `main` before creating a new branch or PR.
4. Use a dedicated branch for this story only.
5. Keep the work strictly within the issue scope and acceptance criteria.
6. If the issue is dependency-heavy, verify that hard dependencies are complete before implementation starts.
7. Run the WIP pre-flight check defined in `docs/PROJECT-STATUS.md` before pulling the story:
   - read the candidate story's `## Files affected` section
   - scan open stories with status "In implementation"
   - stop if 2 or more stories are already "In implementation"
   - stop if any active story declares overlapping files
   - report any WIP limit failure or file overlap as a conflict blocker using the Blocked Status Protocol
8. If you discover blocked prerequisites, unclear acceptance criteria, scope drift, unresolved dependency decisions, WIP limit violations, or file ownership conflicts:
   - before declaring a `Type: decision` blocker, search `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections; follow and record an existing answer instead of escalating it
   - only if no documented answer exists, declare the decision blocker with the protocol's required `Checked:` line naming what was searched
   - stop
   - report WIP limit violations and file ownership conflicts through the Blocked Status Protocol
   - report other blockers clearly
   - do not silently expand scope
9. If no PR exists yet:
   - create a Draft PR as early as possible
   - the PR body must be non-empty: include `Closes #$ARGUMENTS` and a brief description of what the PR changes and why; `--fill` alone is insufficient
   - link the PR to issue #$ARGUMENTS
   - ensure the PR references and closes #$ARGUMENTS when merged
   - ensure the story moves to "In implementation"
10. If a PR already exists:
   - continue on the existing PR branch
   - update the existing PR instead of opening a parallel PR unless explicitly needed
   - keep the story in "In implementation" while implementation or blocker fixes are ongoing
11. Implement only the requested changes.
12. Add or update deterministic tests as needed.
13. For mutation work, preserve fresh-refresh and safety checks before mutating behavior.
14. Push commits to the same PR branch as work progresses.
15. Before requesting review, perform a self-review:
   - run `git diff main` (or the equivalent against the base branch) and verify the diff matches the issue scope exactly
   - run `scripts/ac-summary.sh --issue $ARGUMENTS --repo LauraMardones/headless-pr-workflow` and use the extracted checklist to verify AC/DoD coverage; exit `2` means no checklist was found and requires a manual issue-body fallback, not that coverage passed, while exit `1` is an invocation or lookup failure
   - do not copy the extracted AC/DoD checklist into the handoff comment
   - if any changed file is eligible under `scripts/dispatcher-change-check.sh`, run `bash scripts/dispatcher-change-check.sh --files <changed-files...>`; fix every `FAIL` (exit `1`) before handoff, and treat exit `2` as an invocation or input blocker
   - confirm the PR body contains `Closes #$ARGUMENTS` and a meaningful description
   - confirm the issue link in the PR references the correct issue number
   - confirm command files use `In implementation`, not the legacy progress-status spelling
   - confirm the PR title and body are accurate and not auto-generated boilerplate
16. When implementation is complete:
   - run the most relevant documented local checks
   - record any checks that could not be run and why
   - commit only the intended changes
   - confirm the current PR head SHA from GitHub
   - mark the PR Ready for review, or explicitly request review if Ready for review is unavailable
   - set the story status to "In review"
   - generate the handoff with `scripts/session-summary.sh --command implement --issue $ARGUMENTS --pr <pr-number> --head <current-head-sha> --checks <checks> --blockers <blockers> --next review` and post its exact stdout
   - use repeatable `--deviation <text>` flags when deviations, residual risks, or decisions must be recorded
   - do not add any section after the generated Session Summary block; the PR body is the implementation document, so do not append “What was implemented”, “AC coverage”, or equivalent recap prose
17. If you are addressing review blockers on an existing PR:
   - treat any earlier approval as stale once a new commit is pushed
   - never leave the story in "In merge" after a new implementation commit
   - return the story to "In review" only when the new head SHA is ready for review

Do not merge the PR.
Do not perform review approval.
Record workflow-relevant facts in GitHub, not only in chat.

---

## Required GitHub Output — Must Not Be Skipped

**Draft PR created early** using the available GitHub operation fallback (draft: true), linked to the issue.
**Handoff comment posted on the PR** at completion using the available GitHub operation fallback.

Generate the handoff with `scripts/session-summary.sh --command implement` and all required phase arguments, then post the exact stdout. Do not add prose or sections after the generated block.
