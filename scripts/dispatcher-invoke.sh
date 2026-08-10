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
#             issue #254 (direct Anthropic/OpenAI API invocation — no CLI, no install step)
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
# Requirements: bash 4+, gh (GitHub CLI), jq, curl
#
# ─── Executor routing table ───────────────────────────────────────────────────
#
# Add a new executor tier by adding a row here and creating the corresponding
# GitHub Secret. Do not add executor-specific conditional logic elsewhere
# (OSS compatibility invariant: routing is data-driven, not logic-driven).
#
# | executor: label             | Executor type  | GitHub Secret name         | Provider   | API model ID               |
# |-----------------------------|----------------|----------------------------|------------|-----------------------------|
# | executor:claude-code-haiku  | Claude Haiku   | ANTHROPIC_API_KEY          | anthropic  | claude-haiku-4-5-20251001  |
# | executor:claude-code-sonnet | Claude Sonnet  | ANTHROPIC_API_KEY          | anthropic  | claude-sonnet-5             |
# | executor:claude-code-opus   | Claude Opus    | ANTHROPIC_API_KEY          | anthropic  | claude-opus-5               |
# | executor:codex              | Codex          | OPENAI_API_KEY_CODEX       | openai     | gpt-5-codex                 |
#
# All three Claude tiers share one GitHub Secret (ANTHROPIC_API_KEY): a single
# Anthropic API key authenticates calls to any Claude model, so per-tier
# secrets (formerly ANTHROPIC_API_KEY_HAIKU/_SONNET/_OPUS) were redundant
# credential-provisioning overhead with no functional benefit (issue #252).
# Per-tier daily token *budgets* remain separate (see EXECUTOR_BUDGET_TYPE
# below and dispatcher-budget.sh) — that mechanism is independent of which
# secret authenticates the call.
#
# ─── Executor invocation mechanism (issue #254) ───────────────────────────────
#
# invoke_executor_command() calls the Anthropic Messages API (Claude tiers) or
# the OpenAI Chat Completions API (Codex tier) directly via curl — there is no
# CLI binary (`claude`/`codex`) and no runner install step, per ADR-002's
# documented architecture ("invokes executors ... via API calls"). Each call
# drives a bounded tool-use loop (run_anthropic_agent / run_openai_agent) that
# gives the model a single `bash` tool scoped to this repository checkout; the
# task prompt is the corresponding .claude/commands/<slash_command>.md file
# with $ARGUMENTS substituted, so the API-invoked agent follows the exact same
# instructions a manually-run command would. The loop ends when the model
# responds without requesting another tool call, or fails closed (non-zero
# return, no Blocked Declaration posted by the caller of invoke_executor_command
# happens one level up) on an HTTP error, a malformed API response, or hitting
# the per-invocation turn cap (AGENT_MAX_TURNS).
#
# Authentication:
#   GH_TOKEN (or GITHUB_TOKEN) — GitHub personal access token or Actions token.
#   In GitHub Actions, the workflow sets GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}.
#   PROJECT_TOKEN must be a classic PAT with repo (full) + project (full) scopes;
#   project (full) is required because the dispatcher writes board status via
#   updateProjectV2ItemFieldValue — read:project alone is not sufficient.
#   ANTHROPIC_API_KEY and OPENAI_API_KEY_CODEX must be set as GitHub Secrets
#   and wired into .github/workflows/dispatcher.yml's env: block for the
#   "Run dispatcher invoke" step (or the job-level env: block) — GitHub
#   Actions never auto-injects a secret; it must be explicitly referenced as
#   ${{ secrets.NAME }} in the workflow YAML or the script never sees it.
#   Do not hardcode any token or API key in this script. Both secrets
#   authenticate direct API calls only (issue #254) — neither is passed to a
#   locally-installed CLI, because none is installed or required.
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

