#!/usr/bin/env bash
# scripts/dispatcher-poll.sh
#
# Reads the work in flight from the GitHub Projects v2 board and asks the
# scheduler (`hpw dispatcher scheduler next`, issue #473) for the next
# new-work action, then outputs one JSON object the workflow decides from.
# New work never comes from a board status: the poll reads no "Ready for
# implementation" or "Ready for refinement" status (issue #474, ADR-010).
# Contract: issue #170 (initial implementation), issue #180 (Red flow health
#           notification), issue #474 (new work from the scheduler; the
#           ready-for-refinement notification of issue #182 is retired)
#
# Usage:
#   GH_TOKEN=<token> bash scripts/dispatcher-poll.sh --repo <owner/repo> [--dry-run]
#
# Environment (optional):
#   SLACK_WEBHOOK_URL  Required by slack-notify.sh for the Red flow health notification.
#   BOARD_URL          GitHub Projects board URL included in Red flow health notifications.
#                      Falls back to "" if unset; slack-notify.sh renders "(no URL)" gracefully.
#   WIP_LIMIT_IMPLEMENTATION, READY_BUFFER_MIN, READY_BUFFER_MAX
#                      The limits the scheduler and flow-review.sh read (issue #358).
#   HPW_BIN            The `hpw` command (default scripts/hpw); overridable for tests.
#
# Side effects:
#   For Red flow health: calls scripts/slack-notify.sh red_flow_health when
#   flow_health == "Red". No de-duplication; fires on every Red-health poll run.
#   Under --dry-run it is not sent. The poll writes no saved state.
#   The scheduler's `[SCHEDULER] ...` lines (the action, idle, unreadable and
#   each skipped entry, such as no-merged-spec) go to stderr unchanged, so they
#   reach the run log. No comment is posted.
#   Side-effect log lines appear only on stderr; stdout JSON output is unaffected.
#
# Requirements: bash 4+, curl, jq
#
# Output (stdout):
#   JSON object:
#     {
#       "in_merge":                 [{"number": N, "title": "..."}, ...],
#       "in_implementation":        [{"number": N, "title": "..."}, ...],
#       "review_due":               [{"number": N, "title": "..."}, ...],
#       "conflict_due":             [{"number": N, "title": "..."}, ...],
#       "owner_missing_due":        [{"number": N, "title": "..."}, ...],
#       "evidence_pending":         true | false,
#       "next_issue":               N | null,
#       "next_action":              {kind, issue, type, epic, step, reason} | null,
#       "scheduler_result":         "refine" | "implement" | "idle" | "unreadable",
#       "resumable":                true | false
#     }
#   Every key is always present; each list is [] when no items match.
#   next_issue is the issue of the scheduler's action (a refine or implement
#   decision), or null; next_action is that action object, or null;
#   scheduler_result is the scheduler's result (issue #474). An output the
#   poll cannot read counts as unreadable. Neither idle nor unreadable changes
#   the poll's exit code.
#   evidence_pending is true when a story is labelled evidence:pending (issue
#   #348) or the label cannot be read: a failed read counts as pending.
#   resumable is true when any of in_merge, in_implementation, review_due,
#   conflict_due or owner_missing_due is not empty, or evidence_pending is true;
#   the workflow runs the invoke step for it (issue #293).
#   review_due (issue #315) lists the stories "In review" that
#   dispatcher-invoke.sh reviews with a /review session: one executor: label
#   with a REVIEW_EXECUTOR_ROUTING row (scripts/lib/review-routing.sh) and an
#   open, non-draft PR that closes the story.
#   conflict_due (issue #409, issue #449) lists the review-loop stories
#   "In review" (one executor: label with a CODEX_REVIEW_LOOP_ROUTING row) that
#   are owed a conflict round by the rule of scripts/lib/conflict-round.sh;
#   dispatcher-invoke.sh starts the round.
#   owner_missing_due (issue #449) lists the review-loop stories "In review"
#   whose clean PR head has no merge-owner evidence and no missing-merge-owner
#   blocker yet, by the same rule; dispatcher-invoke.sh posts that blocker.
#
# Prototype divergences from expected behaviour:
#   D1  Pagination: fetches all project items via cursor pagination, 100 per
#       page, capped at 50 pages (5000 items) as a runaway-loop safety valve.
#       A repo with >5000 project items still produces incomplete results;
#       a warning is logged to stderr if the cap is hit.
#   D2  Project auto-detection: uses the first linked GitHub Projects (v2)
#       board. Repos with multiple linked projects may detect the wrong board.
#   D3  Label fetch: executor labels are fetched from the GraphQL project
#       item's linked issue. Items with no linked issue (draft notes, PR
#       items) are skipped entirely.
#   D4  No executor invocation or GitHub state mutation occurs in any run.
#       The --dry-run flag adds a header line to stderr and suppresses the
#       Slack notification.
#   D5  Every status filter is applied in jq against the same accumulated
#       items list, so full-board pagination is done once per poll, not once
#       per status. The statuses read are those of work in flight ("In merge",
#       "In implementation", "In review"); finding them from PR facts is #475.
#   D6  The scheduler reads its own facts (`hpw dispatcher facts read`), so a
#       poll makes a second set of GitHub reads; it needs no model.
#   D7  Duplicate GitHub API call: flow-review.sh re-queries the board
#       independently. Acceptable at current scale; future refactor can share
#       the query result.

