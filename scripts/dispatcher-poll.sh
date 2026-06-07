#!/usr/bin/env bash
# scripts/dispatcher-poll.sh
#
# Prototype: query GitHub Projects v2 for items with status
# "Ready for implementation" or "Ready for refinement" and output a JSON
# object with both result lists.
# Contract: issue #170 (initial implementation), issue #181 (dual-status extension),
#           issue #182 (ready-for-refinement notification)
#
# Usage:
#   GH_TOKEN=<token> bash scripts/dispatcher-poll.sh --repo <owner/repo> [--dry-run]
#
# Environment (optional):
#   SLACK_WEBHOOK_URL  Required by slack-notify.sh for ready_for_refinement notifications.
#                      If unset, slack-notify.sh exits 1; the poll loop continues (|| true).
#
# Side effects:
#   For each "Ready for refinement" issue not yet notified this runner session,
#   calls scripts/slack-notify.sh ready_for_refinement. Notification state is
#   tracked in a local temp file (see "Ready for refinement notification" section).
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
#   D1  Pagination: fetches max 100 project items per request; repos with
#       >100 project items may produce incomplete results.
#   D2  Project auto-detection: uses the first linked GitHub Projects (v2)
#       board. Repos with multiple linked projects may detect the wrong board.
#   D3  Label fetch: executor labels are fetched from the GraphQL project
#       item's linked issue. Items with no linked issue (draft notes, PR
#       items) are skipped entirely.
#   D4  Dry-run is the only supported mode: no executor invocation or GitHub
#       state mutation occurs in any run. The --dry-run flag adds a header
#       line to stderr; behaviour is otherwise identical.
#   D5  Single GraphQL round-trip: both status filters are applied in jq
#       against the same items response. No second network request is made.
#   D6  Refinement notification de-duplication resets on runner restart. The
#       state file lives in TMPDIR and is not persisted across CI runner
#       restarts. Accepted prototype limitation; document in ops runbooks.

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

# ─── Fetch project items with status ──────────────────────────────────────────

# D1: max 100 items
ITEMS=$(graphql "{
  repository(owner:\"$OWNER\", name:\"$REPO_NAME\") {
    projectsV2(first:1) { nodes { items(first:100) { nodes {
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
    }}}}
  }
}")

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
# De-duplication state file: one notified issue number per line.
# Survives within a single runner session; resets on runner restart (D6).
# State file path: ${TMPDIR:-/tmp}/dispatcher-poll-notified-refinement-${REPO//\//-}

REFINE_NOTIFIED_FILE="${TMPDIR:-/tmp}/dispatcher-poll-notified-refinement-${REPO//\//-}"

if [[ "$REFINE_COUNT" -gt 0 ]]; then
    while IFS= read -r item; do
        NUMBER=$(echo "$item" | jq -r '.number')
        TITLE=$(echo "$item" | jq -r '.title')
        URL="https://github.com/${REPO}/issues/${NUMBER}"

        if ! grep -qxF "$NUMBER" "$REFINE_NOTIFIED_FILE" 2>/dev/null; then
            CONTEXT_JSON=$(jq -n \
                --arg issue_title "$TITLE" \
                --arg issue_url "$URL" \
                '{"issue_title": $issue_title, "issue_url": $issue_url}')
            bash "$(dirname "$0")/slack-notify.sh" ready_for_refinement "$CONTEXT_JSON" || true
            echo "$NUMBER" >> "$REFINE_NOTIFIED_FILE"
            echo "[POLL] Notified: ready_for_refinement #${NUMBER} ${TITLE}" >&2
        fi
    done < <(echo "$READY_FOR_REFINE" | jq -c '.[]')
fi

# ─── JSON output (stdout) ─────────────────────────────────────────────────────

jq -n \
    --argjson impl "$READY_FOR_IMPL" \
    --argjson refine "$READY_FOR_REFINE" \
    '{ready_for_implementation: $impl, ready_for_refinement: $refine}'