# ─── Job-level wall-clock deadline (ADR-003) — survives recursive exec ────────
# One story's cycle calls invoke_executor_command() up to four times
# (/implement, /review, /merge, /cleanup), and the tail-call
# `exec bash "$0" ...` used to move to the next ready story replaces this
# process without starting a new Actions job — so all of that shares one
# 6-hour job ceiling. DISPATCH_JOB_START_TS must therefore be set ONCE, at the
# first invocation of this script for the job, and must NOT reset on
# subsequent phases or recursive exec calls. The idempotent-default form below
# is what makes that true: exec inherits the current process's exported
# environment, so on every invocation after the first, DISPATCH_JOB_START_TS
# is already set and this line is a no-op. Do not change this to an
# unconditional assignment — that would silently reintroduce a per-phase
# budget instead of a job-wide one.
DISPATCH_JOB_START_TS="${DISPATCH_JOB_START_TS:-$(date +%s)}"
export DISPATCH_JOB_START_TS

# ─── Executor routing table (data-driven) ─────────────────────────────────────
# Format: ["<label-suffix>"]="<DisplayName>:<SECRET_NAME>"
# The three Claude tiers share one GitHub Secret (ANTHROPIC_API_KEY) — see
# header comment above (issue #252).
declare -A EXECUTOR_ROUTING=(
    ["claude-code-haiku"]="Claude Haiku:ANTHROPIC_API_KEY"
    ["claude-code-sonnet"]="Claude Sonnet:ANTHROPIC_API_KEY"
    ["claude-code-opus"]="Claude Opus:ANTHROPIC_API_KEY"
    ["codex"]="Codex:OPENAI_API_KEY_CODEX"
)

# ─── Executor provider + model routing table (data-driven, issue #254) ────────
# Maps executor label suffix to the provider whose API invoke_executor_command()
# calls directly (no CLI binary, no install step) and the model ID sent in that
# call. Add a new row to both tables when adding a new executor tier (OSS
# invariant: data-driven, not logic-driven — do not branch on EXECUTOR_LABEL
# anywhere else in this script).
declare -A EXECUTOR_PROVIDER=(
    ["claude-code-haiku"]="anthropic"
    ["claude-code-sonnet"]="anthropic"
    ["claude-code-opus"]="anthropic"
    ["codex"]="openai"
)

declare -A EXECUTOR_MODEL=(
    ["claude-code-haiku"]="claude-haiku-4-5-20251001"
    ["claude-code-sonnet"]="claude-sonnet-5"
    ["claude-code-opus"]="claude-opus-5"
    ["codex"]="gpt-5-codex"
)

