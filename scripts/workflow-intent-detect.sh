#!/usr/bin/env bash
# scripts/workflow-intent-detect.sh
#
# Prototype: detect workflow intent signals from GitHub Projects status.
# Reads all items in the linked project board and identifies those whose
# status is "Ready for implementation" (the executor-pull intent signal).
# Performs no GitHub state mutation of any kind.
#
# Usage:
#   GH_TOKEN=<token> bash scripts/workflow-intent-detect.sh --repo <owner/repo>
#
# Requirements: bash, curl, jq
#
# Prototype divergences from expected behaviour:
#   D1  Pagination: fetches max 100 project items per request; repos with
#       >100 project items may produce incomplete results.
#   D2  Project auto-detection: uses the first linked GitHub Projects (v2)
#       board. Repos with multiple linked projects may detect the wrong board.
#   D3  Label fetch: executor labels are fetched from the GraphQL project
#       item's linked issue. Items with no linked issue (draft notes, PR
#       items) are skipped entirely.

set -euo pipefail

# ─── Argument parsing ─────────────────────────────────────────────────────────

REPO=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            [[ $# -ge 2 ]] || { echo "Error: --repo requires a value." >&2; exit 1; }
            REPO="$2"; shift 2
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

# ─── Fetch project items with status and labels ───────────────────────────────

# D1: max 100 items
ITEMS=$(graphql "{
  repository(owner:\"$OWNER\", name:\"$REPO_NAME\") {
    projectsV2(first:1) { nodes { items(first:100) { nodes {
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
    }}}}
  }
}")

# ─── Detect intent signals ────────────────────────────────────────────────────

DETECTED=()
DETECTED_NUMS=()
DETECTED_TITLES=()
DETECTED_EXECUTORS=()

N=$(echo "$ITEMS" | jq '.data.repository.projectsV2.nodes[0].items.nodes | length')

for ((i=0; i<N; i++)); do
    ITEM=$(echo "$ITEMS" | jq ".data.repository.projectsV2.nodes[0].items.nodes[$i]")

    # D3: skip non-issue items
    NUM=$(echo "$ITEM" | jq -r '.content.number // empty')
    [[ -n "$NUM" ]] || continue

    TITLE=$(echo "$ITEM" | jq -r '.content.title // "(no title)"')

    STATUS=$(echo "$ITEM" | jq -r '
        [.fieldValues.nodes[] |
         select((.field.name? // "") == "Status" and .name? != null) |
         .name] | first // ""')

    [[ "$STATUS" == "Ready for implementation" ]] || continue

    EXECUTOR=$(echo "$ITEM" | jq -r '
        [.content.labels.nodes[]? | select(.name | startswith("executor:")) | .name] |
        first // ""')

    DETECTED_NUMS+=("$NUM")
    DETECTED_TITLES+=("$TITLE")
    DETECTED_EXECUTORS+=("$EXECUTOR")
done

SIGNAL_COUNT="${#DETECTED_NUMS[@]}"

# ─── Output ───────────────────────────────────────────────────────────────────

if [[ "$SIGNAL_COUNT" -eq 0 ]]; then
    echo "No intent signals detected."
    exit 0
fi

echo "Intent signals detected: $SIGNAL_COUNT item(s) ready for implementation."
echo ""

for ((i=0; i<SIGNAL_COUNT; i++)); do
    NUM="${DETECTED_NUMS[$i]}"
    TITLE="${DETECTED_TITLES[$i]}"
    EXECUTOR="${DETECTED_EXECUTORS[$i]}"

    if [[ -n "$EXECUTOR" ]]; then
        echo "[INTENT] #$NUM \"$TITLE\" [$EXECUTOR]"
    else
        echo "[INTENT] #$NUM \"$TITLE\""
    fi
done

echo ""
echo "Summary: $SIGNAL_COUNT intent signal(s) detected."