set -euo pipefail

# ─── Argument parsing ─────────────────────────────────────────────────────────

REPO=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            [[ $# -ge 2 ]] || { echo "Error: --repo requires a value." >&2; exit 1; }
            REPO="$2"; shift 2
            ;;
        --dry-run)
            DRY_RUN=true; shift
            ;;
        *)
            echo "Error: Unknown flag: $1" >&2; exit 1
            ;;
    esac
done

[[ -n "$REPO" ]] || { echo "Error: --repo <owner/repo> is required." >&2; exit 1; }
[[ "$REPO" == *"/"* ]] || { echo "Error: --repo must be in owner/repo format." >&2; exit 1; }

# ─── Auth ─────────────────────────────────────────────────────────────────────

TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
[[ -n "$TOKEN" ]] || {
    echo "Error: GitHub token not found. Set GH_TOKEN or GITHUB_TOKEN in the environment." >&2
    exit 1
}

# ─── Dependencies ─────────────────────────────────────────────────────────────

for cmd in curl jq; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "Error: '$cmd' is required but not found in PATH." >&2
        exit 1
    }
done

# ─── Variables ────────────────────────────────────────────────────────────────

OWNER="${REPO%%/*}"
REPO_NAME="${REPO##*/}"
GRAPHQL="https://api.github.com/graphql"
REST="https://api.github.com"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HPW_BIN="${HPW_BIN:-$SCRIPT_DIR/hpw}"  # `hpw dispatcher ...` (ADR-014); overridable for tests
# The review-path tables, shared with dispatcher-invoke.sh (issue #315).
# shellcheck source=scripts/lib/review-routing.sh
source "$SCRIPT_DIR/lib/review-routing.sh"
# The conflict-round rule, shared with dispatcher-invoke.sh (issue #449).
# shellcheck source=scripts/lib/conflict-round.sh
source "$SCRIPT_DIR/lib/conflict-round.sh"
# The scheduler call, shared with dispatcher-invoke.sh (issue #474).
# shellcheck source=scripts/lib/scheduler.sh
source "$SCRIPT_DIR/lib/scheduler.sh"

# ─── GraphQL helper ───────────────────────────────────────────────────────────

graphql() {
    local payload resp
    payload=$(jq -nc --arg q "$1" '{query: $q}')
    resp=$(curl -sf \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -X POST --data "$payload" "$GRAPHQL")
    if echo "$resp" | jq -e '.errors' >/dev/null 2>&1; then
        echo "Error: GraphQL: $(echo "$resp" | jq -r '.errors[0].message // "unknown"')" >&2
        exit 1
    fi
    echo "$resp"
}

# ─── Locate project board ─────────────────────────────────────────────────────

PROJECTS=$(graphql "{
  repository(owner:\"$OWNER\", name:\"$REPO_NAME\") {
    projectsV2(first:10) { nodes { number title } }
  }
}")

COUNT=$(echo "$PROJECTS" | jq '.data.repository.projectsV2.nodes | length')

if [[ "$COUNT" -eq 0 ]]; then
    echo "Error: No GitHub Projects (v2) board linked to $REPO." >&2
    exit 1
fi