# ─── Executor API key env-var table (data-driven) ─────────────────────────────
# Historically named the env-var a locally-installed CLI expected for auth.
# That CLI-invocation path was removed by issue #254 in favor of direct
# provider API calls (see EXECUTOR_PROVIDER/EXECUTOR_MODEL above), so this
# table is not read by invoke_executor_command() today — kept as-is per issue
# #254's scope decision (unaffected by this change) and left available for any
# future consumer that still needs the CLI-style env-var name per tier.
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
SKIPPED_ISSUES=""  # space-separated issue numbers skipped due to budget cap

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
        --skipped-issues)
            [[ $# -ge 2 ]] || { echo "Error: --skipped-issues requires a value." >&2; exit 1; }
            SKIPPED_ISSUES="$2"; shift 2 ;;
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

for cmd in gh jq curl; do
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
COMMANDS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/.claude/commands"

# ─── Executor agent loop tuning (issue #254 / ADR-003) — overridable for tests ─
# AGENT_MAX_WALLCLOCK_SECONDS is the enforced safety bound, checked inside the
# loop on every turn. AGENT_MAX_TURNS x (AGENT_API_TIMEOUT + AGENT_TOOL_TIMEOUT)
# alone is NOT a safe bound — at the defaults below that product is 60 x 900s =
# 54000s (15h), which exceeds GitHub Actions' 6h job ceiling. Relying on the
# platform to force-kill an over-budget job is not acceptable: an abrupt kill
# can bypass the loop's own non-zero return and the caller's Blocked
# Declaration. AGENT_MAX_WALLCLOCK_SECONDS makes the script fail closed through
# its own normal exit path, comfortably before that platform kill could ever
# fire (see ADR-003).
AGENT_MAX_TURNS="${AGENT_MAX_TURNS:-60}"          # tool-use round-trips before failing closed
AGENT_MAX_TOKENS="${AGENT_MAX_TOKENS:-8192}"      # max_tokens requested per API turn
AGENT_TOOL_TIMEOUT="${AGENT_TOOL_TIMEOUT:-300}"   # seconds a single bash-tool command may run
AGENT_API_TIMEOUT="${AGENT_API_TIMEOUT:-600}"     # seconds a single API call may take
AGENT_MAX_WALLCLOCK_SECONDS="${AGENT_MAX_WALLCLOCK_SECONDS:-10800}"  # 3h total budget per invoke_executor_command() call — enforced, not incidental
ANTHROPIC_API_URL="${ANTHROPIC_API_URL:-https://api.anthropic.com/v1/messages}"
OPENAI_API_URL="${OPENAI_API_URL:-https://api.openai.com/v1/chat/completions}"

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

# ─── Utility: POST JSON to a URL, fail closed on transport/HTTP error ────────
# The request body is written to a temp file and sent via --data @<file>,
# never as a raw command-line argument — a large multi-turn conversation
# payload would otherwise risk the same "Argument list too long" class of
# failure fixed for board-data pagination in issue #251. Returns the response
# body on stdout; a curl transport failure or a non-2xx HTTP status both
# return non-zero with a clear error on stderr (the response body, which for
# both providers is a JSON error object, is included for diagnosis).

_curl_json() {
    local url="$1" body="$2"; shift 2
    local -a headers=("$@")
    local tmp_req tmp_body http_code rc=0
    tmp_req="$(mktemp)"
    tmp_body="$(mktemp)"
    printf '%s' "$body" > "$tmp_req"

    http_code=$(curl -sS --max-time "$AGENT_API_TIMEOUT" \
        -o "$tmp_body" -w '%{http_code}' \
        -X POST "$url" "${headers[@]}" --data @"$tmp_req") || rc=$?
    rm -f "$tmp_req"

    if [[ $rc -ne 0 ]]; then
        echo "Error: request to $url failed (curl exit $rc — network error or timeout)." >&2
        rm -f "$tmp_body"
        return 1
    fi
    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        echo "Error: API request to $url failed with HTTP $http_code:" >&2
        cat "$tmp_body" >&2
        rm -f "$tmp_body"
        return 1
    fi
    cat "$tmp_body"
    rm -f "$tmp_body"
}

# ─── Utility: execute a single bash-tool command inside the repo checkout ────
# Combined stdout+stderr, capped to bound context growth across agent turns,
# and time-boxed (AGENT_TOOL_TIMEOUT) so a hung command fails the tool call
# instead of hanging the job. Results are returned via AGENT_TOOL_LAST_OUTPUT /
# AGENT_TOOL_LAST_RC (the caller runs synchronously in the same shell, not a
# subshell, so these are visible immediately after the call returns).

_run_agent_bash_tool() {
    local cmd="$1" out rc=0
    out=$(timeout "$AGENT_TOOL_TIMEOUT" bash -c "$cmd" 2>&1) || rc=$?
    if [[ ${#out} -gt 16000 ]]; then
        out="${out:0:16000}
... [output truncated: ${#out} bytes total, showing first 16000]"
    elif [[ -z "$out" && $rc -ne 0 ]]; then
        # A tool_result with is_error=true and empty content is rejected by
        # the Anthropic API (HTTP 400) — confirmed live: a command killed by
        # `timeout` after producing zero output hit exactly this. Every
        # error path must give the model something to react to.
        if [[ $rc -eq 124 ]]; then
            out="(command timed out after ${AGENT_TOOL_TIMEOUT}s with no output)"
        else
            out="(command exited with status $rc and produced no output)"
        fi
    fi
    AGENT_TOOL_LAST_OUTPUT="$out"
    AGENT_TOOL_LAST_RC="$rc"
}

# ─── Utility: bounded tool-use loop against the Anthropic Messages API ───────
# Drives the model through repeated bash-tool turns until it responds without
# requesting a further tool call (task complete) or AGENT_MAX_TURNS is hit
# (fails closed). Returns 0 with the model's final message on stdout, or
# non-zero on any transport/HTTP error, malformed response, or turn-cap.

run_anthropic_agent() {
    local model="$1" api_key="$2" task_prompt="$3"
    local system='You are an autonomous software delivery agent running inside a GitHub Actions checkout of this repository. You have exactly one tool, "bash", which runs a shell command in the repository working directory and returns its combined stdout/stderr. Use it to read files, edit files, run tests, and drive git and the gh CLI (already authenticated via GH_TOKEN) to commit, push, and open or update pull requests, following the instructions below exactly. When the task is completely finished, reply with a final plain-text message and do not request any further tool call.'
    local tools='[{"name":"bash","description":"Execute a bash command in the repository working directory. Returns combined stdout+stderr.","input_schema":{"type":"object","properties":{"command":{"type":"string","description":"The shell command to run."}},"required":["command"]}}]'
    local messages
    messages=$(jq -n --arg t "$task_prompt" '[{role:"user", content:$t}]')
    local -a headers=(-H "x-api-key: $api_key" -H "anthropic-version: 2023-06-01" -H "content-type: application/json")

    local turn=0 response stop_reason assistant_content
    local elapsed
    while (( turn < AGENT_MAX_TURNS )); do
        elapsed=$(( $(date +%s) - DISPATCH_JOB_START_TS ))
        if (( elapsed >= AGENT_MAX_WALLCLOCK_SECONDS )); then
            echo "Error: agent exceeded AGENT_MAX_WALLCLOCK_SECONDS (${AGENT_MAX_WALLCLOCK_SECONDS}s) since job start (elapsed ${elapsed}s)." >&2
            return 1
        fi
        turn=$(( turn + 1 ))
        local request tmp_messages
        # $messages grows every turn (tool outputs, file contents, PR bodies
        # this session wrote) and is NOT safe to pass via --argjson on the
        # command line — confirmed live (run 31425632706, PR #255, head
        # d845ece): by turn 17 it exceeded the OS argument-length limit
        # ("jq: Argument list too long"), and because this call sits inside a
        # function invoked as an `if` condition, `set -e` did not catch the
        # failure — $request was silently left empty and posted as an empty
        # body, surfacing as a confusing "invalid JSON" error from the API
        # instead of a clear one here. Same root cause already fixed once in
        # this codebase for board-data pagination (issue #251); --slurpfile
        # reads the large value from disk instead of argv, with no such limit.
        tmp_messages="$(mktemp)"
        printf '%s' "$messages" > "$tmp_messages"
        local jq_rc=0
        request=$(jq -n --arg model "$model" --arg system "$system" \
            --argjson max_tokens "$AGENT_MAX_TOKENS" \
            --slurpfile messages_wrap "$tmp_messages" --argjson tools "$tools" \
            '{model:$model, max_tokens:$max_tokens, system:$system, messages:$messages_wrap[0], tools:$tools}') || jq_rc=$?
        rm -f "$tmp_messages"
        if [[ $jq_rc -ne 0 || -z "$request" ]]; then
            echo "Error: failed to build the Anthropic API request (jq exit $jq_rc)." >&2
            return 1
        fi

        response=$(_curl_json "$ANTHROPIC_API_URL" "$request" "${headers[@]}") || return 1

        echo "$response" | jq -e '.content | type == "array"' >/dev/null 2>&1 || {
            echo "Error: malformed Anthropic API response (no .content array)." >&2
            echo "$response" >&2
            return 1
        }

        stop_reason=$(echo "$response" | jq -r '.stop_reason // empty')
        assistant_content=$(echo "$response" | jq -c '.content')
        messages=$(echo "$messages" | jq -c --argjson c "$assistant_content" '. + [{role:"assistant", content:$c}]')
        echo "[agent turn $turn/$AGENT_MAX_TURNS] stop_reason=$stop_reason" >&2

        if [[ "$stop_reason" == "end_turn" || "$stop_reason" == "stop_sequence" ]]; then
            echo "$response" | jq -r '[.content[] | select(.type=="text") | .text] | join("\n")'
            return 0
        fi

        if [[ "$stop_reason" != "tool_use" ]]; then
            # Confirmed live (run 31425063548, PR #255): stop_reason=max_tokens
            # means the response was cut off mid-generation, not that the task
            # finished — treating it as success previously caused /implement
            # to report "completed" with no commit and no PR ever created.
            # Any stop_reason that isn't a genuine completion or a tool
            # request fails closed instead of being assumed to mean success.
            echo "Error: agent stopped with stop_reason='$stop_reason' (not a genuine completion — response may be truncated); not treating as success." >&2
            return 1
        fi

        local tool_results='[]' block tool_id tool_cmd
        while IFS= read -r block; do
            [[ -z "$block" ]] && continue
            tool_id=$(echo "$block" | jq -r '.id')
            tool_cmd=$(echo "$block" | jq -r '.input.command // empty')
            echo "[agent bash] $tool_cmd" >&2
            _run_agent_bash_tool "$tool_cmd"
            tool_results=$(echo "$tool_results" | jq -c \
                --arg id "$tool_id" --arg out "$AGENT_TOOL_LAST_OUTPUT" --argjson rc "$AGENT_TOOL_LAST_RC" \
                '. + [{type:"tool_result", tool_use_id:$id, content:$out, is_error:($rc != 0)}]')
        done < <(echo "$response" | jq -c '.content[] | select(.type=="tool_use")')

        if [[ "$(echo "$tool_results" | jq 'length')" -eq 0 ]]; then
            echo "Error: Anthropic response had stop_reason=tool_use but no tool_use content blocks." >&2
            return 1
        fi

        messages=$(echo "$messages" | jq -c --argjson tr "$tool_results" '. + [{role:"user", content:$tr}]')
    done

    echo "Error: agent exceeded AGENT_MAX_TURNS ($AGENT_MAX_TURNS) without completing the task." >&2
    return 1
}

# ─── Utility: bounded tool-use loop against the OpenAI Chat Completions API ──
# Same contract as run_anthropic_agent, adapted to OpenAI's function-calling
# message/tool_call shape (Codex tier).

run_openai_agent() {
    local model="$1" api_key="$2" task_prompt="$3"
    local system='You are an autonomous software delivery agent running inside a GitHub Actions checkout of this repository. You have exactly one tool, "bash", which runs a shell command in the repository working directory and returns its combined stdout/stderr. Use it to read files, edit files, run tests, and drive git and the gh CLI (already authenticated via GH_TOKEN) to commit, push, and open or update pull requests, following the instructions below exactly. When the task is completely finished, reply with a final plain-text message and do not request any further tool call.'
    local tools='[{"type":"function","function":{"name":"bash","description":"Execute a bash command in the repository working directory. Returns combined stdout+stderr.","parameters":{"type":"object","properties":{"command":{"type":"string","description":"The shell command to run."}},"required":["command"]}}}]'
    local messages
    messages=$(jq -n --arg s "$system" --arg t "$task_prompt" \
        '[{role:"system", content:$s}, {role:"user", content:$t}]')
    local -a headers=(-H "Authorization: Bearer $api_key" -H "content-type: application/json")

    local turn=0 response finish_reason assistant_message has_tool_calls
    local elapsed
    while (( turn < AGENT_MAX_TURNS )); do
        elapsed=$(( $(date +%s) - DISPATCH_JOB_START_TS ))
        if (( elapsed >= AGENT_MAX_WALLCLOCK_SECONDS )); then
            echo "Error: agent exceeded AGENT_MAX_WALLCLOCK_SECONDS (${AGENT_MAX_WALLCLOCK_SECONDS}s) since job start (elapsed ${elapsed}s)." >&2
            return 1
        fi
        turn=$(( turn + 1 ))
        local request tmp_messages
        # See the matching comment in run_anthropic_agent() — $messages must
        # never be passed via --argjson on the command line; it grows every
        # turn and this exact failure mode (jq "Argument list too long",
        # silently swallowed because set -e doesn't reach here) was confirmed
        # live on run 31425632706 (PR #255, head d845ece).
        tmp_messages="$(mktemp)"
        printf '%s' "$messages" > "$tmp_messages"
        local jq_rc=0
        request=$(jq -n --arg model "$model" \
            --argjson max_tokens "$AGENT_MAX_TOKENS" \
            --slurpfile messages_wrap "$tmp_messages" --argjson tools "$tools" \
            '{model:$model, max_tokens:$max_tokens, messages:$messages_wrap[0], tools:$tools}') || jq_rc=$?
        rm -f "$tmp_messages"
        if [[ $jq_rc -ne 0 || -z "$request" ]]; then
            echo "Error: failed to build the OpenAI API request (jq exit $jq_rc)." >&2
            return 1
        fi

        response=$(_curl_json "$OPENAI_API_URL" "$request" "${headers[@]}") || return 1

        echo "$response" | jq -e '.choices[0].message' >/dev/null 2>&1 || {
            echo "Error: malformed OpenAI API response (no .choices[0].message)." >&2
            echo "$response" >&2
            return 1
        }

        finish_reason=$(echo "$response" | jq -r '.choices[0].finish_reason // empty')
        assistant_message=$(echo "$response" | jq -c '.choices[0].message')
        messages=$(echo "$messages" | jq -c --argjson m "$assistant_message" '. + [$m]')
        echo "[agent turn $turn/$AGENT_MAX_TURNS] finish_reason=$finish_reason" >&2

        has_tool_calls=$(echo "$assistant_message" | jq -r '(.tool_calls // []) | length')

        if [[ "$finish_reason" == "stop" ]]; then
            echo "$assistant_message" | jq -r '.content // empty'
            return 0
        fi

        if [[ "$finish_reason" != "tool_calls" || "$has_tool_calls" -eq 0 ]]; then
            # Mirrors the Anthropic fix: finish_reason="length" (OpenAI's
            # max_tokens truncation) or anything else that isn't a genuine
            # "stop" or a tool request must not be read as task completion.
            echo "Error: agent stopped with finish_reason='$finish_reason' (not a genuine completion — response may be truncated); not treating as success." >&2
            return 1
        fi

        local call call_id fn_args tool_cmd tool_msg
        while IFS= read -r call; do
            [[ -z "$call" ]] && continue
            call_id=$(echo "$call" | jq -r '.id')
            fn_args=$(echo "$call" | jq -r '.function.arguments // "{}"')
            tool_cmd=$(echo "$fn_args" | jq -r '.command // empty' 2>/dev/null || echo "")
            echo "[agent bash] $tool_cmd" >&2
            _run_agent_bash_tool "$tool_cmd"
            tool_msg=$(jq -n --arg id "$call_id" --arg out "$AGENT_TOOL_LAST_OUTPUT" \
                '{role:"tool", tool_call_id:$id, content:$out}')
            messages=$(echo "$messages" | jq -c --argjson m "$tool_msg" '. + [$m]')
        done < <(echo "$assistant_message" | jq -c '.tool_calls[]')
    done

    echo "Error: agent exceeded AGENT_MAX_TURNS ($AGENT_MAX_TURNS) without completing the task." >&2
    return 1
}

# ─── Utility: invoke an executor command via a direct provider API call ──────
# Issue #254: no CLI binary, no install step. Resolves provider/model from the
# data-driven tables above, loads the corresponding .claude/commands/*.md file
# as the task prompt — the same instructions a manually-run command follows —
# substitutes $ARGUMENTS, and drives a bounded tool-use loop against that
# provider's API (run_anthropic_agent / run_openai_agent).

invoke_executor_command() {
    local slash_command="$1"
    local target_arg="$2"

    local api_key_value="${!EXECUTOR_SECRET:-}"
    if [[ -z "$api_key_value" ]]; then
        echo "Error: Secret '$EXECUTOR_SECRET' is not set in the environment." >&2
        return 1
    fi

    local provider="${EXECUTOR_PROVIDER[$EXECUTOR_LABEL]:-}"
    local model="${EXECUTOR_MODEL[$EXECUTOR_LABEL]:-}"
    if [[ -z "$provider" || -z "$model" ]]; then
        echo "Error: No provider/model entry for executor '$EXECUTOR_LABEL' in EXECUTOR_PROVIDER/EXECUTOR_MODEL." >&2
        return 1
    fi

    local command_file="$COMMANDS_DIR/${slash_command}.md"
    if [[ ! -f "$command_file" ]]; then
        echo "Error: Command definition not found: $command_file" >&2
        return 1
    fi

    local task_prompt
    task_prompt=$(sed "s/\$ARGUMENTS/$target_arg/g" "$command_file")

    if $DRY_RUN; then
        echo "[DRY RUN] Would invoke $provider ($model) directly via API for /$slash_command $target_arg — no CLI, no install step"
        return 0
    fi

    case "$provider" in
        anthropic)
            run_anthropic_agent "$model" "$api_key_value" "$task_prompt"
            ;;
        openai)
            run_openai_agent "$model" "$api_key_value" "$task_prompt"
            ;;
        *)
            echo "Error: Unknown provider '$provider' for executor '$EXECUTOR_LABEL'." >&2
            return 1
            ;;
    esac
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

# ─── Utility: paginated project board fetch (D1 fix) ──────────────────────────
# Walks every page of project items via cursor pagination (capped at 50 pages /
# 5000 items as a runaway-loop safety valve) instead of the previous hard cap
# of the first 100 items. Returns a BOARD_DATA-shaped JSON object:
#   {data:{repository:{projectsV2:{nodes:[{id, fields:{nodes:[...]}, items:{nodes:[...]}}]}}}}
# Project id and field definitions are captured from the first page only
# (they do not vary per page).

fetch_board_data() {
    local cursor="" page_count=0 max_pages=50
    local project_id="" page has_next
    # Items accumulate on disk, not in a shell variable passed via --argjson:
    # past ~100 project items (each carrying a full issue body) the growing
    # JSON blob exceeds the OS argument-length limit ("Argument list too
    # long"). jq reads file contents directly, which has no such limit.
    local tmp_dir items_file fields_file page_file
    tmp_dir="$(mktemp -d)"
    items_file="$tmp_dir/items.json"
    fields_file="$tmp_dir/fields.json"
    echo '[]' > "$items_file"
    echo '[]' > "$fields_file"

    while :; do
        page_count=$(( page_count + 1 ))
        if [[ "$page_count" -gt "$max_pages" ]]; then
            echo "Warning: hit ${max_pages}-page pagination cap ($((max_pages * 100)) items); board results may be incomplete." >&2
            break
        fi

        if [[ -z "$cursor" ]]; then
            page=$(gh api graphql -f query='
            query($owner: String!, $repo: String!) {
              repository(owner: $owner, name: $repo) {
                projectsV2(first: 1) {
                  nodes {
                    id
                    fields(first: 30) {
                      nodes {
                        ... on ProjectV2SingleSelectField { id name options { id name } }
                      }
                    }
                    items(first: 100) {
                      pageInfo { hasNextPage endCursor }
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
            ' -f owner="$OWNER" -f repo="$REPO_NAME") || { rm -rf "$tmp_dir"; return 1; }
        else
            page=$(gh api graphql -f query='
            query($owner: String!, $repo: String!, $after: String) {
              repository(owner: $owner, name: $repo) {
                projectsV2(first: 1) {
                  nodes {
                    id
                    fields(first: 30) {
                      nodes {
                        ... on ProjectV2SingleSelectField { id name options { id name } }
                      }
                    }
                    items(first: 100, after: $after) {
                      pageInfo { hasNextPage endCursor }
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
            ' -f owner="$OWNER" -f repo="$REPO_NAME" -f after="$cursor") || { rm -rf "$tmp_dir"; return 1; }
        fi

        if [[ "$page_count" -eq 1 ]]; then
            project_id=$(echo "$page" | jq -r '.data.repository.projectsV2.nodes[0].id // empty')
            echo "$page" | jq -c '.data.repository.projectsV2.nodes[0].fields.nodes' > "$fields_file"
        fi

        page_file="$tmp_dir/page_${page_count}.json"
        echo "$page" | jq '.data.repository.projectsV2.nodes[0].items.nodes' > "$page_file"
        jq -s 'add' "$items_file" "$page_file" > "$tmp_dir/items_new.json"
        mv "$tmp_dir/items_new.json" "$items_file"
        rm -f "$page_file"

        has_next=$(echo "$page" | jq -r '.data.repository.projectsV2.nodes[0].items.pageInfo.hasNextPage')
        cursor=$(echo "$page" | jq -r '.data.repository.projectsV2.nodes[0].items.pageInfo.endCursor')

        [[ "$has_next" == "true" ]] || break
    done

    jq -n --arg pid "$project_id" --slurpfile fields "$fields_file" --slurpfile items "$items_file" \
        '{data:{repository:{projectsV2:{nodes:[{id:$pid, fields:{nodes:$fields[0]}, items:{nodes:$items[0]}}]}}}}'
    rm -rf "$tmp_dir"
}

# ─── Utility: find next Ready for implementation story (re-fetches board) ────

find_next_ready_story() {
    # Optional first arg: space-separated issue numbers to exclude (already budget-skipped)
    local _fnrs_exclude="${1:-}"
    local board
    board=$(fetch_board_data) || return 1

    local _all_ready
    _all_ready=$(echo "$board" | jq -r '
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
        | .content.number') || return 1

    if [[ -z "$_fnrs_exclude" ]]; then
        echo "$_all_ready" | head -1
        return 0
    fi

    # Return first Ready story not in the exclusion list
    local _num _skip _excl
    while IFS= read -r _num; do
        [[ -z "$_num" ]] && continue
        _skip=false
        for _excl in $_fnrs_exclude; do
            [[ "$_num" == "$_excl" ]] && { _skip=true; break; }
        done
        if ! $_skip; then echo "$_num"; return 0; fi
    done <<< "$_all_ready"
    return 0  # empty output: all Ready stories are excluded
}

# ─── Fetch project board data (paginated) ──────────────────────────────────────

LAST_ACTION="fetching project board"
echo "Fetching project board..."
BOARD_DATA=$(fetch_board_data) || {
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
    BOARD_DATA=$(fetch_board_data)

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
            # Track this issue as skipped so find_next_ready_story won't reselect it
            SKIPPED_ISSUES="${SKIPPED_ISSUES:+$SKIPPED_ISSUES }$ISSUE_NUMBER"
            LAST_ACTION="budget-skip for #$ISSUE_NUMBER; searching for next story"
            NEXT_ISSUE=$(find_next_ready_story "$SKIPPED_ISSUES" || true)
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
                --total-ready-count "$TOTAL_READY_COUNT" \
                --skipped-issues "$SKIPPED_ISSUES")
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
