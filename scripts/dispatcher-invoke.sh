#!/usr/bin/env bash
# scripts/dispatcher-invoke.sh
#
# Dispatcher pre-flight gate — executor routing, WIP check, stale detection,
# file-overlap check, and conflict blocker.
# Implements: issue #171 (Feature #162)
# Policy source of truth: docs/PROJECT-STATUS.md
#
# Usage:
#   GH_TOKEN=<token> bash scripts/dispatcher-invoke.sh \
#       --repo <owner/repo> --issue <issue-number> [--dry-run]
#
# Exit codes:
#   0  — all pre-flight checks pass; safe to proceed to executor invocation
#   1  — pre-flight check failed; conflict blocker posted on the target issue
#
# Requirements: bash 4+, gh (GitHub CLI), jq
#
# ─── Executor routing table ───────────────────────────────────────────────────
#
# Add a new executor tier by adding a row here and creating the corresponding
# GitHub Secret. Do not add executor-specific conditional logic elsewhere
# (OSS compatibility invariant: routing is data-driven, not logic-driven).
#
# | executor: label             | Executor type  | GitHub Secret name         |
# |-----------------------------|----------------|----------------------------|
# | executor:claude-code-haiku  | Claude Haiku   | ANTHROPIC_API_KEY_HAIKU    |
# | executor:claude-code-sonnet | Claude Sonnet  | ANTHROPIC_API_KEY_SONNET   |
# | executor:claude-code-opus   | Claude Opus    | ANTHROPIC_API_KEY_OPUS     |
# | executor:codex              | Codex          | OPENAI_API_KEY_CODEX       |
#
# Authentication:
#   GH_TOKEN (or GITHUB_TOKEN) — GitHub personal access token or Actions token.
#   In GitHub Actions, reference secrets via ${{ secrets.GH_TOKEN }}.
#   Do not hardcode any token or API key in this script.
#
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ─── Executor routing table (data-driven) ─────────────────────────────────────
# Format: ["<label-suffix>"]="<DisplayName>:<SECRET_NAME>"
declare -A EXECUTOR_ROUTING=(
    ["claude-code-haiku"]="Claude Haiku:ANTHROPIC_API_KEY_HAIKU"
    ["claude-code-sonnet"]="Claude Sonnet:ANTHROPIC_API_KEY_SONNET"
    ["claude-code-opus"]="Claude Opus:ANTHROPIC_API_KEY_OPUS"
    ["codex"]="Codex:OPENAI_API_KEY_CODEX"
)

# ─── Argument parsing ─────────────────────────────────────────────────────────

REPO=""
ISSUE_NUMBER=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            [[ $# -ge 2 ]] || { echo "Error: --repo requires a value." >&2; exit 1; }
            REPO="$2"; shift 2 ;;
        --issue)
            [[ $# -ge 2 ]] || { echo "Error: --issue requires a value." >&2; exit 1; }
            ISSUE_NUMBER="$2"; shift 2 ;;
        --dry-run)
            DRY_RUN=true; shift ;;
        *)
            echo "Error: Unknown flag: $1" >&2; exit 1 ;;
    esac
done

[[ -n "$REPO" ]]         || { echo "Error: --repo <owner/repo> is required." >&2;    exit 1; }
[[ -n "$ISSUE_NUMBER" ]] || { echo "Error: --issue <issue-number> is required." >&2; exit 1; }
[[ "$REPO" == *"/"* ]]   || { echo "Error: --repo must be in owner/repo format." >&2; exit 1; }

# ─── Authentication ───────────────────────────────────────────────────────────

GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
[[ -n "$GH_TOKEN" ]] || {
    echo "Error: GitHub token not found. Set GH_TOKEN or GITHUB_TOKEN." >&2
    exit 1
}
export GH_TOKEN

# ─── Dependency check ─────────────────────────────────────────────────────────

for cmd in gh jq; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "Error: '$cmd' is required but not found in PATH." >&2; exit 1
    }
done

# ─── Variables ────────────────────────────────────────────────────────────────

OWNER="${REPO%%/*}"
REPO_NAME="${REPO##*/}"
DECLARED_BY="dispatcher"
NOW_TS=$(date -u +%s)
STALE_THRESHOLD=7200   # 2 hours in seconds
WIP_LIMIT=2

