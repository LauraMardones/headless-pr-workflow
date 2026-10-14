#!/usr/bin/env bash
# scripts/dispatcher-merge.sh — /merge as a deterministic script (issue #264).
#
# The dispatcher calls this instead of starting a model session for /merge.
# It ports .claude/commands/merge.md step by step; docs/DISPATCHER-MERGE-CLEANUP-MAP.md
# maps each step to a branch below and to a test. merge.md stays the source of
# truth for manual runs: an edit to it needs the matching edit here.
#
# Usage: dispatcher-merge.sh --pr <N> --repo <owner/name> [--method squash|merge|rebase]
#
# Exit 0: the PR is merged (now or already). Exit 1: blocked or failed; the
# reason is on stderr. Exit 2: invalid invocation.
#
# Anything this script cannot judge blocks the merge (fail closed). It never
# writes the board: a blocked story stays "In merge" (one attempt per head,
# #293) and the dispatcher's finish_story writes "Done" after a merge.
#
# Overridable for tests: HPW_BIN, MERGE_GATE_SCRIPT, SESSION_SUMMARY_SCRIPT,
# COMMENT_RETRY_DELAY.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HPW_BIN="${HPW_BIN:-$SCRIPT_DIR/hpw}"
MERGE_GATE_SCRIPT="${MERGE_GATE_SCRIPT:-$SCRIPT_DIR/merge-gate-summary}"
SESSION_SUMMARY_SCRIPT="${SESSION_SUMMARY_SCRIPT:-$SCRIPT_DIR/session-summary.sh}"

PR="" REPO="" METHOD="squash"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --pr)     PR="${2:-}"; shift 2 ;;
        --repo)   REPO="${2:-}"; shift 2 ;;
        --method) METHOD="${2:-}"; shift 2 ;;
        *) echo "Error: unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ "$PR" =~ ^[0-9]+$ ]] || { echo "Error: --pr must be a PR number." >&2; exit 2; }
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "Error: --repo must be owner/name." >&2; exit 2; }
case "$METHOD" in squash|merge|rebase) ;; *) echo "Error: invalid --method: $METHOD" >&2; exit 2 ;; esac

block() { echo "/merge blocked for PR #$PR: $*" >&2; exit 1; }

# GraphQL pre-flight: before any other step and before any mutation.
if ! command -v gh >/dev/null 2>&1; then
    block "merging requires GraphQL-capable gh (2.50 or later) to run the merge gate; gh was not found. GitHub Actions or a local session provides it. No GitHub mutation was made."
fi
if ! gh api graphql -f query='{ viewer { login } }' >/dev/null 2>&1; then
    block "merging requires GraphQL-capable gh (2.50 or later) to run the merge gate; the GraphQL call failed (for example HTTP 403). GitHub Actions or a local session provides it. No GitHub mutation was made."
fi

# Decision documents (ADR-011): the PO merges these personally.
if ! files=$(gh api --paginate "repos/$REPO/pulls/$PR/files?per_page=100" --jq '.[].filename' 2>/dev/null); then
    block "the changed files of the PR could not be read, so the decision-document rule cannot be checked."
fi
decision_files=$(grep -E '^(specs/[0-9]+-[^/]+/|docs/decisions/ADR-[^/]*\.md$)' <<< "$files" || true)
if [[ -n "$decision_files" ]]; then
    block "the PR changes decision documents (ADR-011); the PO merges such PRs personally in GitHub. Files: $(tr '\n' ' ' <<< "$decision_files")"
fi

# Fresh refresh: state, draft flag and head, read immediately before the gate.
if ! info=$(gh api "repos/$REPO/pulls/$PR" --jq '[.state, (.draft|tostring), ((.merged_at != null)|tostring), .head.sha, .head.ref] | @tsv' 2>/dev/null) \
        || [[ -z "$info" ]]; then
    block "the PR could not be read from GitHub."
fi
IFS=$'\t' read -r state draft merged head_sha head_ref <<< "$info"
if [[ "$merged" == "true" ]]; then
    echo "PR #$PR is already merged; nothing to merge."
    exit 0
fi
[[ "$state" == "open" ]] || block "the PR is $state, not open."
[[ "$draft" != "true" ]] || block "the PR is a draft."
[[ "$head_sha" =~ ^[0-9a-f]{40}$ ]] || block "the PR head SHA could not be verified."

# Merge ownership (docs/MERGE-POLICY.md): the session that produced the approved
# head merges it. The durable evidence is the /implement handoff comment for this
# exact head SHA; without one the owner is unknown and `hpw merge-owner` blocks.
# A takeover needs a recorded human/operator decision, which this script cannot
# supply, so it never merges on takeover.
OWNER_ID="dispatcher"
owner_args=(--session-id "$OWNER_ID")
if ! comment_bodies=$(gh api --paginate "repos/$REPO/issues/$PR/comments?per_page=100" --jq '.[].body' 2>/dev/null); then
    block "the PR comments could not be read, so merge ownership cannot be verified."
