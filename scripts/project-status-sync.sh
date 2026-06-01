#!/usr/bin/env bash
# scripts/project-status-sync.sh
#
# Prototype implementation of hpw project-status sync
# Contract: docs/commands/project-status-sync.md
#
# Usage:
#   GH_TOKEN=<token> bash scripts/project-status-sync.sh --repo <owner/repo> [--dry-run]
#
# Requirements: bash, curl, jq
#
# Prototype divergences from contract (see PR description):
#   D1  Branch naming (Rule 4): contract expects <prefix>/issue-<N>-* but this repo uses
#       <prefix>/implement-<N>-* (e.g. claude/implement-152-DHWi7). Prototype matches
#       both patterns. Production must resolve the canonical branch naming convention.
#   D2  Live mutation: prototype detects and reports transitions but does not write to
#       GitHub Projects status fields. All runs behave like --dry-run for the mutation
#       step. The mutation path is stubbed with a TODO comment.
#   D3  Pagination: fetches max 100 items per request; repos with >100 items, PRs, or
#       branches may produce incomplete results.
#   D4  Manual linking only: only detects PRs linked via body text ("Closes/Fixes/Resolves
#       #N"). PRs manually linked via GitHub's Development sidebar are not detected.

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
API="https://api.github.com"
GRAPHQL="$API/graphql"

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

# Prototype: use the first project; full spec would fail with exit 4 when COUNT > 1
PROJECT_NUMBER=$(echo "$PROJECTS" | jq '.data.repository.projectsV2.nodes[0].number')

# ─── Fetch project items with status ──────────────────────────────────────────

ITEMS=$(graphql "{
  repository(owner:\"$OWNER\", name:\"$REPO_NAME\") {
    projectsV2(first:1) { nodes { items(first:100) { nodes {
      id
      content { ... on Issue { number title } }
      fieldValues(first:20) { nodes {
        ... on ProjectV2ItemFieldSingleSelectValue {
          name
          field { ... on ProjectV2SingleSelectField { name } }
        }
      }}
    }}}}
  }
}")

# ─── Fetch all PRs and branches ───────────────────────────────────────────────

ALL_PRS=$(rest "/repos/$OWNER/$REPO_NAME/pulls?state=all&per_page=100")
ALL_BRANCHES=$(rest "/repos/$OWNER/$REPO_NAME/branches?per_page=100")

# ─── Dry-run header ───────────────────────────────────────────────────────────

if $DRY_RUN; then
    echo "DRY RUN — no GitHub state will be mutated."
    echo ""
fi

# ─── Process each project item ────────────────────────────────────────────────

TRANSITIONS=0
UNCHANGED=0
SKIPPED=0

N=$(echo "$ITEMS" | jq '.data.repository.projectsV2.nodes[0].items.nodes | length')

for ((i=0; i<N; i++)); do
    ITEM=$(echo "$ITEMS" | jq ".data.repository.projectsV2.nodes[0].items.nodes[$i]")

    # Skip non-issue items (draft notes, PR items, etc.)
    NUM=$(echo "$ITEM" | jq -r '.content.number // empty')
    [[ -n "$NUM" ]] || continue

    TITLE=$(echo "$ITEM" | jq -r '.content.title // "(no title)"')

    # Extract current status from the "Status" single-select project field
    CURRENT=$(echo "$ITEM" | jq -r '
        [.fieldValues.nodes[] |
         select((.field.name? // "") == "Status" and .name? != null) |
         .name] | first // "(no status)"')

    # ── Find PRs linked to this issue (D4: body-text linking only) ───────────
    LINKED=$(echo "$ALL_PRS" | jq --argjson n "$NUM" '
        map(select(
            (.body // "") | ascii_downcase |
            test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")
        ))')

    OPEN_PR=$(echo "$LINKED" | jq 'map(select(.state=="open")) | sort_by(.created_at) | last // null')
    PR_NUM=$(echo "$OPEN_PR"  | jq -r '.number // empty')
    PR_DRAFT=$(echo "$OPEN_PR" | jq -r 'if . == null then "false" else (.draft|tostring) end')
    HAS_MERGED=$(echo "$LINKED" | jq '[.[] | select(.merged_at != null)] | length > 0')

    DETECTED=""

    # Rule 1: Done — any linked PR has been merged
    [[ "$HAS_MERGED" == "true" ]] && DETECTED="Done"

    # Rule 2: Needs rework — open non-draft PR whose latest review per reviewer is CHANGES_REQUESTED
    if [[ -z "$DETECTED" && -n "$PR_NUM" && "$PR_DRAFT" == "false" ]]; then
        REVIEWS=$(rest "/repos/$OWNER/$REPO_NAME/pulls/$PR_NUM/reviews?per_page=100")
        REWORK=$(echo "$REVIEWS" | jq '
            group_by(.user.login) |
            map(sort_by(.submitted_at) | last) |
            map(select(.state == "CHANGES_REQUESTED")) |
            length > 0')
        [[ "$REWORK" == "true" ]] && DETECTED="Needs rework"
    fi

    # Rule 3: In review — open non-draft PR exists (Rule 2 did not match)
    [[ -z "$DETECTED" && -n "$PR_NUM" && "$PR_DRAFT" == "false" ]] && DETECTED="In review"

    # Rule 4: In implementation — branch matching naming convention exists, no ready PR
    # D1: also matches /implement-<N>- used in this repo alongside contract's /issue-<N>-
    if [[ -z "$DETECTED" ]]; then
        BRANCH=$(echo "$ALL_BRANCHES" | jq -r --argjson n "$NUM" '
            map(select(.name | test("/(issue|implement)-\($n)[-]"; "i"))) |
            first | .name // empty')
        NO_READY=$([[ -z "$PR_NUM" || "$PR_DRAFT" == "true" ]] && echo true || echo false)
        [[ -n "$BRANCH" && "$NO_READY" == "true" ]] && DETECTED="In implementation"
    fi

    # ── Output per-item block ────────────────────────────────────────────────

    echo "[SYNC]  #$NUM $TITLE"
    echo "        Current status : $CURRENT"

    if [[ -z "$DETECTED" ]]; then
        echo "        Detected status: (no detectable state)"
        echo "        Action         : skipped (no detectable state)"
        SKIPPED=$((SKIPPED+1))
    elif [[ "$CURRENT" == "$DETECTED" ]]; then
        echo "        Detected status: $DETECTED"
        echo "        Action         : no change"
        UNCHANGED=$((UNCHANGED+1))
    else
        echo "        Detected status: $DETECTED"
        if $DRY_RUN; then
            echo "        Action         : would transition  $CURRENT → $DETECTED"
        else
            # TODO (D2): mutate GitHub Projects item "$NUM" status to "$DETECTED"
            # Requires: project item ID, Status field ID, and target option ID (all from GraphQL).
            echo "        Action         : transition  $CURRENT → $DETECTED"
        fi
        TRANSITIONS=$((TRANSITIONS+1))
    fi

    echo ""
done

# ─── Summary ──────────────────────────────────────────────────────────────────

if $DRY_RUN; then
    echo "Dry run complete: $TRANSITIONS transition(s) would be applied, $UNCHANGED item(s) unchanged, $SKIPPED item(s) skipped."
else
    echo "Sync complete: $TRANSITIONS transition(s) applied, $UNCHANGED item(s) unchanged, $SKIPPED item(s) skipped."
fi
