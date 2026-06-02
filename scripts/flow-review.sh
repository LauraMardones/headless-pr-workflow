#!/usr/bin/env bash
# scripts/flow-review.sh
#
# Generates the weekly flow review report from GitHub Projects v2.
# Read-only except when --post is used to post the report as a GitHub comment.
# Implements: issue #158
#
# Usage:
#   GH_TOKEN=<token> bash scripts/flow-review.sh --repo <owner/repo> \
#     [--since <YYYY-MM-DD>] [--json] [--post <issue-number>]
#
# Requirements: bash, curl, jq
#
# Prototype divergences:
#   D1  Pagination: fetches max 100 project items per request; repos with
#       >100 project items may produce incomplete results.
#   D2  Project auto-detection: uses the first linked GitHub Projects (v2)
#       board. Repos with multiple linked projects may detect the wrong board.
#   D3  Completed this week: uses closedAt on the linked issue as a proxy for
#       "moved to Done this week". Items marked Done in the project but whose
#       linked issue is still open may be missed; items closed but not yet
#       marked Done in the project are excluded.
#   D4  Recoveries — stale detection: identifies items currently in
#       "In implementation" with updatedAt >2h ago as best-effort stale
#       candidates. The "no handoff note" condition from PROJECT-STATUS.md is
#       not checked (would require fetching all comments per item, causing N+1
#       requests). Items previously rolled back are not detectable without
#       GitHub audit log access.

set -euo pipefail

# ─── Argument parsing ─────────────────────────────────────────────────────────

