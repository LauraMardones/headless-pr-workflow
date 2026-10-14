#!/usr/bin/env bash
# scripts/dispatcher-cleanup.sh — /cleanup as a deterministic script (issue #264).
#
# The dispatcher calls this instead of starting a model session for /cleanup.
# It ports .claude/commands/cleanup.md; docs/DISPATCHER-MERGE-CLEANUP-MAP.md maps
# each step to a branch below and to a test. cleanup.md stays the source of
# truth for manual runs: an edit to it needs the matching edit here.
#
# Usage: dispatcher-cleanup.sh --issue <N> --repo <owner/name> [--pr <N>]
#
# The merged PR is the one given by --pr, or the newest merged PR whose body
# closes the issue. The sync and branch decisions belong to `hpw post-merge-sync`
# and `hpw branch-cleanup`, which classify the local state and refuse every
# unsafe action. Anything they keep (dirty, unrelated, ambiguous, in use, with
# unique content) is reported with its safe command and is not a failure.
#
# Exit 0: cleanup ran; kept items are reported. Exit 1: the PR is not confirmed
# merged, or a step failed. Exit 2: invalid invocation. Nothing is forced.
#
# Overridable for tests: HPW_BIN, SESSION_SUMMARY_SCRIPT.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HPW_BIN="${HPW_BIN:-$SCRIPT_DIR/hpw}"
SESSION_SUMMARY_SCRIPT="${SESSION_SUMMARY_SCRIPT:-$SCRIPT_DIR/session-summary.sh}"

ISSUE="" PR="" REPO=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --issue) ISSUE="${2:-}"; shift 2 ;;
        --pr)    PR="${2:-}"; shift 2 ;;
        --repo)  REPO="${2:-}"; shift 2 ;;
        *) echo "Error: unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ "$ISSUE" =~ ^[0-9]+$ ]] || { echo "Error: --issue must be an issue number." >&2; exit 2; }
[[ -z "$PR" || "$PR" =~ ^[0-9]+$ ]] || { echo "Error: --pr must be a PR number." >&2; exit 2; }
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "Error: --repo must be owner/name." >&2; exit 2; }

fail() { echo "/cleanup stopped for #$ISSUE: $*" >&2; exit 1; }

# Step 1: GitHub says the PR is merged, before anything local is touched.
if [[ -z "$PR" ]]; then
    PR=$(gh api "repos/$REPO/pulls?state=closed&sort=updated&direction=desc&per_page=100" 2>/dev/null \
        | jq -r --argjson n "$ISSUE" '
            map(select(.merged_at != null and
                ((.body // "") | ascii_downcase | test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b"))))
            | first | .number // empty') || PR=""
fi
[[ -n "$PR" ]] || fail "no merged PR that closes #$ISSUE was found on GitHub; nothing was changed."
merged=$(gh api "repos/$REPO/pulls/$PR" --jq '(.merged_at != null)|tostring' 2>/dev/null) || merged=""
[[ "$merged" == "true" ]] || fail "GitHub does not report PR #$PR as merged; nothing was changed."
echo "PR #$PR is merged on GitHub; cleaning up for #$ISSUE."

failed=()
kept=()
manual=()
did=()

# Steps 2-6: the local base branch (stale copy of the PR, fast-forward, verify).
sync_json=$("$HPW_BIN" post-merge-sync "$PR" --repo "$REPO" --execute --json 2>/dev/null)
if ! jq -e . >/dev/null 2>&1 <<< "$sync_json"; then
    failed+=("post-merge-sync produced no report")
else
    classification=$(jq -r '.classification' <<< "$sync_json")
    failed_step=$(jq -r '.execution.failed_step // empty' <<< "$sync_json")
    echo "Local base branch: $classification"
    if [[ -n "$failed_step" ]]; then
        failed+=("post-merge-sync failed at: $failed_step")
    elif jq -e '.ok' >/dev/null <<< "$sync_json"; then
        did+=("local base branch: $classification")
    else
        kept+=("local base branch kept ($classification): $(jq -r '.blocking_reasons | join("; ")' <<< "$sync_json")")
    fi
    while IFS= read -r line; do [[ -n "$line" ]] && manual+=("$line"); done < <(jq -r '.manual_commands[]?' <<< "$sync_json")
fi

# Steps 7-15: local branches, worktrees and the remote topic branch.
branch_json=$("$HPW_BIN" branch-cleanup "$PR" --repo "$REPO" --execute --json 2>/dev/null)
if ! jq -e . >/dev/null 2>&1 <<< "$branch_json"; then
    failed+=("branch-cleanup produced no report")
else
    while IFS= read -r line; do [[ -n "$line" ]] && did+=("deleted: $line"); done < <(jq -r '.deleted[]?' <<< "$branch_json")
    while IFS= read -r line; do [[ -n "$line" ]] && kept+=("$line"); done < <(jq -r '.kept[]? | "\(.branch) kept: \(.reason)"' <<< "$branch_json")
    while IFS= read -r line; do [[ -n "$line" ]] && manual+=("$line"); done < <(jq -r '.manual_commands[]?' <<< "$branch_json")
fi

# Step 16: the end-of-run report.
echo "── /cleanup report for #$ISSUE (PR #$PR) ──"
echo "Done:";   [[ ${#did[@]} -gt 0 ]]    && printf '  - %s\n' "${did[@]}"    || echo "  - nothing"
echo "Kept:";   [[ ${#kept[@]} -gt 0 ]]   && printf '  - %s\n' "${kept[@]}"   || echo "  - nothing"
echo "Manual follow-up:"; [[ ${#manual[@]} -gt 0 ]] && printf '  %s\n' "${manual[@]}" || echo "  none"
[[ ${#failed[@]} -gt 0 ]] && { echo "Failed:"; printf '  - %s\n' "${failed[@]}"; }

# Closing comment on the issue.
if [[ ${#failed[@]} -gt 0 ]]; then blockers="${failed[*]}"; else blockers="none"; fi
checks="hpw post-merge-sync --execute, hpw branch-cleanup --execute"
[[ ${#kept[@]} -gt 0 ]] && checks+="; kept: ${#kept[@]} item(s), reported in the dispatcher log"
if summary=$(bash "$SESSION_SUMMARY_SCRIPT" --command cleanup --issue "$ISSUE" --pr "$PR" \
        --checks "$checks" --blockers "$blockers" --next done); then
    gh api "repos/$REPO/issues/$ISSUE/comments" -X POST -f body="$summary" >/dev/null \
        || echo "Warning: the closing comment on #$ISSUE could not be posted." >&2
else
    echo "Warning: the cleanup Session Summary could not be generated." >&2
fi

[[ ${#failed[@]} -eq 0 ]] || exit 1
exit 0
