#!/usr/bin/env bash
# scripts/dispatcher-poll.sh
#
# Prototype: query GitHub Projects v2 for items with status
# "Ready for implementation" or "Ready for refinement" and output a JSON
# object with both result lists.
# Contract: issue #170 (initial implementation), issue #181 (dual-status extension),
#           issue #182 (ready-for-refinement notification), issue #180 (Red flow health notification)
#
# Usage:
#   GH_TOKEN=<token> bash scripts/dispatcher-poll.sh --repo <owner/repo> [--dry-run]
#
# Environment (optional):
#   SLACK_WEBHOOK_URL  Required by slack-notify.sh for ready_for_refinement notifications.
#                      If unset, slack-notify.sh exits 1; the poll loop continues (|| true).
#   BOARD_URL          GitHub Projects board URL included in Red flow health notifications.
#                      Falls back to "" if unset; slack-notify.sh renders "(no URL)" gracefully.
#
# Environment (optional, state):
#   DISPATCHER_STATE_DIR  Directory holding persisted refinement-notification
#                      state. Defaults to <repo-root>/.dispatcher-state. The
#                      dispatcher workflow restores it from actions/cache before
#                      the poll and saves it after (issue #257).
#
# Side effects:
#   For each "Ready for refinement" issue not already notified during its
#   current continuous stay in that status, calls scripts/slack-notify.sh
#   ready_for_refinement. De-duplication state is persisted per repository in
#   $DISPATCHER_STATE_DIR/<owner>/<repo>/notified-refinement and reconciled on
#   every non-dry-run poll: an issue is recorded only after slack-notify.sh
#   succeeds, and is dropped as soon as it leaves "Ready for refinement", so a
#   later re-entry notifies again (see "Ready for refinement notification").
#   For Red flow health: calls scripts/slack-notify.sh red_flow_health when
#   flow_health == "Red". No de-duplication; fires on every Red-health poll run.
#   Under --dry-run neither notification is sent and the state file is read
#   but never written.
#   Side-effect log lines appear only on stderr; stdout JSON output is unaffected.
#
# Requirements: bash, curl, jq
#
# Output (stdout):
#   JSON object:
#     {
#       "ready_for_implementation": [{"number": N, "title": "..."}, ...],
#       "ready_for_refinement":     [{"number": N, "title": "..."}, ...]
#     }
#   Both keys are always present; each value is [] when no items match.
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
#       The --dry-run flag adds a header line to stderr and suppresses Slack
#       notifications and refinement-state writes.
#   D5  Both status filters are applied in jq against the same accumulated
#       items list, so full-board pagination is done once per poll, not once
#       per status.
#   D6  Refinement notification state persists across ephemeral GitHub-hosted
#       runners only through the workflow's actions/cache restore/save steps
#       (interim mechanism until #259 moves the dispatcher to persistent
#       infrastructure). If the snapshot is missing, evicted, or corrupt, the
#       poll warns and starts from empty state, so each issue currently in
#       "Ready for refinement" may be notified once more.
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${DISPATCHER_STATE_DIR:-$REPO_ROOT/.dispatcher-state}"

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

# ─── Filter by status (D5: single response, two jq passes) ───────────────────