# D2: uses the first linked project board
PROJECT_NUMBER=$(echo "$PROJECTS" | jq '.data.repository.projectsV2.nodes[0].number')

# ─── Fetch project items with status (paginated) ──────────────────────────────

# D1: walks every page via cursor pagination, capped at 50 pages as a
# runaway-loop safety valve. Items accumulate on disk, not in a shell
# variable passed via --argjson: past a few hundred project items the
# growing JSON blob risks exceeding the OS argument-length limit
# ("Argument list too long") — jq reading file contents directly has no
# such limit. See scripts/dispatcher-invoke.sh's fetch_board_data() for the
# same fix, hit in production once its richer per-item query (which
# includes full issue bodies) crossed that limit at ~130 items.
POLL_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$POLL_TMP_DIR"' EXIT
ITEMS_FILE="$POLL_TMP_DIR/items.json"
echo '[]' > "$ITEMS_FILE"
CURSOR=""
PAGE_COUNT=0
MAX_PAGES=50

while :; do
    PAGE_COUNT=$(( PAGE_COUNT + 1 ))
    if [[ "$PAGE_COUNT" -gt "$MAX_PAGES" ]]; then
        echo "Warning: hit ${MAX_PAGES}-page pagination cap ($((MAX_PAGES * 100)) items); results may be incomplete." >&2
        break
    fi

    if [[ -z "$CURSOR" ]]; then
        AFTER_ARG=""
    else
        AFTER_ARG=", after:\"$CURSOR\""
    fi

    PAGE=$(graphql "{
  repository(owner:\"$OWNER\", name:\"$REPO_NAME\") {
    projectsV2(first:1) { nodes { items(first:100$AFTER_ARG) {
      pageInfo { hasNextPage endCursor }
      nodes {
        content {
          ... on Issue {
            number
            title
            labels(first:20) { nodes { name } }
          }
        }
        fieldValues(first:20) { nodes {
          ... on ProjectV2ItemFieldSingleSelectValue {
            name
            field { ... on ProjectV2SingleSelectField { name } }
          }
        }}
      }
    }}}
  }
}")

    PAGE_FILE="$POLL_TMP_DIR/page_${PAGE_COUNT}.json"
    echo "$PAGE" | jq '.data.repository.projectsV2.nodes[0].items.nodes' > "$PAGE_FILE"
    jq -s 'add' "$ITEMS_FILE" "$PAGE_FILE" > "$POLL_TMP_DIR/items_new.json"
    mv "$POLL_TMP_DIR/items_new.json" "$ITEMS_FILE"
    rm -f "$PAGE_FILE"

    HAS_NEXT=$(echo "$PAGE" | jq -r '.data.repository.projectsV2.nodes[0].items.pageInfo.hasNextPage')
    CURSOR=$(echo "$PAGE" | jq -r '.data.repository.projectsV2.nodes[0].items.pageInfo.endCursor')

    [[ "$HAS_NEXT" == "true" ]] || break
done

ITEMS=$(jq -n --slurpfile nodes "$ITEMS_FILE" '{data:{repository:{projectsV2:{nodes:[{items:{nodes:$nodes[0]}}]}}}}')

# ─── Work in flight by status (D5: single response) ─────────────────────────