# ─── Utility: ISO-8601 timestamp to unix seconds (Linux + macOS) ──────────────

iso_to_ts() {
    local iso="$1"
    date -u -d "$iso" +%s 2>/dev/null \
        || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null \
        || echo 0
}

# ─── Utility: extract "## Files affected" list from a markdown body ───────────

extract_files() {
    local body="$1"
    echo "$body" \
        | awk '/^## Files affected/{found=1; next} found && /^##/{exit} found{print}' \
        | grep -E '^\s*-\s' \
        | sed 's/^\s*-\s*//' \
        | sed 's/`//g' \
        | sed 's/\s*(.*)//' \
        | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
        | grep -v '^$' || true
}

# ─── Utility: check whether an issue or PR has a handoff note ────────────────

has_handoff_note() {
    local issue_num="$1"
    local pr_number="${2:-}"  # optional pre-fetched PR number; avoids a redundant API call
    # Check issue comments
    if gh api "repos/$REPO/issues/$issue_num/comments?per_page=100" \
            | jq -e '[.[] | select(.body | test("## Handoff Note"))] | length > 0' \
            >/dev/null 2>&1; then
        return 0
    fi
    # If caller did not supply a PR number, find the linked open PR now
    if [[ -z "$pr_number" ]]; then
        pr_number=$(gh api "repos/$REPO/pulls?state=open&per_page=100" \
            | jq -r --argjson n "$issue_num" '
                map(select(
                    (.body // "") | ascii_downcase |
                    test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")
                )) | first | .number // empty')
    fi
    if [[ -n "$pr_number" ]]; then
        if gh api "repos/$REPO/pulls/$pr_number" \
                | jq -e '.body | test("## Handoff Note")' >/dev/null 2>&1; then
            return 0
        fi
        if gh api "repos/$REPO/issues/$pr_number/comments?per_page=100" \
                | jq -e '[.[] | select(.body | test("## Handoff Note"))] | length > 0' \
                >/dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# ─── Utility: post a GitHub issue comment ────────────────────────────────────

post_comment() {
    local issue_num="$1"
    local body="$2"
    if $DRY_RUN; then
        echo "[DRY RUN] Would post comment on #$issue_num:"
        echo "$body" | sed 's/^/  /'
    else
        gh api "repos/$REPO/issues/$issue_num/comments" \
            -X POST -f body="$body" >/dev/null
    fi
}

# ─── Utility: post a conflict blocker on the target issue and exit 1 ─────────

post_conflict_blocker() {
    local issue_num="$1"
    local issue_title="$2"
    local unblocked_when="$3"
    local body
    body="## Blocked Declaration
Type: conflict
Declared by: $DECLARED_BY
Blocks: #$issue_num — $issue_title
Unblocked when: $unblocked_when
Owner: PO (@LauraMardones)
State of in-progress work: none"
    echo "BLOCKED: posting conflict blocker on #$issue_num"
    post_comment "$issue_num" "$body"
    exit 1
}

# ─── Fetch project board data (single query) ──────────────────────────────────

echo "Fetching project board..."
BOARD_DATA=$(gh api graphql -f query='
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    projectsV2(first: 1) {
      nodes {
        id
        fields(first: 30) {
          nodes {
            ... on ProjectV2SingleSelectField {
              id
              name
              options { id name }
            }
          }
        }
        items(first: 100) {
          nodes {
            id
            content {
              ... on Issue {
                number
                title
                body
                updatedAt
              }
            }
            fieldValues(first: 20) {
              nodes {
                ... on ProjectV2ItemFieldSingleSelectValue {
                  name
                  field { ... on ProjectV2SingleSelectField { name } }
                }
              }
            }
          }
        }
      }
    }
  }
}
' -f owner="$OWNER" -f repo="$REPO_NAME") || {
    echo "Error: Failed to fetch project board data." >&2; exit 1
}

PROJECT_ID=$(echo "$BOARD_DATA" | jq -r '.data.repository.projectsV2.nodes[0].id // empty')
[[ -n "$PROJECT_ID" ]] || { echo "Error: No GitHub Projects (v2) board found on $REPO." >&2; exit 1; }

# Extract Status field ID and option IDs
STATUS_FIELD_ID=$(echo "$BOARD_DATA" | jq -r '
    .data.repository.projectsV2.nodes[0].fields.nodes[]
    | select(.name? == "Status") | .id // empty' | head -1)

get_status_option_id() {
    local status_name="$1"
    # The project board may abbreviate "Ready for implementation" as "Ready".
    # Try the exact name first, then the short alias.
    local result
    result=$(echo "$BOARD_DATA" | jq -r --arg n "$status_name" '
        .data.repository.projectsV2.nodes[0].fields.nodes[]
        | select(.name? == "Status")
        | .options[]
        | select(.name == $n)
        | .id // empty' | head -1)
    if [[ -z "$result" && "$status_name" == "Ready for implementation" ]]; then
        result=$(echo "$BOARD_DATA" | jq -r '
            .data.repository.projectsV2.nodes[0].fields.nodes[]
            | select(.name? == "Status")
            | .options[]
            | select(.name == "Ready")
            | .id // empty' | head -1)
    fi
    echo "$result"
}

# ─── Step 1: Fetch target story ───────────────────────────────────────────────

echo "Fetching target story #$ISSUE_NUMBER..."
TARGET=$(gh api "repos/$REPO/issues/$ISSUE_NUMBER") || {
    echo "Error: Could not fetch issue #$ISSUE_NUMBER from $REPO." >&2; exit 1
}

TARGET_TITLE=$(echo "$TARGET" | jq -r '.title')
TARGET_BODY=$(echo "$TARGET" | jq -r '.body // ""')
TARGET_LABELS=$(echo "$TARGET" | jq -r '[.labels[].name] | join(" ")')

# ─── Step 2: Executor label routing ──────────────────────────────────────────

EXECUTOR_LABEL=""
for lbl in $TARGET_LABELS; do
    if [[ "$lbl" == executor:* ]]; then
        EXECUTOR_LABEL="${lbl#executor:}"
        break
    fi
done

if [[ -z "$EXECUTOR_LABEL" ]]; then
    echo "Error: No executor: label found on #$ISSUE_NUMBER." >&2
    echo "       Labels present: ${TARGET_LABELS:-"(none)"}" >&2
    echo "       Add one of: ${!EXECUTOR_ROUTING[*]}" | sed 's/ /, executor:/g' >&2
    exit 1
fi

ROUTING_ENTRY="${EXECUTOR_ROUTING[$EXECUTOR_LABEL]+x}"
if [[ -z "$ROUTING_ENTRY" ]]; then
    echo "Error: Unrecognised executor label 'executor:$EXECUTOR_LABEL' on #$ISSUE_NUMBER." >&2
    echo "       Known executors: $(printf 'executor:%s  ' "${!EXECUTOR_ROUTING[@]}")" >&2
    exit 1
fi

ROUTING_VALUE="${EXECUTOR_ROUTING[$EXECUTOR_LABEL]}"
EXECUTOR_TYPE="${ROUTING_VALUE%%:*}"
EXECUTOR_SECRET="${ROUTING_VALUE##*:}"
echo "Executor: $EXECUTOR_TYPE (secret: $EXECUTOR_SECRET)"

# ─── Step 3: Collect "In implementation" stories ─────────────────────────────

IN_IMPL_ITEMS=$(echo "$BOARD_DATA" | jq '[
    .data.repository.projectsV2.nodes[0].items.nodes[]
    | select(
        (.content | type) == "object" and
        (.content.number != null) and
        ([ .fieldValues.nodes[]
           | select((.field.name? // "") == "Status" and (.name? // "") == "In implementation")
         ] | length > 0)
      )
    | { id: .id, number: .content.number, title: .content.title,
        body: (.content.body // ""), updatedAt: .content.updatedAt }
]')

IN_IMPL_COUNT=$(echo "$IN_IMPL_ITEMS" | jq 'length')
echo "Stories in \"In implementation\": $IN_IMPL_COUNT"

# ─── Step 4: Stale detection and recovery ─────────────────────────────────────
# Per docs/PROJECT-STATUS.md Recovery Protocol:
#   Stale = In implementation + no activity >2h + no handoff note (both required)

READY_FOR_IMPL_OPTION_ID=$(get_status_option_id "Ready for implementation")

set_project_status_ready() {
    local item_id="$1"
    local issue_num="$2"
    if [[ -z "$STATUS_FIELD_ID" || -z "$READY_FOR_IMPL_OPTION_ID" ]]; then
        echo "Warning: Could not resolve Status field/option IDs; skipping board update for #$issue_num." >&2
        return
    fi
    if $DRY_RUN; then
        echo "[DRY RUN] Would set #$issue_num to \"Ready for implementation\" on project board"
        return
    fi
    gh api graphql -f query='
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId
        itemId: $itemId
        fieldId: $fieldId
        value: { singleSelectOptionId: $optionId }
      }) { projectV2Item { id } }
    }
    ' -f projectId="$PROJECT_ID" \
      -f itemId="$item_id" \
      -f fieldId="$STATUS_FIELD_ID" \
      -f optionId="$READY_FOR_IMPL_OPTION_ID" >/dev/null \
    || echo "Warning: Board status update failed for #$issue_num; manual update required." >&2
}

STALE_COUNT=0
for ((i=0; i < IN_IMPL_COUNT; i++)); do
    item=$(echo "$IN_IMPL_ITEMS" | jq ".[$i]")
    item_id=$(echo "$item" | jq -r '.id')
    issue_num=$(echo "$item" | jq -r '.number')
    issue_title=$(echo "$item" | jq -r '.title')
    updated_at=$(echo "$item" | jq -r '.updatedAt')

    # Baseline: issue updatedAt (covers issue comments, label changes, etc.)
    last_ts=$(iso_to_ts "$updated_at")

    # Fetch linked open PR once — reused for activity timestamps, handoff check, and branch name.
    # Policy (docs/PROJECT-STATUS.md): activity includes commits, comments, AND PR updates.
    linked_pr=$(gh api "repos/$REPO/pulls?state=open&per_page=100" \
        | jq --argjson n "$issue_num" '
            map(select(
                (.body // "") | ascii_downcase |
                test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")
            )) | first // null')
    pr_number=$(echo "$linked_pr" | jq -r '.number // empty')
    pr_updated_at=$(echo "$linked_pr" | jq -r '.updated_at // empty')

    # PR updated_at covers PR description edits, review comments, and CI status updates
    if [[ -n "$pr_updated_at" ]]; then
        pr_ts=$(iso_to_ts "$pr_updated_at")
        [[ $pr_ts -gt $last_ts ]] && last_ts=$pr_ts
    fi

    # Latest commit pushed to the PR branch — a push updates neither issue nor PR timestamps
    if [[ -n "$pr_number" ]]; then
        latest_commit=$(gh api "repos/$REPO/pulls/$pr_number/commits?per_page=100" \
            | jq -r '[.[].commit | (.committer.date // .author.date // "")] | map(select(. != "")) | sort | last // empty')
        if [[ -n "$latest_commit" ]]; then
            commit_ts=$(iso_to_ts "$latest_commit")
            [[ $commit_ts -gt $last_ts ]] && last_ts=$commit_ts
        fi
    fi

    inactivity=$(( NOW_TS - last_ts ))

    if [[ $inactivity -le $STALE_THRESHOLD ]]; then
        continue
    fi

    # Both conditions must be true for stale: >2h inactivity AND no handoff note
    if has_handoff_note "$issue_num" "$pr_number"; then
        continue
    fi

    hours_inactive=$(( inactivity / 3600 ))
    echo "STALE: #$issue_num \"$issue_title\" — inactive ${hours_inactive}h, no handoff note"

    # Branch name: prefer PR head ref (authoritative); fall back to naming-convention search
    branch=""
    [[ -n "$pr_number" ]] && branch=$(echo "$linked_pr" | jq -r '.head.ref // empty')
    if [[ -z "$branch" ]]; then
        branch=$(gh api "repos/$REPO/branches?per_page=100" \
            | jq -r --argjson n "$issue_num" '
                map(select(.name | test("/(issue|implement)-\($n)[-_]"; "i")))
                | first | .name // "(unknown)"') || branch="(unknown)"
    fi

    recovery_comment="## Recovery Comment
Detected: story in \"In implementation\" with no activity for >2h and no handoff note.
Action: status rolled back to \"Ready for implementation\".
Branch: $branch — existing commits intact.
Next executor: review branch state before pulling."

    post_comment "$issue_num" "$recovery_comment"
    set_project_status_ready "$item_id" "$issue_num"

    STALE_COUNT=$(( STALE_COUNT + 1 ))
done

# Recalculate active (non-stale) WIP count after recovery rollbacks
# Re-query the board to get updated state
if [[ $STALE_COUNT -gt 0 && $DRY_RUN == false ]]; then
    echo "Re-fetching board after stale recovery..."
    BOARD_DATA=$(gh api graphql -f query='
    query($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        projectsV2(first: 1) {
          nodes {
            id
            items(first: 100) {
              nodes {
                id
                content { ... on Issue { number title body updatedAt } }
                fieldValues(first: 20) {
                  nodes {
                    ... on ProjectV2ItemFieldSingleSelectValue {
                      name
                      field { ... on ProjectV2SingleSelectField { name } }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    ' -f owner="$OWNER" -f repo="$REPO_NAME")

    IN_IMPL_ITEMS=$(echo "$BOARD_DATA" | jq '[
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select(
            (.content | type) == "object" and
            (.content.number != null) and
            ([ .fieldValues.nodes[]
               | select((.field.name? // "") == "Status" and (.name? // "") == "In implementation")
             ] | length > 0)
          )
        | { id: .id, number: .content.number, title: .content.title,
            body: (.content.body // ""), updatedAt: .content.updatedAt }
    ]')
    IN_IMPL_COUNT=$(echo "$IN_IMPL_ITEMS" | jq 'length')
fi

# ─── Step 5: WIP limit check ──────────────────────────────────────────────────
# Policy: max 2 parallel stories In implementation (docs/PROJECT-STATUS.md)

echo "Active WIP count: $IN_IMPL_COUNT (limit: $WIP_LIMIT)"

if [[ $IN_IMPL_COUNT -ge $WIP_LIMIT ]]; then
    active_list=$(echo "$IN_IMPL_ITEMS" | jq -r '.[] | "  #\(.number) \(.title)"')
    echo "FAIL: WIP limit reached ($IN_IMPL_COUNT >= $WIP_LIMIT)"
    echo "$active_list"
    unblocked_when="Fewer than $WIP_LIMIT stories are in \"In implementation\" status on the project board"
    post_conflict_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "$unblocked_when"
fi

# ─── Step 6: File overlap check ───────────────────────────────────────────────

TARGET_FILE_LIST=$(extract_files "$TARGET_BODY")

if [[ -z "$TARGET_FILE_LIST" ]]; then
    echo "Warning: No '## Files affected' section found on #$ISSUE_NUMBER; skipping file overlap check."
else
    echo "Target files:"
    echo "$TARGET_FILE_LIST" | sed 's/^/  /'

    OVERLAP_FOUND=false
    OVERLAP_DETAILS=""

    for ((i=0; i < IN_IMPL_COUNT; i++)); do
        item=$(echo "$IN_IMPL_ITEMS" | jq ".[$i]")
        active_num=$(echo "$item" | jq -r '.number')
        active_title=$(echo "$item" | jq -r '.title')
        active_body=$(echo "$item" | jq -r '.body')
        active_files=$(extract_files "$active_body")

        if [[ -z "$active_files" ]]; then
            continue
        fi

        # Find intersection
        while IFS= read -r tfile; do
            while IFS= read -r afile; do
                if [[ "$tfile" == "$afile" ]]; then
                    OVERLAP_FOUND=true
                    OVERLAP_DETAILS+="  $tfile (owned by #$active_num \"$active_title\")"$'\n'
                fi
            done <<< "$active_files"
        done <<< "$TARGET_FILE_LIST"
    done

    if $OVERLAP_FOUND; then
        echo "FAIL: File overlap detected:"
        echo "$OVERLAP_DETAILS"
        unblocked_when="The overlapping files are no longer owned by an active \"In implementation\" story: $(echo "$OVERLAP_DETAILS" | head -1 | sed 's/^ *//')"
        post_conflict_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "$unblocked_when"
    fi
fi

# ─── All checks passed ────────────────────────────────────────────────────────

echo ""
echo "✓ Executor routing:  $EXECUTOR_TYPE (secret: $EXECUTOR_SECRET)"
echo "✓ WIP limit:         $IN_IMPL_COUNT active / $WIP_LIMIT limit"
echo "✓ File overlap:      none"
[[ $STALE_COUNT -gt 0 ]] && echo "✓ Stale recovery:    $STALE_COUNT story(ies) rolled back"
echo ""
echo "Pre-flight passed for #$ISSUE_NUMBER — proceed to executor invocation."
exit 0
