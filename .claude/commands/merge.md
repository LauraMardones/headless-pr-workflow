Merge PR #$ARGUMENTS in LauraMardones/headless-pr-workflow using GitHub as source of truth.

Treat this as a merge-owner session.

Follow:
- docs/MERGE-POLICY.md

Workflow status goal:
- If merge succeeds and all remaining Definition of Done criteria are satisfied: "In merge" -> "Done"

Required behavior:
1. Do a fresh GitHub refresh immediately before merging.
2. Verify:
   - current PR head SHA
   - approval source
   - review state
   - required checks
   - target branch
   - unresolved blockers
   - draft state
3. Actively check GitHub review evidence in the correct PR review surfaces, not only issue comments or a narrow approval summary view.
4. For approval, accept either:
   - a formal GitHub approval on the current head SHA, or
   - a valid solo-maintainer override recorded on the current head SHA according to docs/MERGE-POLICY.md
5. Merge only if the relied-on approval source still applies to the current PR head SHA and all merge gates pass.
6. If any merge gate is stale, missing, ambiguous, or failing:
   - stop
   - report the blocker instead of merging
   - if new implementation is required, set the story status to "In progress"
   - if only renewed review is required, set the story status to "In review"
7. If local state is dirty or unrelated work exists, report the safe next step instead of forcing sync or cleanup.
8. Do not rely on local branch state unless it is required for a documented post-merge follow-up.
9. If all merge gates pass:
   - merge the PR
   - confirm merged state in GitHub
   - confirm any remaining Definition of Done criteria
   - set the story status to "Done"
   - report any safe post-merge follow-up needed

Safe post-merge cleanup:
10. Perform only trivial and low-risk cleanup in this session.
11. You may delete the local topic branch only if all of the following are true:
   - the PR is confirmed merged
   - the branch is not checked out
   - no other worktree uses the branch
   - local git state is clean
   - git clearly recognizes the branch as safely merged without ambiguity
12. Do not remove worktrees in this session.
13. Do not delete remote branches in this session.
14. Do not force cleanup, reset, or sync.
15. If cleanup is unsafe, ambiguous, blocked by permissions, or complicated by squash merge semantics, stop cleanup and report that a separate post-merge cleanup session is needed.

Treat GitHub as authoritative for the merge decision.
Never merge a stale or ambiguously approved head SHA.

---

## Required GitHub Output — Must Not Be Skipped

**Merge confirmation comment posted on the merged PR** using `mcp__github__add_issue_comment`.

Post the confirmation comment using this structure (omit inapplicable fields):

```
## Session Summary
Command: merge
Issue / PR: #<pr-number>
Head SHA: <merged-sha>
Checks run: <list, or "none">
Next action: cleanup
```
