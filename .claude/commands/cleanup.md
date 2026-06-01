Clean up post-merge branch, worktree, and local base-branch state for PR #$ARGUMENTS in LauraMardones/headless-pr-workflow.

Treat this as a post-merge cleanup session, not a merge-decision session.

Use GitHub as source of truth for whether the PR is merged. Use local git state carefully for cleanup decisions.

Goals:
- confirm the PR is merged
- replace stale local working-tree copies of already-merged PR changes with the real GitHub main history
- remove obsolete local story branches when safe
- remove obsolete local worktrees when safe
- optionally remove the remote topic branch when safe
- avoid deleting anything that may still contain unique or unmerged work

Required behavior:

1. First confirm from GitHub that the PR is actually merged.

2. Identify:
   - PR branch name
   - PR head SHA
   - merge commit SHA or resulting base-branch SHA
   - target/base branch
   - related issue state
   - local branches associated with the story
   - local worktrees associated with the story
   - local working-tree status

3. If the local base branch is dirty, distinguish between:
   - stale local copies of the already-merged PR changes
   - unrelated local work
   - ambiguous or unique local changes

4. Do not stop merely because the local worktree is dirty.

5. If dirty files appear to match the merged PR content and local base is stale:
   - create a patch backup of those dirty files
   - restore only those PR-related paths
   - fast-forward the local base branch from GitHub
   - verify the same changes return from Git history
   - verify the worktree is clean except for the temporary patch backup
   - remove the temporary patch backup after successful verification

6. If dirty files are unrelated, ambiguous, or contain unique local work:
   - do not restore them
   - do not remove the worktree
   - report the safe next step

7. Check whether any relevant local branch is:
   - currently checked out
   - used by another worktree
   - dirty through an associated worktree
   - clearly merged into the updated base branch

8. If a local worktree is dirty, in use, or ambiguous:
   - do not remove it
   - report the safe next step

9. If a local branch appears not to be merged because the PR used squash merge or another non-fast-forward strategy:
   - do not assume it is unsafe
   - compare effective content against the updated base branch
   - determine whether any unique changes still remain on the branch

10. Only delete a local branch if one of the following is true:
   - git clearly recognizes it as merged into the updated base branch
   - content verification shows the branch contains no unique remaining changes relative to the merged base branch

11. Only delete a remote branch if:
   - GitHub confirms the PR is merged
   - GitHub compare shows the topic branch has no unique remaining changes relative to the updated base branch, or the branch is otherwise clearly contained by the merge
   - deletion is unambiguous and safe

12. Remove local worktrees only when they are:
   - clean
   - unused
   - clearly tied to the merged story branch
   - not the current worktree

13. Never force-delete ambiguous, dirty, or potentially unique work.

14. Never use destructive cleanup as a shortcut around uncertainty.

15. If permissions or sandboxing block a legitimate cleanup step:
   - report exactly which step is blocked
   - provide the exact safe command for the user to run manually
   - continue with any remaining non-blocked verification

16. At the end, report:
   - what was deleted
   - what was kept
   - what was restored or fast-forwarded
   - why anything was left in place
   - whether any manual follow-up remains

Important cleanup pattern:

If local main has uncommitted modifications that are exactly the merged PR changes, the correct cleanup is usually:

- back up the diff
- restore those files
- fast-forward local main from origin/main
- verify clean status

Do not create a new local commit for changes that are already merged on GitHub.
Do not treat stale local PR diffs as unique work once they have been verified against the merged PR.

---

## Required GitHub Output — Must Not Be Skipped

**Closing comment posted on the closed issue** using `mcp__github__add_issue_comment`.

Post the closing comment using this structure (omit inapplicable fields):

```
## Session Summary
Command: cleanup
Issue / PR: #<issue-number>
Checks run: <list, or "none">
Next action: none
```