REPO=""
SINCE=""
JSON_OUTPUT=false
POST_ISSUE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            [[ $# -ge 2 ]] || { echo "Error: --repo requires a value." >&2; exit 1; }
            REPO="$2"; shift 2
            ;;
        --since)
            [[ $# -ge 2 ]] || { echo "Error: --since requires a value." >&2; exit 1; }
            SINCE="$2"; shift 2
            ;;
        --json)
            JSON_OUTPUT=true; shift
            ;;
        --post)
            [[ $# -ge 2 ]] || { echo "Error: --post requires a value." >&2; exit 1; }
            POST_ISSUE="$2"; shift 2
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
API="https://api.github.com"
GRAPHQL="$API/graphql"

TODAY=$(date -u +%Y-%m-%d)
NOW_TS=$(date -u +%s)

if [[ -z "$SINCE" ]]; then
    if date --version >/dev/null 2>&1; then
        SINCE=$(date -u -d "7 days ago" +%Y-%m-%d)   # GNU date (Linux)
    else
        SINCE=$(date -u -v-7d +%Y-%m-%d)              # BSD date (macOS)
    fi
fi

SINCE_TS="${SINCE}T00:00:00Z"
STALE_2H_TS=$((NOW_TS - 7200))

# ─── API helpers ──────────────────────────────────────────────────────────────

rest() {
    curl -sf \
        -H "Authorization: Bearer $TOKEN" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "$API$1"
}

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

ts_to_epoch() {
    local ts="$1"
    if date --version >/dev/null 2>&1; then
        date -u -d "$ts" +%s 2>/dev/null || echo 0   # GNU date
    else
        date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$ts" +%s 2>/dev/null || echo 0  # BSD date
    fi
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

# ─── Fetch all project items with status, labels, timestamps ──────────────────

# D1: max 100 items per request
ITEMS=$(graphql "{
  repository(owner:\"$OWNER\", name:\"$REPO_NAME\") {
    projectsV2(first:1) { nodes { items(first:100) { nodes {
      content {
        ... on Issue {
          number
          title
          state
          closedAt
          updatedAt
          labels(first:20) { nodes { name } }
        }
      }
      fieldValues(first:20) { nodes {
        ... on ProjectV2ItemFieldSingleSelectValue {
          name
          field { ... on ProjectV2SingleSelectField { name } }
        }
      }}
    }}}}
  }
}")

# ─── Process items into sections ──────────────────────────────────────────────

COMPLETED_NUMS=()
COMPLETED_TITLES=()
COMPLETED_DATES=()

IN_IMPL_NUMS=()
IN_IMPL_TITLES=()
IN_IMPL_EXECUTORS=()

BLOCKED_NUMS=()
BLOCKED_TITLES=()
BLOCKED_DAYS=()

RECOVERY_NUMS=()
RECOVERY_TITLES=()

READY_NUMS=()
READY_TITLES=()
READY_EXECUTORS=()

N=$(echo "$ITEMS" | jq '.data.repository.projectsV2.nodes[0].items.nodes | length')

for ((i=0; i<N; i++)); do
    ITEM=$(echo "$ITEMS" | jq ".data.repository.projectsV2.nodes[0].items.nodes[$i]")

    NUM=$(echo "$ITEM" | jq -r '.content.number // empty')
    [[ -n "$NUM" ]] || continue

    TITLE=$(echo "$ITEM" | jq -r '.content.title // "(no title)"')
    CLOSED_AT=$(echo "$ITEM" | jq -r '.content.closedAt // ""')
    UPDATED_AT=$(echo "$ITEM" | jq -r '.content.updatedAt // ""')

    STATUS=$(echo "$ITEM" | jq -r '
        [.fieldValues.nodes[] |
         select((.field.name? // "") == "Status" and .name? != null) |
         .name] | first // ""')

    EXECUTOR=$(echo "$ITEM" | jq -r '
        [.content.labels.nodes[]? | select(.name | startswith("executor:")) | .name] |
        first // ""')

    case "$STATUS" in
        "Done")
            # D3: closedAt as proxy for "moved to Done this week"
            if [[ -n "$CLOSED_AT" && "$CLOSED_AT" > "$SINCE_TS" ]]; then
                COMPLETED_NUMS+=("$NUM")
                COMPLETED_TITLES+=("$TITLE")
                COMPLETED_DATES+=("${CLOSED_AT%%T*}")
            fi
            ;;

        "In implementation")
            IN_IMPL_NUMS+=("$NUM")
            IN_IMPL_TITLES+=("$TITLE")
            IN_IMPL_EXECUTORS+=("$EXECUTOR")

            # D4: best-effort stale detection — updatedAt > 2h ago
            if [[ -n "$UPDATED_AT" ]]; then
                UPDATED_TS=$(ts_to_epoch "$UPDATED_AT")
                if [[ "$UPDATED_TS" -gt 0 && "$UPDATED_TS" -lt "$STALE_2H_TS" ]]; then
                    RECOVERY_NUMS+=("$NUM")
                    RECOVERY_TITLES+=("$TITLE")
                fi
            fi
            ;;

        "Blocked")
            BLOCKED_NUMS+=("$NUM")
            BLOCKED_TITLES+=("$TITLE")
            if [[ -n "$UPDATED_AT" ]]; then
                UPDATED_TS=$(ts_to_epoch "$UPDATED_AT")
                DAYS=$(( (NOW_TS - UPDATED_TS) / 86400 ))
            else
                DAYS=0
            fi
            BLOCKED_DAYS+=("$DAYS")
            ;;

        "Ready for implementation")
            READY_NUMS+=("$NUM")
            READY_TITLES+=("$TITLE")
            READY_EXECUTORS+=("$EXECUTOR")
            ;;
    esac
done

# ─── Compute flow health signal ───────────────────────────────────────────────

WIP_COUNT="${#IN_IMPL_NUMS[@]}"
WIP_VIOLATION=false
if [[ "$WIP_COUNT" -gt 2 ]]; then
    WIP_VIOLATION=true
fi

BLOCKED_STALE_GT7=false
BLOCKED_STALE_GT3=false
for ((i=0; i<${#BLOCKED_NUMS[@]}; i++)); do
    d="${BLOCKED_DAYS[$i]}"
    if [[ "$d" -gt 7 ]]; then BLOCKED_STALE_GT7=true; fi
    if [[ "$d" -gt 3 ]]; then BLOCKED_STALE_GT3=true; fi
done

READY_COUNT="${#READY_NUMS[@]}"

# Red: WIP > 2 OR any blocker > 7 days OR (ready queue = 0 AND WIP >= 2)
# Yellow: WIP = 2 OR any blocker 3–7 days OR ready queue = 0
# Green: all else
FLOW_HEALTH="Green"
if $WIP_VIOLATION || $BLOCKED_STALE_GT7 || [[ "$READY_COUNT" -eq 0 && "$WIP_COUNT" -ge 2 ]]; then
    FLOW_HEALTH="Red"
elif [[ "$WIP_COUNT" -eq 2 ]] || $BLOCKED_STALE_GT3 || [[ "$READY_COUNT" -eq 0 ]]; then
    FLOW_HEALTH="Yellow"
fi

# ─── Build human-readable report ──────────────────────────────────────────────

REPORT=""
REPORT+="## Weekly Flow Review — ${TODAY}"$'\n'
REPORT+=""$'\n'

REPORT+="**Completed this week:**"$'\n'
if [[ "${#COMPLETED_NUMS[@]}" -eq 0 ]]; then
    REPORT+="(none)"$'\n'
else
    for ((i=0; i<${#COMPLETED_NUMS[@]}; i++)); do
        REPORT+="- #${COMPLETED_NUMS[$i]} ${COMPLETED_TITLES[$i]} (closed ${COMPLETED_DATES[$i]})"$'\n'
    done
fi
REPORT+="Count: ${#COMPLETED_NUMS[@]}"$'\n'
REPORT+=""$'\n'

REPORT+="**In implementation:**"$'\n'
if [[ "${#IN_IMPL_NUMS[@]}" -eq 0 ]]; then
    REPORT+="(none)"$'\n'
else
    for ((i=0; i<${#IN_IMPL_NUMS[@]}; i++)); do
        ex="${IN_IMPL_EXECUTORS[$i]}"
        if [[ -n "$ex" ]]; then
            REPORT+="- #${IN_IMPL_NUMS[$i]} ${IN_IMPL_TITLES[$i]} (executor: ${ex})"$'\n'
        else
            REPORT+="- #${IN_IMPL_NUMS[$i]} ${IN_IMPL_TITLES[$i]}"$'\n'
        fi
    done
fi
WIP_LINE="WIP count: ${WIP_COUNT}/2"
if $WIP_VIOLATION; then WIP_LINE+=" ⚠ WIP limit exceeded"; fi
REPORT+="${WIP_LINE}"$'\n'
REPORT+=""$'\n'

REPORT+="**Blocked:**"$'\n'
if [[ "${#BLOCKED_NUMS[@]}" -eq 0 ]]; then
    REPORT+="(none)"$'\n'
else
    for ((i=0; i<${#BLOCKED_NUMS[@]}; i++)); do
        d="${BLOCKED_DAYS[$i]}"
        line="- #${BLOCKED_NUMS[$i]} ${BLOCKED_TITLES[$i]} — days without update: ${d}"
        if [[ "$d" -gt 7 ]]; then line+=" ⚠ >7 days"; fi
        REPORT+="${line}"$'\n'
    done
fi
REPORT+=""$'\n'

REPORT+="**Recoveries:**"$'\n'
if [[ "${#RECOVERY_NUMS[@]}" -eq 0 ]]; then
    REPORT+="(none)"$'\n'
else
    for ((i=0; i<${#RECOVERY_NUMS[@]}; i++)); do
        REPORT+="- #${RECOVERY_NUMS[$i]} ${RECOVERY_TITLES[$i]} — stale detected ${TODAY}"$'\n'
    done
fi
REPORT+=""$'\n'

REPORT+="**Ready queue:**"$'\n'
if [[ "${#READY_NUMS[@]}" -eq 0 ]]; then
    REPORT+="(none)"$'\n'
else
    for ((i=0; i<${#READY_NUMS[@]}; i++)); do
        ex="${READY_EXECUTORS[$i]}"
        if [[ -n "$ex" ]]; then
            REPORT+="- #${READY_NUMS[$i]} ${READY_TITLES[$i]} (executor: ${ex})"$'\n'
        else
            REPORT+="- #${READY_NUMS[$i]} ${READY_TITLES[$i]}"$'\n'
        fi
    done
fi
REPORT+="Queue depth: ${READY_COUNT}"$'\n'
REPORT+=""$'\n'

REPORT+="**Flow health:** ${FLOW_HEALTH}"$'\n'

# ─── JSON output ──────────────────────────────────────────────────────────────

if $JSON_OUTPUT; then
    COMPLETED_JSON="[]"
    for ((i=0; i<${#COMPLETED_NUMS[@]}; i++)); do
        COMPLETED_JSON=$(jq -n \
            --argjson arr "$COMPLETED_JSON" \
            --argjson n "${COMPLETED_NUMS[$i]}" \
            --arg t "${COMPLETED_TITLES[$i]}" \
            --arg d "${COMPLETED_DATES[$i]}" \
            '$arr + [{"number": $n, "title": $t, "closed_at": $d}]')
    done

    IN_IMPL_JSON="[]"
    for ((i=0; i<${#IN_IMPL_NUMS[@]}; i++)); do
        IN_IMPL_JSON=$(jq -n \
            --argjson arr "$IN_IMPL_JSON" \
            --argjson n "${IN_IMPL_NUMS[$i]}" \
            --arg t "${IN_IMPL_TITLES[$i]}" \
            --arg e "${IN_IMPL_EXECUTORS[$i]}" \
            '$arr + [{"number": $n, "title": $t, "executor": $e}]')
    done

    BLOCKED_JSON="[]"
    for ((i=0; i<${#BLOCKED_NUMS[@]}; i++)); do
        BLOCKED_JSON=$(jq -n \
            --argjson arr "$BLOCKED_JSON" \
            --argjson n "${BLOCKED_NUMS[$i]}" \
            --arg t "${BLOCKED_TITLES[$i]}" \
            --argjson d "${BLOCKED_DAYS[$i]}" \
            '$arr + [{"number": $n, "title": $t, "days_without_update": $d}]')
    done

    RECOVERY_JSON="[]"
    for ((i=0; i<${#RECOVERY_NUMS[@]}; i++)); do
        RECOVERY_JSON=$(jq -n \
            --argjson arr "$RECOVERY_JSON" \
            --argjson n "${RECOVERY_NUMS[$i]}" \
            --arg t "${RECOVERY_TITLES[$i]}" \
            '$arr + [{"number": $n, "title": $t}]')
    done

    READY_JSON="[]"
    for ((i=0; i<${#READY_NUMS[@]}; i++)); do
        READY_JSON=$(jq -n \
            --argjson arr "$READY_JSON" \
            --argjson n "${READY_NUMS[$i]}" \
            --arg t "${READY_TITLES[$i]}" \
            --arg e "${READY_EXECUTORS[$i]}" \
            '$arr + [{"number": $n, "title": $t, "executor": $e}]')
    done

    jq -n \
        --arg report_date "$TODAY" \
        --arg since "$SINCE" \
        --arg repo "$REPO" \
        --argjson completed "$COMPLETED_JSON" \
        --argjson in_implementation "$IN_IMPL_JSON" \
        --argjson wip_count "$WIP_COUNT" \
        --argjson wip_violation "$WIP_VIOLATION" \
        --argjson blocked "$BLOCKED_JSON" \
        --argjson recoveries "$RECOVERY_JSON" \
        --argjson ready_queue "$READY_JSON" \
        --arg flow_health "$FLOW_HEALTH" \
        '{
            report_date: $report_date,
            since: $since,
            repo: $repo,
            completed: $completed,
            in_implementation: $in_implementation,
            wip_count: $wip_count,
            wip_violation: $wip_violation,
            blocked: $blocked,
            recoveries: $recoveries,
            ready_queue: $ready_queue,
            flow_health: $flow_health
        }'
else
    printf '%s' "$REPORT"
fi

# ─── Post report to GitHub issue ──────────────────────────────────────────────

if [[ -n "$POST_ISSUE" ]]; then
    COMMENT_BODY=$(jq -nc --arg body "$REPORT" '{body: $body}')
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Content-Type: application/json" \
        -X POST \
        --data "$COMMENT_BODY" \
        "$API/repos/$OWNER/$REPO_NAME/issues/$POST_ISSUE/comments")
    if [[ "$HTTP_CODE" == "201" ]]; then
        echo "Report posted as comment on #${POST_ISSUE}." >&2
    else
        echo "Warning: Failed to post report to issue #${POST_ISSUE} (HTTP ${HTTP_CODE})." >&2
        exit 1
    fi
fi
