#!/usr/bin/env bash
# scripts/dispatcher-invoke.sh
#
# Dispatcher pre-flight gate and execution loop — executor routing, WIP check,
# stale detection, file-overlap check, conflict blocker, and full story-cycle
# invocation (/implement → /review → /merge → /cleanup) with board status
# transitions. After each story completes or is blocked, the loop finds the
# next "Ready for implementation" story and repeats the full pre-flight + cycle.
# Implements: issue #171 (pre-flight gate), issue #172 (execution loop, Feature #162),
#             issue #179 (Slack notification wiring — decision_blocker, dispatcher_error,
#                         feature_closure_confirmation, epic_closure_approval)
# Policy source of truth: docs/PROJECT-STATUS.md
#
# Usage:
#   GH_TOKEN=<token> bash scripts/dispatcher-invoke.sh \
#       --repo <owner/repo> --issue <issue-number> [--dry-run]
#
# Exit codes:
#   0  — story completed (Done) or no more "Ready for implementation" stories
#   1  — pre-flight check failed or mid-cycle executor failure; blocker posted on issue
#
# Requirements: bash 4+, gh (GitHub CLI), jq
#
# ─── Executor routing table ───────────────────────────────────────────────────
#
# Add a new executor tier by adding a row here and creating the corresponding
# GitHub Secret. Do not add executor-specific conditional logic elsewhere
# (OSS compatibility invariant: routing is data-driven, not logic-driven).
#
# | executor: label             | Executor type  | GitHub Secret name         | CLI env var name    |
# |-----------------------------|----------------|----------------------------|---------------------|
# | executor:claude-code-haiku  | Claude Haiku   | ANTHROPIC_API_KEY_HAIKU    | ANTHROPIC_API_KEY   |
# | executor:claude-code-sonnet | Claude Sonnet  | ANTHROPIC_API_KEY_SONNET   | ANTHROPIC_API_KEY   |
# | executor:claude-code-opus   | Claude Opus    | ANTHROPIC_API_KEY_OPUS     | ANTHROPIC_API_KEY   |
# | executor:codex              | Codex          | OPENAI_API_KEY_CODEX       | OPENAI_API_KEY      |
#
# Authentication:
#   GH_TOKEN (or GITHUB_TOKEN) — GitHub personal access token or Actions token.
#   In GitHub Actions, the workflow sets GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}.
#   PROJECT_TOKEN must be a PAT with repo + read:project scopes; the built-in
#   GITHUB_TOKEN lacks read:project for user-owned Projects v2.
#   Do not hardcode any token or API key in this script.
#
# ─── Notification events (issue #179) ────────────────────────────────────────
#
# Four Slack events are wired into this script via scripts/slack-notify.sh:
#   decision_blocker          — executor declared Type: decision during /implement or /review
#   dispatcher_error          — unexpected script failure (EXIT trap, non-zero, unhandled)
#   feature_closure_confirmation — all stories in a feature group reached Done
#   epic_closure_approval     — all features in an epic have all stories Done
#
# Notification calls are guarded with || true; adapter failure never aborts the loop.
# Under --dry-run, calls are suppressed and logged instead.
# Conflict and dependency blockers (WIP overflow, file overlap) do NOT fire decision_blocker.
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

# ─── Executor CLI routing table (data-driven, parallel to EXECUTOR_ROUTING) ───
# Maps executor label suffix to the CLI binary used for command invocation.
# Add a new row here when adding a new executor tier (OSS invariant: data-driven).
declare -A EXECUTOR_CLI=(
    ["claude-code-haiku"]="claude"
    ["claude-code-sonnet"]="claude"
    ["claude-code-opus"]="claude"
    ["codex"]="codex"
)

# ─── Executor API key env-var table (data-driven, parallel to EXECUTOR_CLI) ───
# Maps executor label suffix to the env-var name that its CLI expects for auth.
# The GitHub Secret name (EXECUTOR_SECRET) holds the value; this table names the
# variable under which that value is passed to the CLI.
declare -A EXECUTOR_API_KEY_ENV=(
    ["claude-code-haiku"]="ANTHROPIC_API_KEY"
    ["claude-code-sonnet"]="ANTHROPIC_API_KEY"
    ["claude-code-opus"]="ANTHROPIC_API_KEY"
    ["codex"]="OPENAI_API_KEY"
)

