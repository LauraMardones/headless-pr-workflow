# Worktree Model

Worktrees and branches are temporary execution contexts. They help assistants and humans work safely, but they are not the source of truth.

## Principles

- A worktree belongs to a task, PR, assistant, and session.
- Worktrees may be deleted after work is pushed or abandoned.
- Unpushed local commits are not durable workflow state.
- Fresh GitHub context must be fetched at session start and before merge.
- Worktree naming should make accidental cross-session work obvious.

## Naming

Recommended branch pattern:

```text
hpw/<issue-or-pr>-<short-slug>
```

Recommended worktree pattern:

```text
../wt/<repo>/<pr-number>-<assistant>-<session-id>
```

Examples:

```text
hpw/123-sha-bound-approval
../wt/example-repo/123-codex-20260421-0915
../wt/example-repo/123-claude-review-20260421-1010
```

Repo adapters may override branch conventions when a project already has established naming policy.

## Session Start

At session start:

- Fetch remote refs.
- Identify current repo and branch.
- Fetch PR context from GitHub.
- Confirm whether the local branch tracks the PR branch.
- Confirm whether the current worktree has uncommitted or unpushed changes.
- Determine next action from GitHub state.

`hpw worktree-status [path]` is the read-only local-state report for these checks. It inspects the Git worktree containing the supplied path, reports branch, HEAD, upstream, ahead/behind, dirty-state, unpushed commit, and linked-worktree facts, and does not fetch or mutate local or GitHub state. Ahead/behind counts are relative to the local upstream tracking ref and may be stale until another command performs an explicit fetch.

## Cleanup

Worktrees can be cleaned when:

- The PR is merged and local sync is complete.
- The branch is abandoned and no unpushed work remains.
- A newer takeover worktree supersedes the old one.

Cleanup must not delete unpushed work without explicit confirmation.

## Post-Merge Cleanup

### When not to switch or pull automatically

After a PR is merged on GitHub, do not immediately switch branches or run `git pull` if the local worktree is dirty. Unsaved or uncommitted local changes may be lost or silently overwritten. Always inspect local state first.

### Distinguishing merged-work cleanup from unrelated local changes

Two different situations can produce a dirty worktree after a PR merges:

1. **Stale local copy of already-merged PR changes.** The worktree still contains the PR's changes as working-tree modifications. The same content already exists in `main` on GitHub through the merge commit. This is not new work — it is the old working-tree copy that was never cleaned up after the push.
2. **Unrelated local changes.** The worktree contains modifications that have nothing to do with the merged PR. These must be preserved; do not restore or discard them without explicit confirmation.

These two cases look similar in `git status` output. Before taking any action, confirm which case applies by comparing the dirty diff against the merged PR.

### The stale-local-copy case

After a PR merges, local files that match the PR's changes will appear modified in `git status` even though the same content now exists in `main` on GitHub. This happens because Git marks files as modified relative to the local `HEAD`, not relative to the upstream state. The local `HEAD` has not been fast-forwarded yet, so Git sees the PR content in the working tree as a local modification.

**This is not a local-ahead conflict.** The working tree is being compared against a stale local `HEAD`. No new commit exists locally; the content is simply a leftover copy of the PR's working-tree state.

### The `## main...origin/main` caveat

Running `git status --short --branch` and seeing `## main...origin/main` without an `[ahead]` or `[behind]` indicator does not prove that local tracking data is fresh. That output reflects the last-fetched remote state. A `git fetch` or `git pull` may still be needed to bring the local tracking ref up to date with the actual state of `origin/main` after the merge.

### Safe sequence for verified stale-copy cleanup

Only use this sequence after confirming that the dirty diff matches the merged PR content. If the diff contains unrelated or ambiguous work, stop and preserve it instead.

```powershell
# 1. Backup the dirty working-tree content
git diff HEAD -- <pr-related-paths> | Out-File -FilePath .hpw-post-merge-backup.patch -Encoding utf8

# 2. Restore PR paths from local HEAD (removes stale working-tree copy)
git restore -- <pr-related-paths>

# 3. Fast-forward local base branch from remote
git pull --ff-only origin <base-branch>

# 4. Verify the worktree is clean
git status --short --branch

# 5. Remove the temporary backup after successful verification
Remove-Item -LiteralPath .\.hpw-post-merge-backup.patch
```

**Precondition:** confirm the dirty diff matches the merged PR content before running any restore or pull step. Do not automate this sequence until that confirmation is complete.

**Do not commit local copies of already-merged changes.** If the stale working-tree content is committed locally, the result is a redundant commit that re-introduces content already present in `main` through the merge commit. This creates a false-ahead local branch and will cause confusion at the next sync.

### Cleanup of temporary files

After the sequence completes successfully, remove:

- The temporary backup patch (`.hpw-post-merge-backup.patch` or whatever name was used).
- Any accidental redirected diff files (e.g., files created by `git diff > somefile.diff` during investigation) that are not part of the tracked project.

Leaving these files in the worktree will cause `git status` to show an untracked file, which makes clean-state verification harder.

### Relation to `hpw post-merge-sync`

The `hpw post-merge-sync` command (implemented in issue #42) automates this same verified stale-copy sequence: backup → restore PR paths → fast-forward → verify clean status → remove backup. The manual sequence above matches the steps the command performs when it classifies the local state as `verified_stale_pr_copy`.

Until you are ready to use the command, the manual sequence above is the safe fallback. If `post-merge-sync` is available and the local state is unambiguous, prefer the command to reduce the risk of manual error.
