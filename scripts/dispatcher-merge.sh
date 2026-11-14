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
# reason is on stderr. Exit 2: invalid invocation. Exit 3: the PR changes
# decision documents (ADR-011); the PO merges it personally. The reason on
# stderr ends with "Files: <file> <file> ..." (issue #502).
#
# Anything this script cannot judge blocks the merge (fail closed). It never
# writes the board: a blocked story stays in the merge step (one attempt per
# head, #293), and the board writer (`hpw dispatcher status sync`, #478) shows
# "Done" after a merge.
#
# Overridable for tests: HPW_BIN, MERGE_GATE_SCRIPT, SESSION_SUMMARY_SCRIPT,
# COMMENT_RETRY_DELAY (read by `hpw dispatcher write`).

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

# hpw's stderr is kept for the block message, never dropped (issue #374).
hpw_err=$(mktemp) || { echo "Error: could not create a temporary file." >&2; exit 1; }
trap 'rm -f "$hpw_err"' EXIT

# Quoted output is bounded so the block message stays readable: last 20 lines,
# at most 2000 bytes.
bounded() { tail -n 20 | tail -c 2000; }

# Blocks on an `hpw <sub> --json` failure that carries no blocking reasons:
# hpw's gh error path ({"ok": false, "error", "returncode", "stderr"}) names the
# gh error; anything else is not a usable result and quotes hpw's own stderr
# (its stdout when stderr is empty, for example a traceback).
block_hpw_failure() {  # <sub> <gh-error prefix> <rc> <stdout>
    local sub="$1" prefix="$2" rc="$3" out="$4" error gh_rc gh_err detail
    error=$(jq -r 'select(type == "object" and .ok == false and (.error | type == "string") and .error != "") | .error'         <<< "$out" 2>/dev/null | head -n 1)
    if [[ -n "$error" ]]; then
        gh_rc=$(jq -r 'select(type == "object") | .returncode // "unknown"' <<< "$out" 2>/dev/null | head -n 1)
        gh_err=$(jq -r 'select(type == "object") | .stderr // ""' <<< "$out" 2>/dev/null | bounded)
        block "$prefix: $error (gh exit ${gh_rc:-unknown}): ${gh_err:-(no stderr)}"
    fi
    detail=$(bounded < "$hpw_err")
    [[ -n "$detail" ]] || detail=$(bounded <<< "$out")
    block "hpw $sub failed (exit $rc) without a usable result: ${detail:-(no output)}"
}

# The blocking reasons of an hpw --json result, joined; empty when there are none.
blocking_reasons() {
    jq -r 'select(type == "object") | (.blocking_reasons // []) | select(type == "array" and length > 0) | map(tostring) | join("; ")'         <<< "$1" 2>/dev/null | head -n 1
}

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
    echo "/merge blocked for PR #$PR: the PR changes decision documents (ADR-011); the PO merges such PRs personally in GitHub. Files: $(tr '\n' ' ' <<< "$decision_files" | sed 's/ *$//')" >&2
    exit 3
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
# head merges it. The durable evidence is the owner marker the dispatcher posts
# on the PR right after its own /implement session for this exact head SHA
# (scripts/dispatcher-invoke.sh, record_merge_owner). It counts only on a comment
# written by the dispatcher's own account, so a manual /implement handoff or any
# other commenter cannot supply it. Without it the owner is unknown and
# `hpw merge-owner` blocks. A takeover needs a recorded human/operator decision,
# which this script cannot supply, so it never merges on takeover.
OWNER_ID="dispatcher"
owner_args=(--session-id "$OWNER_ID")
# Block 2 of ADR-014 (`hpw dispatcher marker holds`, issue #433) reads the
# marker: exit 0 holds, 1 does not, 3 the account or the comments could not be
# read (fail closed: no merge).
marker_note="; no dispatcher owner marker for head ${head_sha:0:7}"
holds_rc=0
holds_out=$("$HPW_BIN" dispatcher marker holds --repo "$REPO" --number "$PR" \
    --marker "action=merge-owner pr=$PR head=$head_sha -->" 2>"$hpw_err") || holds_rc=$?