fi
if grep -qxF "Head: $head_sha" <<< "$comment_bodies" && grep -qxF "Command: implement" <<< "$comment_bodies"; then
    owner_args+=(--expected-owner "$OWNER_ID" --expected-owner-sha "$head_sha")
fi
if ! owner_out=$("$HPW_BIN" merge-owner "$PR" --repo "$REPO" "${owner_args[@]}" --json 2>/dev/null); then
    reasons=$(jq -r '(.blocking_reasons // []) | join("; ")' <<< "$owner_out" 2>/dev/null)
    block "merge ownership gate not passed (no /implement handoff for head ${head_sha:0:7}, or takeover required). ${reasons}"
fi

# Merge gate, run against the freshly refreshed evidence. Only exit 0 merges.
gate_line=$(python3 "$MERGE_GATE_SCRIPT" --pr "$PR" --repo "$REPO" 2>/dev/null)
gate_rc=$?
echo "$gate_line"
if [[ $gate_rc -ne 0 ]]; then
    block "merge gate exit $gate_rc (1: a gate does not pass; 2: the PR/head could not be safely evaluated)."
fi
gate_head=$(sed -n 's/.*head=\([0-9a-fA-F]\{7\}\).*/\1/p' <<< "$gate_line" | head -n 1)
if [[ -z "$gate_head" || "${head_sha:0:7}" != "${gate_head,,}" ]]; then
    block "the gate was evaluated for head '${gate_head:-unknown}', not the refreshed head ${head_sha:0:7}."
fi

# The merge. hpw merge-pr re-reads readiness and merges only the verified head.
merge_json=$("$HPW_BIN" merge-pr "$PR" --repo "$REPO" --method "$METHOD" --execute --json 2>/dev/null)
merge_rc=$?
if [[ $merge_rc -ne 0 ]] || [[ "$(jq -r '.merged // false' <<< "$merge_json" 2>/dev/null)" != "true" ]]; then
    reasons=$(jq -r '(.blocking_reasons // []) | join("; ")' <<< "$merge_json" 2>/dev/null)
    block "hpw merge-pr did not confirm a merge (exit $merge_rc). ${reasons}"
fi
merged_head=$(jq -r '.head_sha // empty' <<< "$merge_json")
[[ -n "$merged_head" ]] || merged_head="$head_sha"
echo "✓ PR #$PR merged at head ${merged_head:0:7}"

# Merge confirmation comment: the exact gate line, then the generated summary.
# It is durable evidence and the merged PR is never merged again, so a failure to
# post it (after retries) fails the phase; the full text goes to the log.
summary=$(bash "$SESSION_SUMMARY_SCRIPT" --command merge --pr "$PR" --head "$merged_head" \
        --checks "merge gate pass (scripts/merge-gate-summary), hpw merge-pr --execute ($METHOD)" \
        --blockers none --next cleanup) \
    || { echo "PR #$PR is merged, but the merge Session Summary could not be generated; no confirmation comment posted." >&2; exit 1; }
confirmation="$gate_line"$'\n\n'"$summary"
posted=false
for attempt in 1 2 3; do
    if gh api "repos/$REPO/issues/$PR/comments" -X POST -f body="$confirmation" >/dev/null; then
        posted=true; break
    fi
    sleep "${COMMENT_RETRY_DELAY:-2}"
done
if ! $posted; then
    echo "PR #$PR is merged, but the merge confirmation comment could not be posted after 3 attempts. Post it manually:" >&2
    echo "$confirmation" >&2
    exit 1
fi

# Safe post-merge cleanup (merge.md steps 10-15): the local topic branch only.
# No worktree removal, no remote deletion, never forced; anything unclear is
# left for the cleanup step.
delete_local_branch_if_safe() {
    local branch="$1"
    [[ -n "$branch" ]] || return 0
    git rev-parse --git-dir >/dev/null 2>&1 || return 0
    git show-ref --verify --quiet "refs/heads/$branch" || return 0
    if [[ "$(git symbolic-ref --short -q HEAD 2>/dev/null)" == "$branch" ]]; then
        echo "Kept local branch $branch: it is checked out."; return 0
    fi
    if git worktree list --porcelain | grep -qx "branch refs/heads/$branch"; then
        echo "Kept local branch $branch: another worktree uses it."; return 0
    fi
    if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
        echo "Kept local branch $branch: local git state is not clean."; return 0
    fi
    if git branch -d "$branch" >/dev/null 2>&1; then
        echo "Deleted local branch $branch."
    else
        echo "Kept local branch $branch: git does not recognize it as safely merged; a separate cleanup session is needed."
    fi
}
delete_local_branch_if_safe "$head_ref"
exit 0