# Resumable work for dispatcher-invoke.sh (issue #293): stories "In merge" are
# resumed at /merge, and stories "In implementation" may have a fix round due.
status_items() {
    echo "$ITEMS" | jq --arg s "$1" '[
      .data.repository.projectsV2.nodes[0].items.nodes[] |
      select(
        (.content.number? != null) and
        ([ .fieldValues.nodes[] |
           select((.field.name? // "") == "Status" and (.name? // "") == $s)
        ] | length > 0)
      ) |
      { number: .content.number, title: .content.title }
    ]'
}

IN_MERGE=$(status_items "In merge")
IN_IMPLEMENTATION=$(status_items "In implementation")

# Stories "In review" that dispatcher-invoke.sh reviews with a /review session
# (issue #315): exactly one executor: label with a REVIEW_EXECUTOR_ROUTING row
# (scripts/lib/review-routing.sh, the table the invoke script reads) and an
# open, non-draft PR linked by "Closes #<n>". A story that waits for Codex
# Cloud is not listed.
REVIEW_CANDIDATES=$(echo "$ITEMS" | jq -c '[
  .data.repository.projectsV2.nodes[0].items.nodes[] |
  select(
    (.content.number? != null) and
    ([ .fieldValues.nodes[] |
       select((.field.name? // "") == "Status" and (.name? // "") == "In review")
    ] | length > 0)
  ) |
  { number: .content.number, title: .content.title,
    executors: [ (.content.labels.nodes // [])[] | .name | select(startswith("executor:")) | ltrimstr("executor:") ] }
]')
# The open-PR list, read once per poll and only when a candidate needs it.
OPEN_PRS=""
load_open_prs() {
    [[ -z "$OPEN_PRS" ]] || return 0
    OPEN_PRS=$(curl -sf \
        -H "Authorization: Bearer $TOKEN" \
        -H "Accept: application/vnd.github+json" \
        "$REST/repos/$REPO/pulls?state=open&per_page=100") || {
        echo "[POLL] Warning: could not read open pull requests; review_due, conflict_due and owner_missing_due may be incomplete" >&2
        OPEN_PRS="[]"
    }
}

# linked_ready_pr <issue> — the open, non-draft PR that closes the story.
linked_ready_pr() {
    echo "$OPEN_PRS" | jq -r --argjson n "$1" '
        [.[] | select((.draft | not) and ((.body // "") | ascii_downcase
            | test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")))][0].number // empty'
}

# rest_get_all <path> — every page of a REST list as one JSON array; fails
# when a page cannot be read.
rest_get_all() {
    local path="$1" page=1 chunk all='[]'
    while :; do
        chunk=$(curl -sf \
            -H "Authorization: Bearer $TOKEN" \
            -H "Accept: application/vnd.github+json" \
            "$REST/$path?per_page=100&page=$page") || return 1
        all=$(printf '%s\n%s\n' "$all" "$chunk" | jq -cs 'add') || return 1
        [[ "$(jq 'length' <<< "$chunk")" -ge 100 ]] || break
        page=$(( page + 1 ))
    done
    echo "$all"
}

# The token's own login: the dispatcher's account, and the bookkeeping job's
# unless CODEX_BOOKKEEPING_LOGIN names it (as in dispatcher-invoke.sh).
TOKEN_LOGIN=""
load_token_login() {
    [[ -z "$TOKEN_LOGIN" ]] || return 0
    # Block 2 of ADR-014 (`hpw dispatcher marker whoami`, issue #433).
    TOKEN_LOGIN=$("${HPW_BIN:-$(dirname "${BASH_SOURCE[0]}")/hpw}" dispatcher marker whoami --repo "$REPO" 2>/dev/null \
        | jq -r '.dispatcher // empty') || TOKEN_LOGIN=""
    [[ -n "$TOKEN_LOGIN" ]]
}
# The PO account, as DECISION_BLOCKER_PO_LOGIN in dispatcher-invoke.sh.
PO_LOGIN="LauraMardones"

# Issue #409, issue #449: conflict_due lists the review-loop stories
# "In review" (one executor: label with a CODEX_REVIEW_LOOP_ROUTING row) for
# which a conflict round is owed, by the rule the invoke step applies too
# (conflict_round_rule, scripts/lib/conflict-round.sh): the open, non-draft PR
# conflicts with main, no fix round is due, the story has no open blocker and
# no conflict-round attempt marker holds for the PR's current head.
# owner_missing_due lists, by the same rule, the review-loop stories
# "In review" whose clean PR head has no merge-owner evidence and no
# missing-merge-owner blocker yet: a session stopped after its push, and the
# invoke step posts that blocker once. Stories "In implementation" need no
# key: in_implementation already starts the invoke step for them. A PR with
# unknown mergeability is read again on the next poll. A PR or a comment list
# that cannot be read lists nothing and warns.
REVIEW_DUE='[]'
CONFLICT_DUE='[]'
OWNER_MISSING_DUE='[]'
if [[ "$(echo "$REVIEW_CANDIDATES" | jq 'length')" -gt 0 ]]; then
    REVIEW_ROWS=$(echo "$REVIEW_CANDIDATES" | jq -c '.[]')
    while IFS= read -r row; do
        mapfile -t EXEC_SUFFIXES < <(echo "$row" | jq -r '.executors[]')
        STORY_NUMBER=$(echo "$row" | jq '.number')
        if review_by_dispatcher_session "${EXEC_SUFFIXES[@]}"; then
            load_open_prs
            if [[ -n "$(linked_ready_pr "$STORY_NUMBER")" ]]; then
                REVIEW_DUE=$(echo "$REVIEW_DUE" | jq -c --argjson r "$row" '. + [{number: $r.number, title: $r.title}]')
            fi
        elif [[ ${#EXEC_SUFFIXES[@]} -eq 1 && -n "${CODEX_REVIEW_LOOP_ROUTING[${EXEC_SUFFIXES[0]}]:-}" ]]; then
            load_open_prs
            PR_NUMBER=$(linked_ready_pr "$STORY_NUMBER")
            [[ -n "$PR_NUMBER" ]] || continue
            PR_DETAIL=$(curl -sf \
                -H "Authorization: Bearer $TOKEN" \
                -H "Accept: application/vnd.github+json" \
                "$REST/repos/$REPO/pulls/$PR_NUMBER") || {
                echo "[POLL] Warning: could not read PR #$PR_NUMBER for #$STORY_NUMBER; conflict_due and owner_missing_due skip it this cycle" >&2
                continue
            }
            # Unknown mergeability (null) decides nothing: read again next poll.
            echo "$PR_DETAIL" | jq -e '.mergeable != null or .mergeable_state == "dirty"' >/dev/null 2>&1 \
                || continue
            if ! load_token_login \
                    || ! STORY_COMMENTS=$(rest_get_all "repos/$REPO/issues/$STORY_NUMBER/comments") \
                    || ! PR_COMMENTS=$(rest_get_all "repos/$REPO/issues/$PR_NUMBER/comments"); then
                echo "[POLL] Warning: could not read the comments of #$STORY_NUMBER or PR #$PR_NUMBER; conflict_due and owner_missing_due skip it this cycle" >&2
                continue
            fi
            RULE=$(printf '%s\n%s\n%s\n' "$PR_DETAIL" "$STORY_COMMENTS" "$PR_COMMENTS" \
                | conflict_round_rule "$TOKEN_LOGIN" "${CODEX_BOOKKEEPING_LOGIN:-$TOKEN_LOGIN}" "$PO_LOGIN") || RULE=""
            case "$RULE" in
                owed)
                    CONFLICT_DUE=$(echo "$CONFLICT_DUE" | jq -c --argjson r "$row" '. + [{number: $r.number, title: $r.title}]') ;;
                ownerless)
                    OWNER_MISSING_DUE=$(echo "$OWNER_MISSING_DUE" | jq -c --argjson r "$row" '. + [{number: $r.number, title: $r.title}]') ;;
            esac
        fi
    done <<< "$REVIEW_ROWS"
fi

# ─── Dry-run header ───────────────────────────────────────────────────────────

if $DRY_RUN; then
    echo "DRY RUN — no executor invocation or GitHub state mutation will occur." >&2
    echo "" >&2
fi

# ─── New work: the scheduler's decision (issue #474) ─────────────────────────
# The scheduler decides from GitHub facts; the poll reads no "Ready" status.
# --in-implementation lists the work in flight as the dispatcher finds it
# today: the board's "In implementation", "In review" and "In merge" stories,
# so the scheduler never starts one of them again. Finding them from PR facts
# is #475 (PO decision on PR #485).

echo "Resumable: $(echo "$IN_MERGE" | jq 'length') item(s) in merge, $(echo "$IN_IMPLEMENTATION" | jq 'length') item(s) in implementation, $(echo "$REVIEW_DUE" | jq 'length') item(s) due for review." >&2

SCHEDULER_ARGS=()
$DRY_RUN && SCHEDULER_ARGS+=(--dry-run)
IN_FLIGHT_LIST=$(jq -rn --argjson a "$IN_IMPLEMENTATION" --argjson b "$REVIEW_CANDIDATES" --argjson c "$IN_MERGE" \
    '$a + $b + $c | map(.number) | unique | map(tostring) | join(",")')
scheduler_next "$IN_FLIGHT_LIST" \
    ${SCHEDULER_ARGS[@]+"${SCHEDULER_ARGS[@]}"}
echo "[POLL] Scheduler result: $SCHEDULER_RESULT${SCHEDULER_ISSUE:+ #$SCHEDULER_ISSUE}" >&2

# ─── Flow health notification (Red only) ─────────────────────────────────────

# D7: flow-review.sh makes an independent board query; || true ensures a
# transient API failure does not abort the poll loop under set -euo pipefail.
# Exit 2 is a missing or invalid limit variable (issue #358): that error is not
# hidden; flow-review.sh has logged it, and the poll fails like the usage limit.
FLOW_RC=0
FLOW_JSON=$(bash "$(dirname "$0")/flow-review.sh" --repo "$REPO" --json) || FLOW_RC=$?
[[ $FLOW_RC -ne 2 ]] || exit 2
FLOW_HEALTH=$(printf '%s' "$FLOW_JSON" | jq -r '.flow_health // ""' 2>/dev/null) || FLOW_HEALTH=""

if [[ "$FLOW_HEALTH" == "Red" ]]; then
    WIP_COUNT=$(echo "$FLOW_JSON" | jq '.wip_count')
    BLOCKED_COUNT=$(echo "$FLOW_JSON" | jq '.blocked | length')
    WIP_VIOLATION=$(echo "$FLOW_JSON" | jq -r '.wip_violation')
    STALE_BLOCKED=$(echo "$FLOW_JSON" | jq '[.blocked[] | select(.days_without_update > 7)] | length')

    if [[ "$WIP_VIOLATION" == "true" ]]; then
        SIGNAL="WIP limit exceeded"
    elif [[ "$STALE_BLOCKED" -gt 0 ]]; then
        SIGNAL="Blocked item stale >7 days"
    else
        SIGNAL="Empty ready queue with active WIP"
    fi

    CONTEXT=$(jq -n \
        --arg s "$SIGNAL" \
        --arg w "$WIP_COUNT" \
        --arg b "$BLOCKED_COUNT" \
        --arg u "${BOARD_URL:-}" \
        '{"signal":$s,"wip_count":$w,"blocked_count":$b,"board_url":$u}')

    if $DRY_RUN; then
        echo "[DRY RUN] Would notify: red_flow_health (${SIGNAL})" >&2
    else
        bash "$(dirname "$0")/slack-notify.sh" red_flow_health "$CONTEXT" >/dev/null || true
    fi
fi

# ─── What the workflow decides from the poll (issues #293, #315, #348, #409, #434) ───

# Issue #348: a story labelled evidence:pending is Done, so no board state
# leads back to it, but its cleanup closing summary is still to be posted by
# dispatcher-invoke.sh. A failed or unreadable read counts as pending, so the
# retry is never skipped because of it.
EVIDENCE_PENDING=true
if LABELED=$(curl -sf \
        -H "Authorization: Bearer $TOKEN" \
        -H "Accept: application/vnd.github+json" \
        "$REST/repos/$REPO/issues?state=all&labels=evidence%3Apending&per_page=1" 2>/dev/null); then
    EVIDENCE_PENDING=$(jq -r 'if type == "array" then length > 0 else true end' <<< "$LABELED" 2>/dev/null) || EVIDENCE_PENDING=true
fi
[[ "$EVIDENCE_PENDING" == true ]] || EVIDENCE_PENDING=false
if [[ "$EVIDENCE_PENDING" == true ]]; then
    echo "[POLL] A story is labelled evidence:pending (or the label could not be read); the invoke step runs" >&2
fi

# ─── JSON output (stdout) ─────────────────────────────────────────────────────

jq -n \
    --argjson in_merge "$IN_MERGE" \
    --argjson in_implementation "$IN_IMPLEMENTATION" \
    --argjson review_due "$REVIEW_DUE" \
    --argjson conflict_due "$CONFLICT_DUE" \
    --argjson owner_missing_due "$OWNER_MISSING_DUE" \
    --argjson evidence_pending "$EVIDENCE_PENDING" \
    --argjson next_action "$SCHEDULER_ACTION" \
    --arg scheduler_result "$SCHEDULER_RESULT" \
    '{in_merge: $in_merge, in_implementation: $in_implementation,
      review_due: $review_due, conflict_due: $conflict_due,
      owner_missing_due: $owner_missing_due,
      evidence_pending: $evidence_pending,
      next_issue: ($next_action.issue // null),
      next_action: $next_action,
      scheduler_result: $scheduler_result,
      resumable: ((($in_merge + $in_implementation + $review_due + $conflict_due + $owner_missing_due) | length > 0)
                  or $evidence_pending)}'