READY_FOR_IMPL=$(echo "$ITEMS" | jq '[
  .data.repository.projectsV2.nodes[0].items.nodes[] |
  select(
    (.content.number? != null) and
    ([ .fieldValues.nodes[] |
       select((.field.name? // "") == "Status" and (.name? // "") == "Ready for implementation")
    ] | length > 0)
  ) |
  { number: .content.number, title: .content.title }
]')

READY_FOR_REFINE=$(echo "$ITEMS" | jq '[
  .data.repository.projectsV2.nodes[0].items.nodes[] |
  select(
    (.content.number? != null) and
    ([ .fieldValues.nodes[] |
       select((.field.name? // "") == "Status" and (.name? // "") == "Ready for refinement")
    ] | length > 0)
  ) |
  { number: .content.number, title: .content.title }
]')

IMPL_COUNT=$(echo "$READY_FOR_IMPL" | jq 'length')
REFINE_COUNT=$(echo "$READY_FOR_REFINE" | jq 'length')

# ─── Dry-run header ───────────────────────────────────────────────────────────

if $DRY_RUN; then
    echo "DRY RUN — no executor invocation or GitHub state mutation will occur." >&2
    echo "" >&2
fi

# ─── Log found items ──────────────────────────────────────────────────────────

if [[ "$IMPL_COUNT" -eq 0 ]]; then
    echo "No items found with status \"Ready for implementation\"." >&2
else
    echo "Items ready for implementation: $IMPL_COUNT" >&2
    echo "" >&2
    echo "$READY_FOR_IMPL" | jq -r '.[] | "[POLL] #\(.number) \(.title)"' >&2
    echo "" >&2
fi

if [[ "$REFINE_COUNT" -eq 0 ]]; then
    echo "No items found with status \"Ready for refinement\"." >&2
else
    echo "Items ready for refinement: $REFINE_COUNT" >&2
    echo "" >&2
    echo "$READY_FOR_REFINE" | jq -r '.[] | "[POLL] #\(.number) \(.title)"' >&2
    echo "" >&2
fi

echo "Summary: $IMPL_COUNT item(s) ready for implementation, $REFINE_COUNT item(s) ready for refinement." >&2

# ─── Ready for refinement notification ───────────────────────────────────────
#
# De-duplication state: one notified issue number per line, persisted per
# repository at $STATE_DIR/<owner>/<repo>/notified-refinement (the directory
# layout keeps owner/repo pairs from colliding). The dispatcher workflow
# restores and saves $STATE_DIR via actions/cache (D6).
#
# Lifecycle, reconciled on every non-dry-run poll:
#   - an issue is recorded only after slack-notify.sh exits 0; a failed
#     delivery stays unrecorded and is retried on the next poll;
#   - recorded issues no longer in "Ready for refinement" are dropped, so a
#     later re-entry is a new stay and notifies once more.
# Missing, unreadable, or malformed state is never fatal: the poll warns
# (except for a plain missing file) and continues from empty state.

REFINE_STATE_FILE="$STATE_DIR/$OWNER/$REPO_NAME/notified-refinement"
PREV_NOTIFIED_FILE="$POLL_TMP_DIR/notified-prev"
NEXT_NOTIFIED_FILE="$POLL_TMP_DIR/notified-next"
: > "$PREV_NOTIFIED_FILE"
: > "$NEXT_NOTIFIED_FILE"

if [[ -e "$REFINE_STATE_FILE" ]]; then
    if ! cp "$REFINE_STATE_FILE" "$PREV_NOTIFIED_FILE" 2>/dev/null; then
        echo "[POLL] Warning: cannot read refinement notification state $REFINE_STATE_FILE; continuing with empty state" >&2
        : > "$PREV_NOTIFIED_FILE"
    elif grep -qvxE '[0-9]+' "$PREV_NOTIFIED_FILE"; then
        echo "[POLL] Warning: malformed refinement notification state $REFINE_STATE_FILE; continuing with empty state" >&2
        : > "$PREV_NOTIFIED_FILE"
    fi
fi

if [[ "$REFINE_COUNT" -gt 0 ]]; then
    while IFS= read -r item; do
        NUMBER=$(echo "$item" | jq -r '.number')
        TITLE=$(echo "$item" | jq -r '.title')
        URL="https://github.com/${REPO}/issues/${NUMBER}"

        if grep -qxF "$NUMBER" "$PREV_NOTIFIED_FILE"; then
            # Still in the same continuous stay: carry the record forward.
            echo "$NUMBER" >> "$NEXT_NOTIFIED_FILE"
            continue
        fi

        CONTEXT_JSON=$(jq -n \
            --arg issue_title "$TITLE" \
            --arg issue_url "$URL" \
            '{"issue_title": $issue_title, "issue_url": $issue_url}')
        if $DRY_RUN; then
            echo "[DRY RUN] Would notify: ready_for_refinement #${NUMBER} ${TITLE}" >&2
        else
            NOTIFY_OK=false
            bash "$(dirname "$0")/slack-notify.sh" ready_for_refinement "$CONTEXT_JSON" >/dev/null && NOTIFY_OK=true || true
            if $NOTIFY_OK; then
                echo "$NUMBER" >> "$NEXT_NOTIFIED_FILE"
                echo "[POLL] Notified: ready_for_refinement #${NUMBER} ${TITLE}" >&2
            else
                echo "[POLL] Warning: slack-notify.sh failed for #${NUMBER}; will retry next cycle" >&2
            fi
        fi
    done < <(echo "$READY_FOR_REFINE" | jq -c '.[]')
fi

# Save the reconciled state atomically. Written even when empty so issues that
# left "Ready for refinement" are cleared. A write failure only warns: the
# next poll then re-notifies at most once per issue still waiting.
if ! $DRY_RUN; then
    if ! { mkdir -p "$(dirname "$REFINE_STATE_FILE")" \
            && cp "$NEXT_NOTIFIED_FILE" "$REFINE_STATE_FILE.tmp.$$" \
            && mv -f "$REFINE_STATE_FILE.tmp.$$" "$REFINE_STATE_FILE"; } 2>/dev/null; then
        rm -f "$REFINE_STATE_FILE.tmp.$$" 2>/dev/null || true
        echo "[POLL] Warning: cannot write refinement notification state $REFINE_STATE_FILE" >&2
    fi
fi

# ─── Flow health notification (Red only) ─────────────────────────────────────

# D7: flow-review.sh makes an independent board query; || true ensures a
# transient API failure does not abort the poll loop under set -euo pipefail.
FLOW_JSON=$(bash "$(dirname "$0")/flow-review.sh" --repo "$REPO" --json) || true
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

# ─── JSON output (stdout) ─────────────────────────────────────────────────────

jq -n \
    --argjson impl "$READY_FOR_IMPL" \
    --argjson refine "$READY_FOR_REFINE" \
    '{ready_for_implementation: $impl, ready_for_refinement: $refine}'
