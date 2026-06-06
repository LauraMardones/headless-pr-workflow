#!/usr/bin/env bash
# scripts/dispatcher-poll.sh
#
# Prototype: query GitHub Projects v2 for items with status
# "Ready for implementation" and output a JSON array of results.
# Contract: issue #170 (Story: Implement scripts/dispatcher-poll.sh)
#
# Usage:
#   GH_TOKEN=<token> bash scripts/dispatcher-poll.sh --repo <owner/repo> [--dry-run]
#
# Requirements: bash, curl, jq
#
# Output (stdout):
#   JSON array: [{"number": N, "title": "..."}, ...]
#   Empty array ([]) when no items match.
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
#       line to stdout; behaviour is otherwise identical.

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

# ─── Filter items with "Ready for implementation" status ──────────────────────

READY=$(echo "$ITEMS" | jq '[
  .data.repository.projectsV2.nodes[0].items.nodes[] |
  select(
    (.content.number? != null) and
    ([ .fieldValues.nodes[] |
       select((.field.name? // "") == "Status" and (.name? // "") == "Ready for implementation")
    ] | length > 0)
  ) |
  { number: .content.number, title: .content.title }
]')

SIGNAL_COUNT=$(echo "$READY" | jq 'length')

# ─── Dry-run header ───────────────────────────────────────────────────────────

if $DRY_RUN; then
    echo "DRY RUN — no executor invocation or GitHub state mutation will occur." >&2
    echo "" >&2
fi

# ─── Log found items ──────────────────────────────────────────────────────────

if [[ "$SIGNAL_COUNT" -eq 0 ]]; then
    echo "No items found with status \"Ready for implementation\"." >&2
else
    echo "Items ready for implementation: $SIGNAL_COUNT" >&2
    echo "" >&2
    echo "$READY" | jq -r '.[] | "[POLL] #\(.number) \(.title)"' >&2
    echo "" >&2
    echo "Summary: $SIGNAL_COUNT item(s) found." >&2
fi

# ─── JSON output (stdout) ─────────────────────────────────────────────────────

echo "$READY"