# ─── Executor budget type table (data-driven, parallel to EXECUTOR_ROUTING) ───
# Maps executor label suffix to the budget executor type accepted by dispatcher-budget.sh.
# Add a new row here when adding a new executor tier (OSS invariant: data-driven).
declare -A EXECUTOR_BUDGET_TYPE=(
    ["claude-code-haiku"]="haiku"
    ["claude-code-sonnet"]="sonnet"
    ["claude-code-opus"]="opus"
    ["codex"]="codex"
)

# ─── Argument parsing ─────────────────────────────────────────────────────────

REPO=""
ISSUE_NUMBER=""
DRY_RUN=false
BUDGET_BLOCKED_COUNT=0
TOTAL_READY_COUNT=0

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
        --budget-blocked-count)
            [[ $# -ge 2 ]] || { echo "Error: --budget-blocked-count requires a value." >&2; exit 1; }
            BUDGET_BLOCKED_COUNT="$2"; shift 2 ;;
        --total-ready-count)
            [[ $# -ge 2 ]] || { echo "Error: --total-ready-count requires a value." >&2; exit 1; }
            TOTAL_READY_COUNT="$2"; shift 2 ;;
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUDGET_SCRIPT="$SCRIPT_DIR/dispatcher-budget.sh"

# Dispatcher error tracking — used by the EXIT trap and notification wiring (issue #179)
LAST_ACTION="initializing"
DISPATCH_HANDLED=false  # set true before any expected exit 1 (blocker posted)

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

# ─── Utility: send a Slack notification via slack-notify.sh (issue #179) ─────
# All calls use || true so adapter failure never aborts the invoke loop.
# Under --dry-run, calls are suppressed and logged.

notify_slack() {
    local event_type="$1"
    local context_json="$2"
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if $DRY_RUN; then
        echo "[DRY RUN] Would notify Slack: $event_type $context_json"
        return 0
    fi
    SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}" \
        bash "$script_dir/slack-notify.sh" "$event_type" "$context_json" || true
}

# ─── Dispatcher error EXIT trap (issue #179) ──────────────────────────────────
# Fires dispatcher_error notification on unexpected exits.
# DISPATCH_HANDLED=true suppresses the notification for expected exits (blockers posted).

_dispatcher_exit_hook() {
    local rc=$?
    [[ $rc -eq 0 ]] && return 0
    $DISPATCH_HANDLED && return 0
    notify_slack "dispatcher_error" \
        "$(jq -n \
               --arg desc "Dispatcher exited unexpectedly (exit code $rc)" \
               --arg action "$LAST_ACTION" \
               '{error_description: $desc, last_action: $action}')"
}
trap _dispatcher_exit_hook EXIT

# ─── Utility: detect decision-type blocker and notify Slack (issue #179) ─────
# Called after an executor fails mid-cycle. Scans recent issue comments for a
# ## Blocked Declaration with Type: decision (posted by the executor). Fires
# decision_blocker Slack event only for decision-type blockers — conflict and
# dependency blockers (WIP, file overlap) do NOT trigger this notification.

check_and_notify_decision_blocker() {
    local issue_num="$1"
    local issue_title="$2"
    local blocker_body
    blocker_body=$(gh api "repos/$REPO/issues/$issue_num/comments?per_page=100" \
        | jq -r '[.[] | select(.body | (test("## Blocked Declaration") and test("Type: decision")))]
                  | last | .body // empty' 2>/dev/null || true)
    [[ -z "$blocker_body" ]] && return 0
    local unblocked_when issue_url
    unblocked_when=$(echo "$blocker_body" \
        | grep -oP '(?<=Unblocked when: ).*' | head -1 \
        | sed 's/[[:space:]]*$//' || echo "condition to be determined by PO")
    issue_url="https://github.com/$REPO/issues/$issue_num"
    notify_slack "decision_blocker" \
        "$(jq -n \
               --arg st "$issue_title" \
               --arg iu "$issue_url" \
               --arg bt "decision" \
               --arg uw "$unblocked_when" \
               '{story_title: $st, issue_url: $iu, blocker_type: $bt, unblocked_when: $uw}')"
}

# ─── Utility: notify Slack on feature/epic closure after a story completes ───
# Uses the BOARD_DATA snapshot from the start of the run. Treats the
# just-completed story as Done regardless of snapshot state. Sends
# feature_closure_confirmation when all feature stories are Done, then sends
# epic_closure_approval when all features in the epic are also Done.

notify_closure_if_complete() {
    local completed_story_num="$1"

    # ── Feature closure ──────────────────────────────────────────────────────
    local feature_num
    feature_num=$(echo "$TARGET_BODY" \
        | grep -oP '(?<=Feature group: #)[0-9]+' | head -1 || true)
    [[ -z "$feature_num" ]] && return 0

    if $DRY_RUN; then
        echo "[DRY RUN] Would check feature #$feature_num closure after #$completed_story_num Done"
        return 0
    fi

    local feature_total feature_done
    feature_total=$(echo "$BOARD_DATA" | jq \
        --arg fn "$feature_num" \
        '[.data.repository.projectsV2.nodes[0].items.nodes[]
          | select(
              (.content | type) == "object" and
              (.content.number != null) and
              ((.content.body // "") | test("Feature group: #" + $fn + "([^0-9]|$)"))
            )] | length')

    [[ -z "$feature_total" || "$feature_total" -eq 0 ]] && return 0

    # Count Done stories; treat the just-completed story as Done even if board snapshot is stale
    feature_done=$(echo "$BOARD_DATA" | jq \
        --arg fn "$feature_num" \
        --argjson cn "$completed_story_num" \
        '[.data.repository.projectsV2.nodes[0].items.nodes[]
          | select(
              (.content | type) == "object" and
              (.content.number != null) and
              ((.content.body // "") | test("Feature group: #" + $fn + "([^0-9]|$)")) and
              (
                .content.number == $cn or
                ([.fieldValues.nodes[]
                  | select((.field.name? // "") == "Status" and (.name? // "") == "Done")
                ] | length > 0)
              )
            )] | length')

    [[ -z "$feature_done" || "$feature_done" -lt "$feature_total" ]] && return 0

    # All stories in the feature are Done — notify feature closure confirmation
    local feature_title feature_url
    feature_title=$(gh api "repos/$REPO/issues/$feature_num" --jq '.title' 2>/dev/null \
                    || echo "Feature #$feature_num")
    feature_url="https://github.com/$REPO/issues/$feature_num"
    echo "Feature #$feature_num: all $feature_total stories Done. Notifying Slack (feature_closure_confirmation)."
    notify_slack "feature_closure_confirmation" \
        "$(jq -n \
               --arg ft "$feature_title" \
               --arg su "$feature_url" \
               '{feature_title: $ft, summary_url: $su}')"

    # ── Epic closure ─────────────────────────────────────────────────────────
    local feature_body epic_num
    feature_body=$(gh api "repos/$REPO/issues/$feature_num" --jq '.body // ""' 2>/dev/null \
                   || echo "")
    epic_num=$(echo "$feature_body" \
        | grep -oP '(?<=Parent epic: #)[0-9]+' | head -1 || true)
    [[ -z "$epic_num" ]] && return 0

    # Collect all features in the epic from the board (features reference epic via body)
    local epic_feature_nums
    epic_feature_nums=$(echo "$BOARD_DATA" | jq -r \
        --arg en "$epic_num" \
        '.data.repository.projectsV2.nodes[0].items.nodes[]
          | select(
              (.content | type) == "object" and
              (.content.number != null) and
              ((.content.body // "") | test("Parent epic: #" + $en + "([^0-9]|$)"))
            )
          | .content.number')
    [[ -z "$epic_feature_nums" ]] && return 0

    # Verify all features' stories on the board are Done
    local all_features_done=true
    while IFS= read -r fn; do
        local ftotal fdone
        ftotal=$(echo "$BOARD_DATA" | jq \
            --arg fn "$fn" \
            '[.data.repository.projectsV2.nodes[0].items.nodes[]
              | select(
                  (.content | type) == "object" and
                  (.content.number != null) and
                  ((.content.body // "") | test("Feature group: #" + $fn + "([^0-9]|$)"))
                )] | length')
        fdone=$(echo "$BOARD_DATA" | jq \
            --arg fn "$fn" \
            --argjson cn "$completed_story_num" \
            '[.data.repository.projectsV2.nodes[0].items.nodes[]
              | select(
                  (.content | type) == "object" and
                  (.content.number != null) and
                  ((.content.body // "") | test("Feature group: #" + $fn + "([^0-9]|$)")) and
                  (
                    .content.number == $cn or
                    ([.fieldValues.nodes[]
                      | select((.field.name? // "") == "Status" and (.name? // "") == "Done")
                    ] | length > 0)
                  )
                )] | length')
        if [[ -n "$ftotal" && "$ftotal" -gt 0 && "$fdone" -lt "$ftotal" ]]; then
            all_features_done=false
            break
        fi
    done <<< "$epic_feature_nums"

    $all_features_done || return 0

    # All features' stories are Done — notify epic closure approval
    local epic_title epic_url
    epic_title=$(gh api "repos/$REPO/issues/$epic_num" --jq '.title' 2>/dev/null \
                 || echo "Epic #$epic_num")
    epic_url="https://github.com/$REPO/issues/$epic_num"
    echo "Epic #$epic_num: all features complete. Notifying Slack (epic_closure_approval)."
    notify_slack "epic_closure_approval" \
        "$(jq -n \
               --arg et "$epic_title" \
               --arg su "$epic_url" \
               '{epic_title: $et, summary_url: $su}')"
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
    DISPATCH_HANDLED=true
    exit 1
}

# ─── Utility: get project item ID for an issue from the board data ───────────

get_project_item_id() {
    local issue_num="$1"
    echo "$BOARD_DATA" | jq -r --argjson n "$issue_num" '
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select(.content.number == $n)
        | .id // empty' | head -1
}

# ─── Utility: set any project board status by name ───────────────────────────

set_project_status() {
    local item_id="$1"
    local issue_num="$2"
    local status_name="$3"
    local option_id
    option_id=$(get_status_option_id "$status_name")
    if [[ -z "$STATUS_FIELD_ID" || -z "$option_id" ]]; then
        echo "Warning: Could not resolve Status field/option IDs for '$status_name'; skipping board update for #$issue_num." >&2
        return
    fi
    if $DRY_RUN; then
        echo "[DRY RUN] Would set #$issue_num to \"$status_name\" on project board"
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
      -f optionId="$option_id" >/dev/null \
    || echo "Warning: Board status update failed for #$issue_num to '$status_name'; manual update required." >&2
}

# ─── Utility: post a mid-cycle blocker comment (reuses Blocked Declaration format) ──

post_mid_cycle_blocker() {
    local issue_num="$1"
    local issue_title="$2"
    local failed_command="$3"
    local body
    body="## Blocked Declaration
Type: external
Declared by: $DECLARED_BY
Blocks: #$issue_num — $issue_title
Unblocked when: /$failed_command completes successfully for #$issue_num with exit code 0
Owner: PO (@LauraMardones)
State of in-progress work: execution loop halted after /$failed_command failed; board reflects last successful state"
    echo "MID-CYCLE FAILURE: posting blocker on #$issue_num (/$failed_command failed)"
    post_comment "$issue_num" "$body"
}

# ─── Utility: invoke an executor command via the executor CLI ─────────────────

invoke_executor_command() {
    local slash_command="$1"
    local target_arg="$2"

    local cli="${EXECUTOR_CLI[$EXECUTOR_LABEL]:-}"
    if [[ -z "$cli" ]]; then
        echo "Error: No CLI entry for executor '$EXECUTOR_LABEL' in EXECUTOR_CLI table." >&2
        return 1
    fi

    local api_key_value="${!EXECUTOR_SECRET:-}"
    if [[ -z "$api_key_value" ]]; then
        echo "Error: Secret '$EXECUTOR_SECRET' is not set in the environment." >&2
        return 1
    fi

    local api_key_env="${EXECUTOR_API_KEY_ENV[$EXECUTOR_LABEL]:-ANTHROPIC_API_KEY}"

    if $DRY_RUN; then
        echo "[DRY RUN] Would invoke: ${api_key_env}=<secret> $cli --dangerously-skip-permissions -p \"/$slash_command $target_arg\""
        return 0
    fi

    env "${api_key_env}=${api_key_value}" \
        "$cli" --dangerously-skip-permissions -p "/$slash_command $target_arg"
}

# ─── Utility: find the open PR linked to an issue (Closes #N pattern) ────────

find_linked_pr() {
    local issue_num="$1"
    gh api "repos/$REPO/pulls?state=open&per_page=100" \
        | jq -r --argjson n "$issue_num" '
            map(select(
                (.body // "") | ascii_downcase |
                test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")
            )) | first | .number // empty'
}

# ─── Utility: find next Ready for implementation story (re-fetches board) ────

find_next_ready_story() {
    local board
    board=$(gh api graphql -f query='
    query($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        projectsV2(first: 1) {
          nodes {
            items(first: 100) {
              nodes {
                content { ... on Issue { number title } }
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
    ' -f owner="$OWNER" -f repo="$REPO_NAME") || return 1

    echo "$board" | jq -r '
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select(
            (.content | type) == "object" and
            (.content.number != null) and
            ([.fieldValues.nodes[]
              | select(
                  (.field.name? // "") == "Status" and
                  ((.name? // "") == "Ready for implementation" or (.name? // "") == "Ready")
                )
             ] | length > 0)
          )
        | .content.number' | head -1
}

# ─── Fetch project board data (single query) ──────────────────────────────────

LAST_ACTION="fetching project board"
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

LAST_ACTION="fetching target story #$ISSUE_NUMBER"
echo "Fetching target story #$ISSUE_NUMBER..."
TARGET=$(gh api "repos/$REPO/issues/$ISSUE_NUMBER") || {
    echo "Error: Could not fetch issue #$ISSUE_NUMBER from $REPO." >&2; exit 1
}

TARGET_TITLE=$(echo "$TARGET" | jq -r '.title')
TARGET_BODY=$(echo "$TARGET" | jq -r '.body // ""')
TARGET_LABELS=$(echo "$TARGET" | jq -r '[.labels[].name] | join(" ")')

# ─── Step 2: Executor label routing ──────────────────────────────────────────

LAST_ACTION="executor label routing for #$ISSUE_NUMBER"
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

LAST_ACTION="collecting in-implementation stories"
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

LAST_ACTION="stale story detection"
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

LAST_ACTION="WIP limit check"
echo "Active WIP count: $IN_IMPL_COUNT (limit: $WIP_LIMIT)"

if [[ $IN_IMPL_COUNT -ge $WIP_LIMIT ]]; then
    active_list=$(echo "$IN_IMPL_ITEMS" | jq -r '.[] | "  #\(.number) \(.title)"')
    echo "FAIL: WIP limit reached ($IN_IMPL_COUNT >= $WIP_LIMIT)"
    echo "$active_list"
    unblocked_when="Fewer than $WIP_LIMIT stories are in \"In implementation\" status on the project board"
    post_conflict_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "$unblocked_when"
fi

# ─── Step 6: File overlap check ───────────────────────────────────────────────

LAST_ACTION="file overlap check"
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

# ─── Execution loop ───────────────────────────────────────────────────────────
# Policy source: docs/PROJECT-STATUS.md — board status transitions are facts;
# each status is set only after the preceding executor command succeeds.

TARGET_ITEM_ID=$(get_project_item_id "$ISSUE_NUMBER")
if [[ -z "$TARGET_ITEM_ID" ]]; then
    echo "Warning: Could not find project item ID for #$ISSUE_NUMBER; board updates will be skipped." >&2
fi

# ─── Budget check before /implement (issue #203) ─────────────────────────────
# Check daily token budget before invoking /implement. Skip and log if the cap
# is reached; track counts for all_budget_blocked detection.

BUDGET_TYPE="${EXECUTOR_BUDGET_TYPE[$EXECUTOR_LABEL]:-}"

SIZE_LABEL=""
for _lbl in $TARGET_LABELS; do
    if [[ "$_lbl" == size:* || "$_lbl" == "small" || "$_lbl" == "medium" || "$_lbl" == "large" ]]; then
        SIZE_LABEL="$_lbl"
        break
    fi
done

ESTIMATED_TOKENS=75000
if [[ -f "$BUDGET_SCRIPT" ]]; then
    ESTIMATED_TOKENS=$(bash "$BUDGET_SCRIPT" estimate "${SIZE_LABEL:-medium}" 2>/dev/null || echo 75000)
fi

TOTAL_READY_COUNT=$(( TOTAL_READY_COUNT + 1 ))

if [[ -n "$BUDGET_TYPE" && -f "$BUDGET_SCRIPT" ]]; then
    if $DRY_RUN; then
        echo "[DRY RUN] Would check: dispatcher-budget.sh check $BUDGET_TYPE"
    else
        _budget_rc=0
        bash "$BUDGET_SCRIPT" check "$BUDGET_TYPE" >/dev/null || _budget_rc=$?
        if [[ $_budget_rc -eq 2 ]]; then
            echo "Error: Budget configuration error for executor '$BUDGET_TYPE' (dispatcher-budget.sh exit 2)." >&2
            echo "       Ensure BUDGET_DAILY_$(echo "$BUDGET_TYPE" | tr '[:lower:]' '[:upper:]') is set in the workflow environment." >&2
            DISPATCH_HANDLED=true
            exit 1
        elif [[ $_budget_rc -eq 1 ]]; then
            echo "[BUDGET SKIP] #$ISSUE_NUMBER: $TARGET_TITLE — ${BUDGET_TYPE} daily cap reached; skipping"
            BUDGET_BLOCKED_COUNT=$(( BUDGET_BLOCKED_COUNT + 1 ))
            LAST_ACTION="budget-skip for #$ISSUE_NUMBER; searching for next story"
            NEXT_ISSUE=$(find_next_ready_story || true)
            if [[ -z "$NEXT_ISSUE" ]]; then
                if [[ $BUDGET_BLOCKED_COUNT -eq $TOTAL_READY_COUNT ]]; then
                    [[ -n "${GITHUB_OUTPUT:-}" ]] && echo "all_budget_blocked=true" >> "$GITHUB_OUTPUT"
                fi
                echo "No more \"Ready for implementation\" stories after budget skip. Dispatcher exiting cleanly."
                DISPATCH_HANDLED=true
                exit 0
            fi
            echo "Found next story: #$NEXT_ISSUE — re-running full pre-flight + execution cycle."
            _NEXT_ARGS=(--repo "$REPO" --issue "$NEXT_ISSUE" \
                --budget-blocked-count "$BUDGET_BLOCKED_COUNT" \
                --total-ready-count "$TOTAL_READY_COUNT")
            $DRY_RUN && _NEXT_ARGS+=(--dry-run)
            DISPATCH_HANDLED=true
            exec bash "$0" "${_NEXT_ARGS[@]}"
        fi
    fi
fi

# ─── Step E1: /implement ──────────────────────────────────────────────────────

LAST_ACTION="invoking /implement for #$ISSUE_NUMBER"
echo ""
echo "── E1: /implement #$ISSUE_NUMBER ───────────────────────────────────────"
set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "In implementation"
if ! invoke_executor_command "implement" "$ISSUE_NUMBER"; then
    check_and_notify_decision_blocker "$ISSUE_NUMBER" "$TARGET_TITLE"
    post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "implement"
    DISPATCH_HANDLED=true
    exit 1
fi
echo "✓ /implement completed for #$ISSUE_NUMBER"

# Budget increment after successful /implement (issue #203)
if [[ -n "$BUDGET_TYPE" && -f "$BUDGET_SCRIPT" ]]; then
    if $DRY_RUN; then
        echo "[DRY RUN] Would call: dispatcher-budget.sh increment $BUDGET_TYPE $ESTIMATED_TOKENS"
    else
        echo "[BUDGET] Increment: $BUDGET_TYPE +$ESTIMATED_TOKENS after #$ISSUE_NUMBER"
        bash "$BUDGET_SCRIPT" increment "$BUDGET_TYPE" "$ESTIMATED_TOKENS" || \
            echo "Warning: Budget increment failed for $BUDGET_TYPE; continuing." >&2
    fi
fi

# ─── Step E2: /review ─────────────────────────────────────────────────────────

LAST_ACTION="invoking /review for #$ISSUE_NUMBER"
echo ""
echo "── E2: /review for #$ISSUE_NUMBER ──────────────────────────────────────"
PR_NUMBER=$(find_linked_pr "$ISSUE_NUMBER")
if [[ -z "$PR_NUMBER" ]]; then
    echo "Error: No linked open PR found for #$ISSUE_NUMBER after /implement." >&2
    post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "review"
    DISPATCH_HANDLED=true
    exit 1
fi
echo "Linked PR: #$PR_NUMBER"
set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "In review"
if ! invoke_executor_command "review" "$PR_NUMBER"; then
    check_and_notify_decision_blocker "$ISSUE_NUMBER" "$TARGET_TITLE"
    post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "review"
    DISPATCH_HANDLED=true
    exit 1
fi
echo "✓ /review completed for PR #$PR_NUMBER"

# ─── Step E3: /merge ──────────────────────────────────────────────────────────

LAST_ACTION="invoking /merge for PR #$PR_NUMBER"
echo ""
echo "── E3: /merge PR #$PR_NUMBER ────────────────────────────────────────────"
set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "Ready to merge"
if ! invoke_executor_command "merge" "$PR_NUMBER"; then
    post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "merge"
    DISPATCH_HANDLED=true
    exit 1
fi
echo "✓ /merge completed for PR #$PR_NUMBER"

# ─── Step E4: /cleanup ────────────────────────────────────────────────────────

LAST_ACTION="invoking /cleanup for #$ISSUE_NUMBER"
echo ""
echo "── E4: /cleanup for #$ISSUE_NUMBER ─────────────────────────────────────"
set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "Done"
# Cleanup failure is non-fatal: story is already Done; log and continue to loop.
if ! invoke_executor_command "cleanup" "$ISSUE_NUMBER"; then
    echo "Warning: /cleanup failed for #$ISSUE_NUMBER; story is Done but cleanup did not complete." >&2
fi
echo "✓ Story #$ISSUE_NUMBER completed (Done)."

# ─── Feature/epic closure check (issue #179) ──────────────────────────────────

LAST_ACTION="feature/epic closure check after #$ISSUE_NUMBER Done"
notify_closure_if_complete "$ISSUE_NUMBER" || true

# ─── Loop: find and process the next Ready for implementation story ───────────

LAST_ACTION="searching for next Ready for implementation story"
echo ""
echo "Searching for next \"Ready for implementation\" story..."
NEXT_ISSUE=$(find_next_ready_story || true)

if [[ -z "$NEXT_ISSUE" ]]; then
    # Check all_budget_blocked: every story that reached the budget check was skipped.
    if [[ $TOTAL_READY_COUNT -gt 0 && $BUDGET_BLOCKED_COUNT -eq $TOTAL_READY_COUNT ]]; then
        [[ -n "${GITHUB_OUTPUT:-}" ]] && echo "all_budget_blocked=true" >> "$GITHUB_OUTPUT"
    fi
    echo "No more \"Ready for implementation\" stories. Dispatcher exiting cleanly."
    DISPATCH_HANDLED=true
    exit 0
fi

echo "Found next story: #$NEXT_ISSUE — re-running full pre-flight + execution cycle."

_NEXT_ARGS=(--repo "$REPO" --issue "$NEXT_ISSUE" \
    --budget-blocked-count "$BUDGET_BLOCKED_COUNT" \
    --total-ready-count "$TOTAL_READY_COUNT")
$DRY_RUN && _NEXT_ARGS+=(--dry-run)

if $DRY_RUN; then
    echo "[DRY RUN] Would exec: bash $0 ${_NEXT_ARGS[*]}"
    DISPATCH_HANDLED=true
    exit 0
fi

DISPATCH_HANDLED=true
exec bash "$0" "${_NEXT_ARGS[@]}"