case "$holds_rc" in
    0) owner_args+=(--expected-owner "$OWNER_ID" --expected-owner-sha "$head_sha")
       marker_note="" ;;
    1) ;;
    *) block "the dispatcher account or the PR comments could not be read, so merge ownership cannot be verified ($(jq -r '.error // "unreadable"' <<< "$holds_out" 2>/dev/null))." ;;
esac
owner_out=$("$HPW_BIN" merge-owner "$PR" --repo "$REPO" "${owner_args[@]}" --json 2>"$hpw_err")
owner_rc=$?
if [[ $owner_rc -ne 0 ]]; then
    # The status and reasons are named only when hpw reported them; a gh error
    # or an unusable result is never shown as an ownership failure (#374).
    reasons=$(blocking_reasons "$owner_out")
    if [[ -n "$reasons" ]]; then
        owner_status=$(jq -r 'select(type == "object") | .ownership_status // "unknown"' <<< "$owner_out" 2>/dev/null | head -n 1)
        block "merge ownership gate not passed (status: ${owner_status:-unknown}${marker_note}). ${reasons}"
    fi
    block_hpw_failure merge-owner "hpw merge-owner could not evaluate ownership" "$owner_rc" "$owner_out"
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
merge_json=$("$HPW_BIN" merge-pr "$PR" --repo "$REPO" --method "$METHOD" --execute --expect-head "$head_sha" --json 2>"$hpw_err")
merge_rc=$?
if [[ $merge_rc -ne 0 ]] || [[ "$(jq -r '.merged // false' <<< "$merge_json" 2>/dev/null)" != "true" ]]; then
    reasons=$(blocking_reasons "$merge_json")
    [[ -z "$reasons" ]] || block "hpw merge-pr did not confirm a merge (exit $merge_rc). ${reasons}"
    block_hpw_failure merge-pr "hpw merge-pr did not confirm a merge (exit $merge_rc)" "$merge_rc" "$merge_json"
fi
merged_head=$(jq -r '.head_sha // empty' <<< "$merge_json")
[[ -n "$merged_head" ]] || merged_head="$head_sha"
echo "✓ PR #$PR merged at head ${merged_head:0:7}"

# Merge confirmation comment: the exact gate line, then the generated summary.
# It is durable evidence and the merged PR is never merged again, so a failure to
# post it (after retries) fails the phase; the full text goes to the log. The
# verified write (`hpw dispatcher write comment --evidence-item`, issue #432) also
# leaves the exact text on the PR as an evidence-pending comment (issue #348), so
# a later dispatcher run posts it once, even though the PR is already merged.
summary=$(bash "$SESSION_SUMMARY_SCRIPT" --command merge --pr "$PR" --head "$merged_head" \
        --checks "merge gate pass (scripts/merge-gate-summary), hpw merge-pr --execute ($METHOD)" \
        --blockers none --next cleanup) \
    || { echo "PR #$PR is merged, but the merge Session Summary could not be generated; no confirmation comment posted." >&2; exit 1; }
confirmation="$gate_line"$'\n\n'"$summary"
# The verified write (ADR-014 → Block 1, issue #432) tries it up to 3 times,
# reads it back, and leaves the evidence-pending copy when it still fails.
body_file=$(mktemp) || { echo "PR #$PR is merged, but a temporary file for the confirmation could not be created." >&2; exit 1; }
printf '%s' "$confirmation" > "$body_file"
write_rc=0
"$HPW_BIN" dispatcher write comment --repo "$REPO" --number "$PR" --body-file "$body_file" \
    --evidence-item merge-confirmation --evidence-head "$merged_head" >/dev/null || write_rc=$?
rm -f "$body_file"
if [[ $write_rc -ne 0 ]]; then
    echo "PR #$PR is merged, but the merge confirmation comment could not be posted after 3 attempts. A later dispatcher run posts it; or post it manually:" >&2
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
