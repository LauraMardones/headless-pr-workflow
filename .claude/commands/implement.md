Implement issue #$ARGUMENTS in LauraMardones/headless-pr-workflow using GitHub as source of truth.

You are acting only as implementer in this session.
Do not review or approve the same PR head SHA that you implement in this session.

Follow core workflow policy and keep repo-specific or governance rules out of core commands.

Workflow status goals:
- Create and link a PR as early as possible.
- PR linked to issue -> story status should be "In progress".
- When implementation is ready for review -> story status should be "In review".
- If you are fixing review blockers on an existing PR, keep the story in "In progress" while fixes are underway, then move it back to "In review" only when the new head SHA is ready for review.

Required behavior:
1. Read the GitHub issue, acceptance criteria, linked context, and any existing PR state first.
2. Use a dedicated branch for this story only.
3. Keep the work strictly within the issue scope and acceptance criteria.
4. If the issue is dependency-heavy, verify that hard dependencies are complete before implementation starts.
5. If you discover blocked prerequisites, unclear acceptance criteria, scope drift, or unresolved dependency decisions:
   - stop
   - report the blocker clearly
   - do not silently expand scope
6. If no PR exists yet:
   - create a Draft PR as early as possible
   - link the PR to issue #$ARGUMENTS
   - ensure the PR references and closes #$ARGUMENTS when merged
   - ensure the story moves to "In progress"
7. If a PR already exists:
   - continue on the existing PR branch
   - update the existing PR instead of opening a parallel PR unless explicitly needed
   - keep the story in "In progress" while implementation or blocker fixes are ongoing
8. Implement only the requested changes.
9. Add or update deterministic tests as needed.
10. For mutation work, preserve fresh-refresh and safety checks before mutating behavior.
11. Push commits to the same PR branch as work progresses.
12. When implementation is complete:
   - run the most relevant documented local checks
   - record any checks that could not be run and why
   - commit only the intended changes
   - confirm the current PR head SHA from GitHub
   - mark the PR Ready for review, or explicitly request review if Ready for review is unavailable
   - set the story status to "In review"
   - leave a concise GitHub handoff note with:
     - PR number
     - current head SHA
     - checks run
     - blockers or risks
     - suggested next action: review
13. If you are addressing review blockers on an existing PR:
   - treat any earlier approval as stale once a new commit is pushed
   - never leave the story in "In merge" after a new implementation commit
   - return the story to "In review" only when the new head SHA is ready for review

Do not merge the PR.
Do not perform review approval.
Record workflow-relevant facts in GitHub, not only in chat.
