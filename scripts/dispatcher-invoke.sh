#!/usr/bin/env bash
# scripts/dispatcher-invoke.sh
#
# Dispatcher pre-flight gate and execution loop — executor routing, WIP check,
# stale detection, comment-triggered decision-blocker resume, file-overlap
# check, conflict blocker, and story-cycle invocation with board status
# transitions. After each story completes, is handed off or is blocked, the
# loop finds the next "Ready for implementation" story and repeats the full
# pre-flight + cycle.
#
# The cycle depends on how the story is reviewed (issue #293, ADR-006):
#   - Codex review loop (the Claude tiers): /implement, then the story is
#     handed to the Codex review loop and its run ends. A later run resumes it
#     from GitHub facts — at /merge → /cleanup when it is "In merge", or at
#     /implement on the same PR when a fix round is due.
#   - executor:codex: not started for /implement (no subscription mechanism,
#     ADR-012; issue #315). A PR that Codex implemented outside the dispatcher
#     is reviewed by a resume entry: /review under claude-code-opus, then the
#     script-only /merge and /cleanup when the story is "In merge".
# Every run handles resumable work first: stories "In merge", then due fix
# rounds, then pending hand-offs, then Codex-implemented PRs "In review", then
# new work. See docs/DISPATCHER-CONFIG.md → "Codex review loop
# hand-off and resume".
# Implements: issue #171 (pre-flight gate), issue #172 (execution loop, Feature #162),
#             issue #179 (Slack notification wiring — decision_blocker, dispatcher_error,
#                         feature_closure_confirmation, epic_closure_approval)
#             issue #254 (direct provider API invocation; replaced by #314 for
#                         the Claude tiers and removed by #315)
#             issue #262 (comment-triggered resume for decision blockers, alongside the
#                         existing time-based stale-recovery loop — see Step 4 below)
#             issue #263 (cross-provider /review pairing — no new executor: label)
#             issue #256 (non-executable ready items — Epic, Feature, missing or
#                         unsupported type — are skipped with a warning, not fatal)
#             issue #317 (subscription allowance check before each session start:
#                         scripts/dispatcher-allowance.sh, called at the top of
#                         invoke_executor_command(); see docs/DISPATCHER-CONFIG.md)
#             issue #264 (/merge and /cleanup are scripts, not model sessions:
#                         scripts/dispatcher-merge.sh and dispatcher-cleanup.sh;
#                         no allowance check, no executor budget)
#             issue #293 (hand-off to the Codex review loop after /implement, and
#                         the two resume entries: "In merge" and fix rounds)
#             issue #314 (every Claude session runs Claude Code on the runner,
#                         signed in with the PO's subscription token; ADR-012)
#             issue #315 (the metered API path is removed; executor:codex is
#                         skipped at implementation; a Codex-implemented PR
#                         "In review" is reviewed on the subscription)
# Policy source of truth: docs/PROJECT-STATUS.md
#
# Usage:
#   GH_TOKEN=<token> bash scripts/dispatcher-invoke.sh \
#       --repo <owner/repo> --issue <issue-number> [--dry-run]
#   GH_TOKEN=<token> bash scripts/dispatcher-invoke.sh \
#       --repo <owner/repo> --resume [--dry-run]
#
# --issue handles resumable work first, then the given story. --resume handles
# only resumable work, for a run in which no story is ready for implementation.
#
# Environment:
#   CODEX_FIX_ROUND_MAX — most fix rounds the dispatcher starts per PR
#                         (whole number of one or more; default 3).
#
# Exit codes:
#   0  — story completed (Done), story handed to the Codex review loop, a
#        resume entry completed or was skipped by a bound, or nothing to do
#        (including when every remaining ready item is non-executable, issue #256)
#   1  — pre-flight check failed or a session failed; blocker posted on issue.
#        Also a missing "In review" / "In merge" board option or an invalid
#        CODEX_FIX_ROUND_MAX, with no blocker.
#
# Requirements: bash 4+, gh (GitHub CLI), jq, curl, timeout (coreutils), and
#   Claude Code (`claude`, installed and pinned by .github/workflows/dispatcher.yml)
#
# ─── Executor routing table ───────────────────────────────────────────────────
#
# Add a new executor tier by adding a row here and creating the corresponding
# GitHub Secret. Do not add executor-specific conditional logic elsewhere
# (OSS compatibility invariant: routing is data-driven, not logic-driven).
#
# | executor: label             | Executor type  | GitHub Secret name         | Provider    | Model        |
# |-----------------------------|----------------|----------------------------|-------------|--------------|
# | executor:claude-code-haiku  | Claude Haiku   | CLAUDE_CODE_OAUTH_TOKEN    | claude-code | haiku        |
# | executor:claude-code-sonnet | Claude Sonnet  | CLAUDE_CODE_OAUTH_TOKEN    | claude-code | sonnet       |
# | executor:claude-code-opus   | Claude Opus    | CLAUDE_CODE_OAUTH_TOKEN    | claude-code | opus         |
# | executor:codex              | Codex          | none                       | none        | none         |
#
# executor:codex is a known label with no provider row: the dispatcher does
# not start it for /implement (it logs a warning and skips the story), but it
# reviews a Codex-implemented PR (see the review path below).
#
# All three Claude tiers run Claude Code on the runner, signed in with one
# subscription token (CLAUDE_CODE_OAUTH_TOKEN; ADR-012, issue #314). Their
# model is a Claude Code alias: the Claude Code version the workflow pins
# decides which model each alias means, so a model change is a deliberate
# version bump. How many sessions may start per day is a separate limit the
# PO sets (dispatcher-usage-limit.sh, issue #320), the same for every tier.
#
# ─── Review path (issue #263, issue #293, ADR-006) ────────────────────────────
#
# The table above (and the `executor:` label on a story) governs /implement
# only. How the story is reviewed is read from two tables below, keyed by the
# implementing executor:
#
# | /implement executor:                | Review                                 | Table                     |
# |-------------------------------------|----------------------------------------|---------------------------|
# | claude-code-haiku / -sonnet / -opus | Codex Cloud, through the review loop   | CODEX_REVIEW_LOOP_ROUTING |
# | codex                               | /review session under claude-code-opus | REVIEW_EXECUTOR_ROUTING   |
#
# The dispatcher starts no /review session for a story that uses the review
# loop. An executor in neither table gets a mid-cycle blocker. The path is
# computed from the story's existing `executor:` label — no new label value is
# introduced or persisted on the issue.
#
# ─── Executor invocation mechanism (issue #314, ADR-012) ──────────────────────
#
# invoke_executor_command() resolves the provider and model from the tables
# below (the one provider is claude-code) and loads the task prompt: .claude/commands/<slash_command>.md with
# $ARGUMENTS substituted, so a dispatcher session follows the same
# instructions as a manually-run command. Then:
#   claude-code — run_claude_code_session() starts Claude
#     Code on the runner (`claude -p`, prompt on stdin, in this checkout),
#     signed in with the subscription token. It is bounded by --max-turns
#     (AGENT_MAX_TURNS) and a `timeout` of the wall-clock time left
#     (AGENT_MAX_WALLCLOCK_SECONDS since DISPATCH_JOB_START_TS). It succeeds
#     only when the stream-json output ends with a `result` whose subtype is
#     "success" and is_error is false, and Claude Code exits 0. Never pass
#     --bare: bare mode ignores the OAuth token.
# Any other outcome returns non-zero, and the caller posts the blocker.
#
# Authentication:
#   GH_TOKEN (or GITHUB_TOKEN) — GitHub personal access token or Actions token.
#   In GitHub Actions, the workflow sets GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}.
#   PROJECT_TOKEN must be a classic PAT. Its scopes, each with its reason, are
#   listed only in docs/DISPATCHER-CONFIG.md -> "GitHub Token — `PROJECT_TOKEN`".
#   CLAUDE_CODE_OAUTH_TOKEN must be set as a GitHub Secret and wired into
#   .github/workflows/dispatcher.yml's env: block for the "Run dispatcher
#   invoke" step — GitHub Actions never auto-injects a secret; it must be
#   explicitly referenced as ${{ secrets.NAME }} in the workflow YAML or the
#   script never sees it. Do not hardcode any token in this script.
#   scripts/lib/claude-session.sh removes every whitespace character from the
#   subscription token and every ANTHROPIC_* variable from the session's
#   environment, so a session can only run on the subscription.
#
# ─── Notification events (issue #179) ────────────────────────────────────────
#
# Five Slack events are wired into this script via scripts/slack-notify.sh:
#   decision_blocker          — executor declared Type: decision during /implement or /review;
#                               carries the declaration's Question, Options and Recommendation
#   po_blocker                — a blocker this script posted that only the PO can resolve
#                               (post_blocker_once, post_mid_cycle_blocker; issue #359);
#                               not post_conflict_blocker. Resolved by the PO's /unblock line
#   dispatcher_error          — unexpected script failure (EXIT trap, non-zero, unhandled)
#   feature_closure_confirmation — all stories in a feature group reached Done
#   epic_closure_approval     — all features in an epic have all stories Done
#
# Notification adapter failure never aborts the loop: dispatcher_error is guarded
# with || true, and the four events that need PO action are only queued here
# (register_actionable_event) and delivered and retried by
# scripts/dispatcher-notify-retry.sh (issue #295).
# Under --dry-run, calls are suppressed and logged instead.
# Conflict and dependency blockers (WIP overflow, file overlap) do NOT fire decision_blocker.
#
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ─── Read the whole script before any session starts (issue #345) ─────────────
# bash reads a script from the file in pieces, by byte offset. A session this
# run starts (/implement, a fix round) may rewrite this very file, and bash
# would then resume in the middle of a line. Everything below, up to the final
# `}; exit $?`, is therefore one brace group: bash parses a compound command
# completely before it runs the first line of it, and `exit` sits on the same
# line as the closing brace so that nothing is read from the file afterwards.
# Keep it that way: no code after the group, and no `return` at the top level.
# `exec bash "$0"` tail calls start a new process and so read the file anew, on
# purpose: the next story runs the code that was just merged. Sibling scripts
# are safe: lib/*.sh are sourced once at start and the others run as separate
# processes read at call time. See docs/DISPATCHER-CONFIG.md.
{

# ─── Job-level wall-clock deadline (ADR-003) — survives recursive exec ────────
# One story's cycle calls invoke_executor_command() up to two times
# (/implement and /review; /merge and /cleanup start no session, issue #264),
# the resume pass adds sessions for other stories, and the tail-call
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
# The three Claude tiers share the PO's subscription token — see header
# comment above (issue #314, ADR-012).
declare -A EXECUTOR_ROUTING=(
    ["claude-code-haiku"]="Claude Haiku:CLAUDE_CODE_OAUTH_TOKEN"
    ["claude-code-sonnet"]="Claude Sonnet:CLAUDE_CODE_OAUTH_TOKEN"
    ["claude-code-opus"]="Claude Opus:CLAUDE_CODE_OAUTH_TOKEN"
    ["codex"]="Codex:"
)

# ─── Executor provider + model routing table (data-driven, #254, #314) ────────
# Maps executor label suffix to how invoke_executor_command() starts the
# session — "claude-code" (Claude Code on the subscription) — and the model: a
# Claude Code alias. Add a new row to both tables when adding a new executor
# tier (OSS invariant: data-driven, not logic-driven — do not branch on
# EXECUTOR_LABEL anywhere else in this script). An executor with no row here
# is not started for /implement: the dispatcher skips its stories (issue #315).
declare -A EXECUTOR_PROVIDER=(
    ["claude-code-haiku"]="claude-code"
    ["claude-code-sonnet"]="claude-code"
    ["claude-code-opus"]="claude-code"
)

declare -A EXECUTOR_MODEL=(
    ["claude-code-haiku"]="haiku"
    ["claude-code-sonnet"]="sonnet"
    ["claude-code-opus"]="opus"
)

# ─── Review-path tables (data-driven, issues #263, #293, #315) ────────────────
# REVIEW_EXECUTOR_ROUTING and CODEX_REVIEW_LOOP_ROUTING live in
# scripts/lib/review-routing.sh, sourced below, so the poll script reads the
# same rule. An executor in neither table stops with a mid-cycle blocker after
# /implement, so a typo or a new tier never skips review silently. Add a row to
# one of them (and to EXECUTOR_ROUTING, plus EXECUTOR_PROVIDER/EXECUTOR_MODEL
# for an executor the dispatcher starts) when adding a new tier.
# A story "In review" whose executor has a REVIEW_EXECUTOR_ROUTING row is
# reviewed by the resume pass (issue #315).

# ─── Argument parsing ─────────────────────────────────────────────────────────

REPO=""
ISSUE_NUMBER=""
DRY_RUN=false
# Space-separated issue numbers skipped as non-executable (issue #256).
# Internal loop state, carried across the recursive exec; not meant to be
# passed by callers.
NON_EXECUTABLE_ISSUES=""
RESUME_ONLY=false  # --resume: handle resumable work only (issue #293)
# Internal loop state, carried across the recursive exec: the resume pass runs
# once per run, not once per chained story.
RESUME_PASS_DONE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            [[ $# -ge 2 ]] || { echo "Error: --repo requires a value." >&2; exit 1; }
            REPO="$2"; shift 2 ;;
        --issue)
            [[ $# -ge 2 ]] || { echo "Error: --issue requires a value." >&2; exit 1; }
            ISSUE_NUMBER="$2"; shift 2 ;;
        --resume)
            RESUME_ONLY=true; shift ;;
        --resume-pass-done)
            RESUME_PASS_DONE=true; shift ;;
        --dry-run)
            DRY_RUN=true; shift ;;
        --non-executable-issues)
            [[ $# -ge 2 ]] || { echo "Error: --non-executable-issues requires a value." >&2; exit 1; }
            NON_EXECUTABLE_ISSUES="$2"; shift 2 ;;
        *)
            echo "Error: Unknown flag: $1" >&2; exit 1 ;;
    esac
done

[[ -n "$REPO" ]]         || { echo "Error: --repo <owner/repo> is required." >&2;    exit 1; }
if $RESUME_ONLY; then
    [[ -z "$ISSUE_NUMBER" ]] || { echo "Error: --resume and --issue cannot be combined." >&2; exit 1; }
else
    [[ -n "$ISSUE_NUMBER" ]] || { echo "Error: --issue <issue-number> is required." >&2; exit 1; }
fi
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

# Most fix rounds the dispatcher starts per PR (issue #293). Set from the
# repository variable of the same name; GitHub passes an unset variable as an
# empty string, which means the default.
CODEX_FIX_ROUND_MAX="${CODEX_FIX_ROUND_MAX:-3}"
[[ "$CODEX_FIX_ROUND_MAX" =~ ^[1-9][0-9]*$ ]] || {
    echo "Error: CODEX_FIX_ROUND_MAX must be a whole number of one or more; got '$CODEX_FIX_ROUND_MAX'." >&2
    exit 1
}

# The hidden marker the codex-review-bookkeeping job puts on the review Session
# Summary it posts after findings. Mirrors MARKER_PREFIX and
# ACTION_MOVE_IN_IMPLEMENTATION in src/headless_pr_workflow/codex_review_bookkeeping.py
# (tests/test_dispatcher_review_loop.py checks that the two agree).
BOOKKEEPING_FIX_MARKER="<!-- hpw:codex-review-bookkeeping action=move-in-implementation head="
# Account the bookkeeping job posts with. Both workflows authenticate with
# PROJECT_TOKEN, so the default is the login behind this run's own token.
CODEX_BOOKKEEPING_LOGIN="${CODEX_BOOKKEEPING_LOGIN:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
USAGE_LIMIT_SCRIPT="${USAGE_LIMIT_SCRIPT:-$SCRIPT_DIR/dispatcher-usage-limit.sh}"  # overridable for tests
ALLOWANCE_SCRIPT="${ALLOWANCE_SCRIPT:-$SCRIPT_DIR/dispatcher-allowance.sh}"  # overridable for tests
MERGE_SCRIPT="${MERGE_SCRIPT:-$SCRIPT_DIR/dispatcher-merge.sh}"          # /merge without a session (#264); overridable for tests
CLEANUP_SCRIPT="${CLEANUP_SCRIPT:-$SCRIPT_DIR/dispatcher-cleanup.sh}"    # /cleanup without a session (#264); overridable for tests
COMMANDS_DIR="$REPO_ROOT/.claude/commands"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"  # Claude Code binary; overridable for tests (shared with #317)
# Token cleaning and the ANTHROPIC_* scrub, shared with the allowance probe (#314).
# shellcheck source=scripts/lib/claude-session.sh
source "$SCRIPT_DIR/lib/claude-session.sh"
# The WIP and queue limits come from repository variables only (issue #358).
# shellcheck source=scripts/lib/limits.sh
source "$SCRIPT_DIR/lib/limits.sh"
# shellcheck source=scripts/lib/decision-blocker.sh
source "$SCRIPT_DIR/lib/decision-blocker.sh"
# shellcheck source=scripts/lib/evidence-pending.sh
source "$SCRIPT_DIR/lib/evidence-pending.sh"
# shellcheck source=scripts/lib/review-routing.sh
source "$SCRIPT_DIR/lib/review-routing.sh"

# ─── Executor session tuning (#254, #314, ADR-003) — overridable for tests ────
# AGENT_MAX_WALLCLOCK_SECONDS is the enforced safety bound: a Claude Code
# session runs under a `timeout` of the time left, so the script fails closed
# through its own normal exit path, comfortably before the 6h platform kill
# could fire (see ADR-003). AGENT_MAX_TURNS is Claude Code's --max-turns.
AGENT_MAX_TURNS="${AGENT_MAX_TURNS:-60}"          # turns before failing closed (Claude Code --max-turns)
AGENT_MAX_WALLCLOCK_SECONDS="${AGENT_MAX_WALLCLOCK_SECONDS:-10800}"  # 3h total budget per Actions job, shared job-wide via DISPATCH_JOB_START_TS — enforced, not incidental

# A missing or invalid limit variable stops the run before anything is read or
# written: no board status, no comment, no Slack dispatcher_error (issue #358).
# The EXIT trap below is not installed yet, so this exit sends no notification.
load_limits || exit 1
WIP_LIMIT="$WIP_LIMIT_IMPLEMENTATION"

# Dispatcher error tracking — used by the EXIT trap and notification wiring (issue #179)
LAST_ACTION="initializing"
DISPATCH_HANDLED=false  # set true before any expected exit 1 (blocker posted)
FIX_ROUND_SESSION=false  # true while the resume pass starts the /implement of a fix round (issue #293)

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
# LAST_POSTED_AT holds the created_at GitHub gave the comment just posted (the
# current UTC time under --dry-run, or when the response carries none), so a
# caller can key an actionable event on it (issue #359).

LAST_POSTED_AT=""

post_comment() {
    local issue_num="$1"
    local body="$2" response
    if $DRY_RUN; then
        echo "[DRY RUN] Would post comment on #$issue_num:"
        echo "$body" | sed 's/^/  /'
        LAST_POSTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    else
        response=$(gh api "repos/$REPO/issues/$issue_num/comments" \
            -X POST -f body="$body") || return 1
        LAST_POSTED_AT=$(jq -r '.created_at // empty' <<< "$response" 2>/dev/null || true)
        [[ -n "$LAST_POSTED_AT" ]] || LAST_POSTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    fi
}

# ─── Utility: send a Slack notification via slack-notify.sh (issue #179) ─────
# Used for events that are not retried (dispatcher_error). The call is guarded
# with || true so adapter failure never aborts the invoke loop. Under --dry-run,
# calls are suppressed and logged. The three events that need PO action go
# through register_actionable_event() instead (issue #295).

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

# ─── Utility: queue an event that needs PO action (issue #295) ───────────────
# decision_blocker, po_blocker, feature_closure_confirmation and
# epic_closure_approval are recorded in the notification state and delivered, retried and bounded by
# scripts/dispatcher-notify-retry.sh run, which the workflow runs on every
# cycle. A registration failure only warns and never aborts the invoke loop.

register_actionable_event() {
    local event_type="$1" issue_num="$2" since="$3" context_json="$4"
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local args=(register --repo "$REPO" --event "$event_type" --issue "$issue_num"
                --since "$since" --context "$context_json")
    $DRY_RUN && args+=(--dry-run)
    bash "$script_dir/dispatcher-notify-retry.sh" "${args[@]}" \
        || echo "Warning: could not register $event_type for #$issue_num; it will not be delivered" >&2
}

# register_po_blocker <issue> <title> <type> <unblocked-when> <state> [<cause>]
# Queues the po_blocker event for the "## Blocked Declaration" that
# post_comment just posted (issue #359): a blocker only the PO can resolve.
# The event key uses that comment's created_at, so a later blocker on the same
# issue is a new event. Resolved by the PO's `/unblock` line or by closing the
# issue (scripts/dispatcher-notify-retry.sh).
register_po_blocker() {
    local issue_num="$1" title="$2" blocker_type="$3" unblocked_when="$4" state="$5" cause="${6:-}"
    register_actionable_event "po_blocker" "$issue_num" "$LAST_POSTED_AT" \
        "$(jq -nc --arg st "$title" --arg iu "https://github.com/$REPO/issues/$issue_num" \
                  --arg bt "$blocker_type" --arg uw "$unblocked_when" --arg state "$state" \
                  --arg cause "$cause" \
                  '{story_title: $st, issue_url: $iu, blocker_type: $bt,
                    unblocked_when: $uw, state: $state, cause: $cause}')"
}

# Delivers queued actionable events before the script replaces itself with the
# run for the next story, whose sessions can take hours (issue #295). Never
# counts a failure toward the bound and never aborts; the workflow's retry step
# still runs once per cycle.
drain_actionable_events() {
    $DRY_RUN && return 0
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bash "$script_dir/dispatcher-notify-retry.sh" run --repo "$REPO" --drain \
        || echo "Warning: could not deliver queued notifications before the next story" >&2
}

# ─── Utility: detect decision-type blocker and notify Slack (issue #179) ─────
# Called after an executor fails mid-cycle. Scans recent issue comments for a
# ## Blocked Declaration with Type: decision (posted by the executor). Fires
# decision_blocker Slack event only for decision-type blockers — conflict and
# dependency blockers (WIP, file overlap) do NOT trigger this notification.

check_and_notify_decision_blocker() {
    local issue_num="$1"
    local issue_title="$2"
    local blocker_json blocker_body blocker_since
    blocker_json=$(gh api "repos/$REPO/issues/$issue_num/comments?per_page=100" \
        | jq -c '[.[] | select(.body | (test("## Blocked Declaration") and test("Type: decision")))]
                  | last // empty' 2>/dev/null || true)
    [[ -z "$blocker_json" ]] && return 0
    blocker_body=$(jq -r '.body // ""' <<< "$blocker_json")
    blocker_since=$(jq -r '.created_at // ""' <<< "$blocker_json")
    local issue_url="https://github.com/$REPO/issues/$issue_num"
    register_actionable_event "decision_blocker" "$issue_num" "$blocker_since" \
        "$(decision_blocker_context "$blocker_body" "$issue_title" "$issue_url")"
}

# ─── Utility: comment-triggered resume for decision blockers (issue #262) ────
# Epic #160 Success Criterion #7: "Dispatcher resumes automatically after PO
# posts a GitHub comment resolving a decision blocker (detected on next
# poll)." This is additive to, not a replacement for, the time-based
# stale-recovery loop below (Step 4) — it fires immediately once a
# qualifying PO comment appears, without waiting for STALE_THRESHOLD, and
# stale-recovery keeps covering stories that silently stall with no blocker
# declared at all, or where the PO's comment is missed for any reason.
#
# Resolving-comment rule (docs/PROJECT-STATUS.md, Decision blockers): a
# comment authored by the PO ($DECISION_BLOCKER_PO_LOGIN), posted strictly
# after the most recent "## Blocked Declaration (Type: decision)" comment,
# containing a standalone line that trims to exactly "/unblock". A substring
# match of the word "unblock"/"unblocked" inside ordinary prose does NOT
# qualify — only a full line equal to "/unblock" resumes the story.
#
# Comments are fetched with --paginate so a declaration or resolving comment
# past the first 100 comments on a long-lived issue is not silently missed
# (the same pagination-blind-spot class of bug fixed for board queries in
# issues #247/#249/#250/#251) — "detected on next poll" must hold regardless
# of thread length.
#
# Idempotency and retry safety: the CLAIM records the ProjectV2 item's
# updatedAt value before the Ready mutation. If the mutation fails, that value
# is unchanged and a later poll retries without posting another CLAIM. If the
# mutation succeeds, the item version changes; any subsequent redispatch changes
# it again. Thus a later In-implementation scan can tell that the old /unblock
# was already consumed even when posting the CONFIRM comment failed. A CONFIRM
# marker remains the normal happy-path fast check. A newly declared blocker has
# a later declaration timestamp and starts a fresh claim cycle.
#
# Returns 0 when a qualifying resolving comment was found and the board
# transition was confirmed (CONFIRM comment posted). Returns 1 with no
# board mutation left un-retryable when there is no open decision blocker,
# the blocker was already confirmed-resumed, no qualifying resolving
# comment exists yet, or the board transition failed this attempt (safe to
# retry on the next poll — no CONFIRM marker was posted).

DECISION_BLOCKER_PO_LOGIN="LauraMardones"
DECISION_BLOCKER_CLAIM_MARKER="Attempting to resolve the Type: decision blocker"
DECISION_BLOCKER_RESUME_MARKER="Detected: PO comment resolving the Type: decision blocker"

check_and_resolve_decision_blocker_comment() {
    local issue_num="$1" item_id="$2" project_item_updated_at="$3"
    local comments
    comments=$(gh api --paginate "repos/$REPO/issues/$issue_num/comments?per_page=100" | jq -s 'add')

    local blocker_created_at
    blocker_created_at=$(echo "$comments" | jq -r '
        [.[] | select(.body | (test("## Blocked Declaration") and test("Type: decision")))]
        | last | .created_at // empty')
    [[ -z "$blocker_created_at" ]] && return 1

    local already_resumed
    already_resumed=$(echo "$comments" | jq -r \
        --arg marker "$DECISION_BLOCKER_RESUME_MARKER" \
        --arg since "$blocker_created_at" \
        '[.[] | select(
             ((.body // "") | contains($marker)) and
             .created_at >= $since
           )] | length > 0')
    [[ "$already_resumed" == "true" ]] && return 1

    local resolved
    resolved=$(echo "$comments" | jq -r \
        --arg login "$DECISION_BLOCKER_PO_LOGIN" \
        --arg since "$blocker_created_at" \
        '[.[] | select(
             (.user.login // "") == $login and
             .created_at > $since and
             ((.body // "") | split("\n") | any(test("^\\s*/unblock\\s*$")))
           )] | length > 0')
    [[ "$resolved" == "true" ]] || return 1

    echo "RESOLVED: #$issue_num — PO posted a standalone /unblock line after the decision blocker declaration"

    local claim_project_version
    claim_project_version=$(echo "$comments" | jq -r \
        --arg marker "$DECISION_BLOCKER_CLAIM_MARKER" \
        --arg since "$blocker_created_at" \
        '[.[] | select(
             ((.body // "") | contains($marker)) and
             .created_at >= $since
           )] | last | (.body // "")
         | capture("Project item version before resume: (?<version>[^\\n]+)").version // empty')

    # The claim records the ProjectV2 item's version before the mutation. A
    # failed mutation leaves that version unchanged and is safe to retry. A
    # successful mutation changes it; any later redispatch changes it again.
    # Therefore a bare CLAIM plus a different current version proves that the
    # old /unblock was already consumed even when the CONFIRM comment failed.
    if [[ -n "$claim_project_version" && "$project_item_updated_at" != "$claim_project_version" ]]; then
        echo "RESOLVED: #$issue_num — prior resume claim already changed the project item; not re-consuming the old /unblock"
        return 1
    fi

    if [[ -z "$claim_project_version" ]]; then
        post_comment "$issue_num" "## Recovery Comment
$DECISION_BLOCKER_CLAIM_MARKER (standalone \`/unblock\` line found); attempting the board transition to \"Ready for implementation\" now.
Project item version before resume: $project_item_updated_at"
    fi

    # The CONFIRM comment ($DECISION_BLOCKER_RESUME_MARKER) is what the
    # already_resumed check above relies on, so it is only posted AFTER
    # set_project_status_ready() confirms the mutation actually happened —
    # see the return-contract comment above.
    if ! set_project_status_ready "$item_id" "$issue_num"; then
        echo "Warning: #$issue_num — valid /unblock found but the board transition failed; not posting the CONFIRM marker, will retry on the next poll." >&2
        return 1
    fi

    local recovery_comment
    recovery_comment="## Recovery Comment
$DECISION_BLOCKER_RESUME_MARKER (standalone \`/unblock\` line found).
Action: status set to \"Ready for implementation\".
Next executor: review the PO's decision in the linked comment above before resuming."
    if ! post_comment "$issue_num" "$recovery_comment"; then
        echo "Warning: #$issue_num — board transition succeeded but the CONFIRM comment failed; the versioned CLAIM will prevent the old /unblock from being consumed again after redispatch." >&2
        return 1
    fi
    return 0
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
    echo "Feature #$feature_num: all $feature_total stories Done. Queuing Slack notification (feature_closure_confirmation)."
    register_actionable_event "feature_closure_confirmation" "$feature_num" "" \
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
    echo "Epic #$epic_num: all features complete. Queuing Slack notification (epic_closure_approval)."
    register_actionable_event "epic_closure_approval" "$epic_num" "" \
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
    # Optional hidden marker (issue #293): records the PR head a resumable step
    # was tried on, so a later run does not repeat it on the same head.
    local marker="${4:-}"
    # Optional cause and unblock condition (issue #381): the generic text stays
    # the default; a caller that knows the cause names it instead.
    local reason="${5:-}"
    local unblocked_when="${6:-/$failed_command completes successfully for #$issue_num with exit code 0}"
    local body state
    state="execution loop halted after /$failed_command failed; board reflects last successful state"
    body="## Blocked Declaration
Type: external
Declared by: $DECLARED_BY
Blocks: #$issue_num — $issue_title
Unblocked when: $unblocked_when
Owner: PO (@LauraMardones)
State of in-progress work: $state"
    [[ -n "$reason" ]] && body+=$'\n'"Cause: $reason"
    [[ -n "$marker" ]] && body+=$'\n\n'"$marker"
    echo "MID-CYCLE FAILURE: posting blocker on #$issue_num (/$failed_command failed)"
    post_comment "$issue_num" "$body" || return 1
    register_po_blocker "$issue_num" "$issue_title" "external" "$unblocked_when" "$state" "$reason"
}

# pr_conflicts_with_main <pr> — 0 only when GitHub reports the PR as conflicting
# (mergeable == false or mergeable_state == "dirty"). A clean PR, an unknown
# mergeability (null, still being computed) and a failed read all return 1, so
# the caller keeps the generic blocker; none of them is read as a conflict
# (issue #381). The PR list has no mergeable fields, so the PR is read alone.
pr_conflicts_with_main() {
    local pr_number="$1" detail
    detail=$(gh api "repos/$REPO/pulls/$pr_number" 2>/dev/null) || return 1
    jq -e '(.mergeable == false) or (.mergeable_state == "dirty")' <<< "$detail" >/dev/null 2>&1
}

# ─── Utility: "posted once" markers for the resume entries (issue #293) ──────
# The resume entries run on every poll, so each bound is recorded on the story
# as a hidden marker in the blocker comment. Nothing is remembered on the
# runner. A marker holds until the PO posts a standalone `/unblock` line after
# it (the resolving-comment rule of issue #262); a marker that names a PR head
# also stops holding when the PR gets a new head, because the new marker text
# differs.

dispatcher_marker() {
    echo "<!-- hpw:dispatcher $* -->"
}

# Markers are honoured only on comments written by the dispatcher's own
# account (the login behind GH_TOKEN). Anyone can comment on a public
# repository, and a forged marker must neither stop a fix round nor make the
# dispatcher write a board status.
DISPATCHER_LOGIN="${DISPATCHER_LOGIN:-}"
resolve_dispatcher_login() {
    if [[ -z "$DISPATCHER_LOGIN" ]]; then
        DISPATCHER_LOGIN=$(gh api user | jq -r '.login // empty' 2>/dev/null || true)
    fi
    [[ -n "$DISPATCHER_LOGIN" ]]
}

# marker_holds <issue> <marker> [<on-read-error>] [<cancel-marker>]
# 0 when the newest dispatcher comment carrying <marker> has no later PO
# `/unblock` line and no later dispatcher comment carrying <cancel-marker>;
# 1 when there is no such comment or it was unblocked or cancelled. <marker>
# is matched as a substring, so a caller may pass a prefix. A failed read (of
# the comments or of the dispatcher's login) counts as "holds", so an API
# error never starts a session or posts a second blocker. A caller that acts
# when the marker holds passes 1 as <on-read-error>, so a failed read does
# nothing instead.
marker_holds() {
    local issue_num="$1" marker="$2" on_read_error="${3:-0}" cancel_marker="${4:-}" comments
    resolve_dispatcher_login || return "$on_read_error"
    comments=$(gh api --paginate "repos/$REPO/issues/$issue_num/comments?per_page=100" | jq -s 'add // []') || return "$on_read_error"
    jq -e --arg marker "$marker" --arg cancel "$cancel_marker" \
          --arg bot "$DISPATCHER_LOGIN" --arg login "$DECISION_BLOCKER_PO_LOGIN" '
        [.[] | select((.user.login // "") == $bot)] as $own
        | ([$own[] | select((.body // "") | contains($marker))] | last) as $held
        | if $held == null then false
          else ([.[] | select(
                   (.user.login // "") == $login and
                   .created_at > $held.created_at and
                   ((.body // "") | split("\n") | any(test("^\\s*/unblock\\s*$")))
                )] | length == 0)
               and
               ($cancel == "" or
                ([$own[] | select(.created_at > $held.created_at and ((.body // "") | contains($cancel)))]
                 | length == 0))
          end' <<< "$comments" >/dev/null
}

# post_blocker_once <issue> <title> <type> <marker> <unblocked-when> <state>
post_blocker_once() {
    local issue_num="$1" issue_title="$2" blocker_type="$3" marker="$4" unblocked_when="$5" state="$6"
    if marker_holds "$issue_num" "$marker"; then
        return 0
    fi
    echo "BLOCKED: posting $blocker_type blocker on #$issue_num"
    post_comment "$issue_num" "## Blocked Declaration
Type: $blocker_type
Declared by: $DECLARED_BY
Blocks: #$issue_num — $issue_title
Unblocked when: $unblocked_when
Owner: PO (@LauraMardones)
State of in-progress work: $state

$marker" || return 1
    register_po_blocker "$issue_num" "$issue_title" "$blocker_type" "$unblocked_when" "$state"
}

# ─── Utility: retry a required comment that could not be written (#348) ──────
# The merge-owner marker, the merge confirmation and the cleanup closing
# summary are durable evidence. When a write of one fails, the writer leaves an
# evidence-pending comment (scripts/lib/evidence-pending.sh) whose hidden
# payload is the exact text. evidence_retry posts that text once, on a later
# run, and counts the runs that failed on hidden markers on the story, never on
# the runner: after EVIDENCE_RETRY_MAX failed runs it posts one blocker (which queues one
# po_blocker event, no dispatcher_error), and stops retrying until the PO posts `/unblock`.
# Every marker is honoured only on a comment of the dispatcher's own account.

EVIDENCE_RETRY_MAX="${EVIDENCE_RETRY_MAX:-3}"

evidence_failed_marker() {
    dispatcher_marker "action=evidence-retry-failed item=$1 ref=$2"
}

evidence_gave_up_marker() {
    dispatcher_marker "action=evidence-gave-up item=$1 ref=$2"
}

# bot_comments_json <issue-or-pr> — the comments of the dispatcher's own account
# as one JSON array; fails when they cannot be read.
bot_comments_json() {
    local number="$1" comments
    resolve_dispatcher_login || return 1
    comments=$(gh api --paginate "repos/$REPO/issues/$number/comments?per_page=100" | jq -s 'add // []') || return 1
    jq -c --arg bot "$DISPATCHER_LOGIN" '[.[] | select((.user.login // "") == $bot)]' <<< "$comments"
}

# evidence_failure_count <story> <item> <ref> — failed runs recorded since the
# last give-up marker for the item.
evidence_failure_count() {
    local story="$1" item="$2" ref="$3" own
    own=$(bot_comments_json "$story") || { echo 0; return 0; }
    jq -r --arg failed "$(evidence_failed_marker "$item" "$ref")" \
          --arg gave "$(evidence_gave_up_marker "$item" "$ref")" '
        ([.[] | select((.body // "") | contains($gave)) | .created_at] | max // "") as $since
        | [.[] | select(((.body // "") | contains($failed)) and .created_at > $since)] | length' <<< "$own"
}

# evidence_record_failure <story> <title> <item> <ref> <what> — records one
# failed run. Returns 1 while retries remain, 2 when this run is the last one.
evidence_record_failure() {
    local story="$1" title="$2" item="$3" ref="$4" what="$5" count run
    count=$(evidence_failure_count "$story" "$item" "$ref")
    run=$(( count + 1 ))
    post_comment "$story" "Dispatcher: $what could not be written to GitHub (failed run $run of $EVIDENCE_RETRY_MAX). It is retried on the next run.

$(evidence_failed_marker "$item" "$ref")" || true
    if [[ $run -lt $EVIDENCE_RETRY_MAX ]]; then
        return 1
    fi
    if ! marker_holds "$story" "$(evidence_gave_up_marker "$item" "$ref")"; then
        post_blocker_once "$story" "$title" "external" "$(evidence_gave_up_marker "$item" "$ref")" \
            "the evidence ($what) is posted by hand, then the PO posts /unblock" \
            "the dispatcher failed $EVIDENCE_RETRY_MAX runs in a row to write $what; no further retry until /unblock. The text is in the evidence-pending comment ($item) on the PR or issue."
    fi
    return 2
}

# evidence_retry <target> <item> <ref> <story> <title> [<expected-head>]
# Posts the pending evidence of <item> on <target> (a PR or an issue) once.
#   0  nothing is pending, it is stale, or the evidence is posted (now or earlier)
#   1  still missing: this run's write failed, or a read failed; retries remain
#   2  still missing and retrying has stopped (blocker posted, or /unblock needed)
# With <expected-head>, evidence recorded for another head is never posted.
evidence_retry() {
    local target="$1" item="$2" ref="$3" story="$4" title="$5" expected_head="${6:-}"
    local own pending head b64 payload
    own=$(bot_comments_json "$target") || {
        echo "Warning: could not read the comments on #$target; the $item evidence is not retried in this run." >&2
        return 1
    }
    pending=$(jq -r --arg m "action=evidence-pending item=$item " \
        '[.[] | select((.body // "") | contains($m))] | last | .body // empty' <<< "$own")
    [[ -n "$pending" ]] || return 0
    if [[ "$pending" =~ head=([^[:space:]]+)[[:space:]]payload=([A-Za-z0-9+/=]*) ]]; then
        head="${BASH_REMATCH[1]}"; b64="${BASH_REMATCH[2]}"
    else
        echo "${LOG_PREFIX}[RESUME SKIP] #$story: the $item evidence-pending comment on #$target is malformed; ignored" >&2
        return 0
    fi
    if [[ -n "$expected_head" && "${head,,}" != "${expected_head,,}" ]]; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$story: the $item evidence on #$target is for head ${head:0:7}, not ${expected_head:0:7}; not posted"
        return 0
    fi
    payload=$(printf '%s' "$b64" | base64 -d 2>/dev/null) || payload=""
    if [[ -z "$payload" ]]; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$story: the $item evidence payload on #$target could not be decoded; ignored" >&2
        return 0
    fi
    if jq -e --arg p "$payload" '
            def trim: gsub("^\\s+|\\s+$"; "");
            any(.[]; ((.body // "") | contains("action=evidence-pending") | not)
                     and ((.body // "") | trim | contains($p | trim)))' <<< "$own" >/dev/null; then
        return 0
    fi
    if marker_holds "$story" "$(evidence_gave_up_marker "$item" "$ref")"; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$story: the $item evidence on #$target is still missing; retrying stopped after $EVIDENCE_RETRY_MAX runs, waiting for a PO /unblock line"
        return 2
    fi
    echo "${LOG_PREFIX}[RESUME] #$story: posting the missing $item evidence on #$target"
    if post_comment "$target" "$payload"; then
        return 0
    fi
    echo "Warning: the $item evidence on #$target could not be posted; retried on the next run." >&2
    evidence_record_failure "$story" "$title" "$item" "$ref" "the $item evidence on #$target"
}

# The cleanup closing summary is written on a story that is already Done, so no
# board state leads back to it. When only its pending copy is on the story, the
# story also carries this label; one listing of the label finds every such
# story, however old. The label is only an index: what is posted is still read
# from the dispatcher's own evidence-pending comment.
EVIDENCE_PENDING_LABEL="evidence:pending"
EVIDENCE_PENDING_LABEL_PATH="evidence%3Apending"

# label_pending_cleanup <issue> — 0 when the story now carries the label.
label_pending_cleanup() {
    if $DRY_RUN; then
        echo "[DRY RUN] Would add the $EVIDENCE_PENDING_LABEL label to #$1"
        return 0
    fi
    # Adding a label the repository does not have fails with 404, so create it
    # first; a label that already exists answers 422, which is fine.
    gh api "repos/$REPO/labels" -X POST -f "name=$EVIDENCE_PENDING_LABEL" -f "color=d93f0b" \
        -f "description=Dispatcher: a cleanup closing summary is still to be posted (#348)" >/dev/null 2>&1 || true
    gh api "repos/$REPO/issues/$1/labels" -X POST -f "labels[]=$EVIDENCE_PENDING_LABEL" >/dev/null
}

# retry_pending_cleanup_summaries — posts the pending closing summary of every
# story that carries the label, and removes the label once nothing is pending.
# Never stops the run: other stories are not blocked by it.
retry_pending_cleanup_summaries() {
    local issues n title rc
    resolve_dispatcher_login || return 0
    issues=$(gh api --paginate "repos/$REPO/issues?state=all&labels=$EVIDENCE_PENDING_LABEL_PATH&per_page=100" \
        --jq '.[] | select(.pull_request == null) | .number' 2>/dev/null) || {
        echo "Warning: could not list the issues labelled $EVIDENCE_PENDING_LABEL; pending cleanup summaries are retried on a later run." >&2
        return 0
    }
    for n in $issues; do
        title=$(gh api "repos/$REPO/issues/$n" --jq '.title' 2>/dev/null) || title=""
        rc=0
        evidence_retry "$n" "cleanup-summary" "issue$n" "$n" "$title" || rc=$?
        # 0: posted now or earlier, or nothing of the dispatcher's own is pending.
        [[ $rc -eq 0 ]] || continue
        if $DRY_RUN; then
            echo "[DRY RUN] Would remove the $EVIDENCE_PENDING_LABEL label from #$n"
        elif ! gh api "repos/$REPO/issues/$n/labels/$EVIDENCE_PENDING_LABEL_PATH" -X DELETE >/dev/null 2>&1; then
            echo "Warning: could not remove the $EVIDENCE_PENDING_LABEL label from #$n; removed on a later run." >&2
        fi
    done
    return 0
}

# ─── Utility: start a Claude Code session on the subscription (#314, ADR-012) ─
# Runs Claude Code on the runner in this checkout with the task prompt on
# stdin, signed in with the cleaned subscription token and with every
# ANTHROPIC_* variable removed (scripts/lib/claude-session.sh). Bounded by
# --max-turns AGENT_MAX_TURNS and a `timeout` of the wall-clock time left of
# AGENT_MAX_WALLCLOCK_SECONDS since DISPATCH_JOB_START_TS.
#
# After the session ends, logs one "[session tool]" line per tool call and one
# "[SESSION]" summary line (stderr). Returns 0 with the result text on stdout
# only when the stream has a `result` with subtype "success" and is_error
# false, and Claude Code exited 0. Every other outcome returns non-zero with a
# "[SESSION ERROR]" line on stderr; a provider usage limit stop returns
# CLAUDE_LIMIT_STOP_RC with a "[SESSION RATE LIMIT]" line. The stream is
# parsed by message type, not line order, and kept in a temporary directory
# that is removed before returning. The token, the full stream and the init
# message are never printed; the token is also masked in the lines that are.

# Exit code of run_claude_code_session() and invoke_executor_command() for a
# session stopped by a provider usage limit (issue #316); 1 stays "failed".
# CLAUDE_LIMIT_RESET_TS then holds the reset time as Unix seconds, or is empty.
CLAUDE_LIMIT_STOP_RC=75
CLAUDE_LIMIT_RESET_TS=""

CLAUDE_SESSION_ARGS=(-p --output-format stream-json --verbose
                     --permission-mode bypassPermissions --strict-mcp-config)

run_claude_code_session() {
    CLAUDE_LIMIT_RESET_TS=""
    local alias="$1" token="$2" task_prompt="$3" slash_command="$4" target_arg="$5"
    local work rc=0
    work=$(mktemp -d)
    _claude_code_session "$work" "$alias" "$token" "$task_prompt" "$slash_command" "$target_arg" || rc=$?
    rm -rf "$work"
    return "$rc"
}

_claude_code_session() {
    local work="$1" alias="$2" token="$3" task_prompt="$4" slash_command="$5" target_arg="$6"
    local label="/$slash_command #$target_arg"
    local elapsed remaining
    elapsed=$(( $(date +%s) - DISPATCH_JOB_START_TS ))
    remaining=$(( AGENT_MAX_WALLCLOCK_SECONDS - elapsed ))
    if (( remaining <= 0 )); then
        echo "[SESSION ERROR] $label not started: no time left of AGENT_MAX_WALLCLOCK_SECONDS (${AGENT_MAX_WALLCLOCK_SECONDS}s) since job start (elapsed ${elapsed}s)" >&2
        return 1
    fi

    printf '%s' "$task_prompt" > "$work/prompt.md"
    echo "Starting Claude Code ($alias) for $label; at most $AGENT_MAX_TURNS turns and ${remaining}s" >&2
    local session_rc=0
    # stdout and stderr go to files, not a pipe: a process the session leaves
    # running cannot hold the step open.
    ( cd "$REPO_ROOT" && claude_scrubbed_exec "$token" \
          timeout "$remaining" "$CLAUDE_BIN" "${CLAUDE_SESSION_ARGS[@]}" \
          --model "$alias" --max-turns "$AGENT_MAX_TURNS" \
          < "$work/prompt.md" > "$work/stream.jsonl" 2> "$work/stderr.txt" ) || session_rc=$?

    jq -cR 'fromjson? // empty | select(type == "object")' "$work/stream.jsonl" > "$work/events.jsonl" 2>/dev/null || : > "$work/events.jsonl"

    # One line per tool call, the input cut to 200 characters.
    jq -r --arg tok "$token" '
        select(.type == "assistant") | .message.content[]? | select(type == "object" and .type == "tool_use")
        | (if (.input | type) == "object" and (.input.command | type) == "string"
           then .input.command else (.input | tojson) end) as $in
        | "[session tool] \(.name // "unknown"): \($in | split($tok) | join("[redacted]") | gsub("[\r\n]+"; " ") | .[0:200])"
        ' "$work/events.jsonl" >&2 2>/dev/null || true

    local init result api_key_source
    init=$(jq -c 'select(.type == "system" and .subtype == "init")' "$work/events.jsonl" | tail -1)
    result=$(jq -c 'select(.type == "result")' "$work/events.jsonl" | tail -1)
    api_key_source=$(jq -r '(.apiKeySource // "missing") | tostring' <<< "${init:-null}")

    if [[ -n "$result" ]]; then
        jq -r --arg label "$label" --arg alias "$alias" --arg aks "$api_key_source" '
            (.usage // {}) as $u
            | "[SESSION] \($label) on \($alias): \(.subtype // "unknown"), \(.num_turns // "?") turns, "
              + "\(((.duration_ms // 0) / 1000) | round) s, apiKeySource \($aks), "
              + "input \(($u.input_tokens // 0) + ($u.cache_creation_input_tokens // 0) + ($u.cache_read_input_tokens // 0)) / output \($u.output_tokens // 0) tokens"
            ' <<< "$result" >&2
    fi

    if [[ $session_rc -eq 124 ]]; then
        echo "[SESSION ERROR] $label stopped by the wall-clock bound after ${remaining}s (AGENT_MAX_WALLCLOCK_SECONDS=${AGENT_MAX_WALLCLOCK_SECONDS})" >&2
        return 1
    fi

    # 401: the token was rejected. Same checks and wording as the allowance
    # probe (#317 AC8).
    local result_text
    result_text=$(jq -r '[.result, .error] | map(strings) | join(" ")' <<< "${result:-null}" 2>/dev/null || true)
    if jq -e '.api_error_status == 401' <<< "${result:-null}" >/dev/null 2>&1 \
        || grep -qE 'API Error: 401|401 Unauthorized|OAuth access token is invalid|authentication_error' \
            <<< "$result_text"$'\n'"$(cat "$work/stderr.txt")"; then
        echo "[SESSION ERROR] $label: the subscription token was rejected (401). Run 'claude setup-token' again and save the CLAUDE_CODE_OAUTH_TOKEN secret with nothing before or after the token" >&2
        return 1
    fi

    if [[ -n "$init" || $session_rc -eq 0 ]] && [[ "$api_key_source" != "none" ]]; then
        echo "[SESSION ERROR] apiKeySource=$api_key_source; only the subscription token may be used" >&2
        return 1
    fi

    local reset_ts="" limit_rc=0
    reset_ts=$(claude_limit_stop "$work/events.jsonl") || limit_rc=$?
    if [[ $limit_rc -eq 0 ]]; then
        CLAUDE_LIMIT_RESET_TS="$reset_ts"
        local reset_text="unknown"
        [[ -n "$reset_ts" ]] && reset_text=$(date -u -d "@$reset_ts" +%Y-%m-%dT%H:%MZ 2>/dev/null || echo "unknown")
        echo "[SESSION RATE LIMIT] $label stopped at a provider usage limit; resets $reset_text" >&2
        return "$CLAUDE_LIMIT_STOP_RC"
    fi

    if [[ $session_rc -ne 0 ]]; then
        echo "[SESSION ERROR] $label: Claude Code exited with code $session_rc" >&2
        tail -n 5 "$work/stderr.txt" | jq -rR --arg tok "$token" 'split($tok) | join("[redacted]")' >&2 2>/dev/null || true
        return 1
    fi
    if [[ -z "$result" ]]; then
        echo "[SESSION ERROR] $label: the stream has no result message" >&2
        return 1
    fi
    if ! jq -e '.subtype == "success" and .is_error == false' <<< "$result" >/dev/null 2>&1; then
        echo "[SESSION ERROR] $label: result $(jq -r '"subtype \(.subtype // "missing"), is_error \(.is_error)"' <<< "$result"); not treating as success" >&2
        return 1
    fi

    jq -r --arg tok "$token" '(.result // "") | tostring | split($tok) | join("[redacted]")' <<< "$result"
}

# ─── Utility: subscription allowance check before a session start (#317) ────
# Runs scripts/dispatcher-allowance.sh (ADR-012) and acts on its decision:
#   exit 0 (start)  — return 0; the session starts as before.
#   exit 1 (refuse) — before /implement: log [RESERVE SKIP], put the story back
#                     to "Ready for implementation" (E1 already moved it), exit 0.
#                     Before the /implement of a fix round (issue #293): log
#                     [RESERVE SKIP] and write nothing. The story keeps its
#                     place in "In implementation" and the resume pass tries
#                     the fix round again on a later run.
#                     Before /review: one comment on the
#                     story naming the step, the window, the share left, the
#                     reserve and the reset time; exit 0. A later poll tries it again.
#   exit 2 (error)  — log [ALLOWANCE ERROR] and fail the job without a
#                     dispatcher_error Slack message; after /implement, the
#                     story gets the same comment with the reason.
# Every non-start path sets DISPATCH_HANDLED=true and exits the script.

allowance_comment() {
    local step="$1" decision_json="$2"
    jq -r --arg step "$step" --arg issue "$ISSUE_NUMBER" '
        def line($name; $w): "- \($name): \($w.remaining_percent)% left, reserve \($w.reserve_percent)%, resets \($w.resets_at // "unknown")";
        . as $d
        | [ ["five-hour window", .five_hour], ["weekly window", .weekly] ]
            | map(select(.[1].remaining_percent != null)) as $read
        | ($read | map(select(.[1].remaining_percent < .[1].reserve_percent))) as $below
        | (if $d.decision == "refuse"
           then "/\($step) was not started for #\($issue): the subscription allowance is below the reserve the PO set."
           else "/\($step) was not started for #\($issue): the subscription allowance could not be read, so nothing starts."
           end) as $head
        | ( if $d.decision == "refuse" and ($below | length) > 0 then $below
            elif $d.decision == "refuse" then []
            else $read end
            | map(line(.[0]; .[1])) ) as $lines
        | ["## Subscription allowance check", "", $head, ""]
          + (if ($lines | length) > 0 then $lines + [""] else [] end)
          + ["Reason: \($d.reason)", "",
             "The story stays at its current step. The dispatcher tries /\($step) again on a later poll."]
        | join("\n")' <<< "$decision_json"
}

# Posts the allowance comment unless it is already the story's newest comment
# for this step (issue #293): a resume entry is tried again on every run, and
# a refusal that repeats must not add a comment each time.
post_allowance_comment() {
    local step="$1" decision_json="$2" newest
    newest=$(gh api --paginate "repos/$REPO/issues/$ISSUE_NUMBER/comments?per_page=100" \
        | jq -rs 'add // [] | last | .body // ""' 2>/dev/null || true)
    if [[ "$newest" == "## Subscription allowance check"* \
          && "$newest" == *"/$step was not started for #$ISSUE_NUMBER:"* ]]; then
        echo "#$ISSUE_NUMBER: the allowance comment for /$step is already the newest comment; not posting it again"
        return 0
    fi
    post_comment "$ISSUE_NUMBER" "$(allowance_comment "$step" "$decision_json")"
}

check_subscription_allowance() {
    local step="$1"
    if $DRY_RUN; then
        echo "[DRY RUN] Would check subscription allowance before /$step"
        return 0
    fi

    local decision_json rc=0
    decision_json=$(bash "$ALLOWANCE_SCRIPT") || rc=$?
    [[ $rc -eq 0 ]] && return 0

    if ! jq -e 'type == "object" and (.decision | type) == "string"' <<< "$decision_json" >/dev/null 2>&1; then
        decision_json=$(jq -n --argjson rc "$rc" '{decision: "error", reason: "allowance check exited \($rc) without a decision", five_hour: {}, weekly: {}}')
        rc=2
    fi

    LAST_ACTION="allowance check before /$step for #$ISSUE_NUMBER"
    if [[ $rc -eq 1 ]]; then
        if [[ "$step" == "implement" ]] && $FIX_ROUND_SESSION; then
            echo "[RESERVE SKIP] #$ISSUE_NUMBER: fix round not started; story stays in \"In implementation\" and the fix round is tried again on a later run"
        elif [[ "$step" == "implement" ]]; then
            echo "[RESERVE SKIP] #$ISSUE_NUMBER: /implement not started; story stays in \"Ready for implementation\" for a later poll"
            set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "Ready for implementation"
        else
            echo "[RESERVE SKIP] #$ISSUE_NUMBER: /$step not started; commenting on the story"
            post_allowance_comment "$step" "$decision_json"
        fi
        DISPATCH_HANDLED=true
        exit 0
    fi

    echo "[ALLOWANCE ERROR] #$ISSUE_NUMBER: /$step not started: $(jq -r '.reason' <<< "$decision_json")" >&2
    if [[ "$step" == "implement" ]] && $FIX_ROUND_SESSION; then
        echo "#$ISSUE_NUMBER stays in \"In implementation\"; the fix round is tried again on a later run" >&2
    elif [[ "$step" == "implement" ]]; then
        set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "Ready for implementation"
    else
        post_allowance_comment "$step" "$decision_json"
    fi
    DISPATCH_HANDLED=true
    exit 1
}

# ─── Utility: usage limit before a session start (#320) ─────────────────────
# The PO caps the sessions the dispatcher starts per UTC day with
# USAGE_LIMIT_SESSIONS_PER_DAY (scripts/dispatcher-usage-limit.sh). Checked
# before the allowance probe, so a stop by the limit spends no probe turn.
#   exit 0 (start)  — return 0.
#   exit 1 (refuse) — same placement as a refusal by the reserve: before
#                     /implement the story goes back to "Ready for
#                     implementation"; before a fix round it keeps its place
#                     and nothing is written; before /review, /merge or
#                     /cleanup one comment says so. The first stop of a UTC
#                     day sets the step output usage_limit_reached=true, which
#                     the workflow turns into one Slack notification. Exit 0:
#                     no other session starts in this run.
#   exit 2 (error)  — [USAGE LIMIT ERROR] from the script; fail the job without
#                     a dispatcher_error Slack message.
# The session is counted by record_session_start, just before it starts.

usage_limit_comment() {
    local step="$1" count="$2" limit="$3"
    local head tail
    if [[ -n "$limit" ]]; then
        head="/$step was not started for #$ISSUE_NUMBER: the dispatcher has started $count of $limit sessions today, the daily limit the PO set. New sessions start again at 00:00 UTC."
    else
        head="/$step was not started for #$ISSUE_NUMBER: USAGE_LIMIT_SESSIONS_PER_DAY is not set or is not a whole number of zero or more, so nothing starts."
    fi
    tail="The story stays at its current step. The dispatcher tries /$step again on a later poll."
    printf '## Usage limit\n\n%s\n\n%s\n' "$head" "$tail"
}

# Posts the comment unless it is already the story's newest comment for this
# step: a resume entry is tried again on every run.
post_usage_limit_comment() {
    local step="$1" count="$2" limit="$3" newest
    newest=$(gh api --paginate "repos/$REPO/issues/$ISSUE_NUMBER/comments?per_page=100" \
        | jq -rs 'add // [] | last | .body // ""' 2>/dev/null || true)
    if [[ "$newest" == "## Usage limit"* \
          && "$newest" == *"/$step was not started for #$ISSUE_NUMBER:"* ]]; then
        echo "#$ISSUE_NUMBER: the usage limit comment for /$step is already the newest comment; not posting it again"
        return 0
    fi
    post_comment "$ISSUE_NUMBER" "$(usage_limit_comment "$step" "$count" "$limit")"
}

check_usage_limit() {
    local step="$1"
    if $DRY_RUN; then
        echo "[DRY RUN] Would check usage limit before /$step"
        return 0
    fi

    local out rc=0 count="" limit=""
    out=$(bash "$USAGE_LIMIT_SCRIPT" check) || rc=$?
    [[ $rc -eq 0 ]] && return 0
    read -r count limit <<< "$out"
    LAST_ACTION="usage limit check before /$step for #$ISSUE_NUMBER"

    if [[ $rc -eq 1 ]]; then
        echo "[USAGE LIMIT SKIP] #$ISSUE_NUMBER: /$step not started; $count of $limit sessions today"
        if [[ "$step" == "implement" ]] && $FIX_ROUND_SESSION; then
            echo "#$ISSUE_NUMBER stays in \"In implementation\"; the fix round is tried again on a later run"
        elif [[ "$step" == "implement" ]]; then
            set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "Ready for implementation"
        else
            post_usage_limit_comment "$step" "$count" "$limit"
        fi
        if bash "$USAGE_LIMIT_SCRIPT" notify-due && [[ -n "${GITHUB_OUTPUT:-}" ]]; then
            echo "usage_limit_reached=true" >> "$GITHUB_OUTPUT"
        fi
        DISPATCH_HANDLED=true
        exit 0
    fi

    if [[ "$step" == "implement" ]] && $FIX_ROUND_SESSION; then
        echo "#$ISSUE_NUMBER stays in \"In implementation\"; the fix round is tried again on a later run" >&2
    elif [[ "$step" == "implement" ]]; then
        set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "Ready for implementation"
    else
        post_usage_limit_comment "$step" "" ""
    fi
    DISPATCH_HANDLED=true
    exit 1
}

# Counts one session, just before it starts: after the usage limit and the
# allowance check passed, so a refused start does not count, and a session
# that later fails or is stopped by a provider limit still does.
record_session_start() {
    bash "$USAGE_LIMIT_SCRIPT" record || {
        echo "[USAGE LIMIT ERROR] could not record the session start — starting nothing" >&2
        return 1
    }
}

# ─── Utility: pause at a provider usage limit (issue #316) ───────────────────
# A session stopped by a provider usage limit (invoke_executor_command()
# returned CLAUDE_LIMIT_STOP_RC) ends by itself at the reset, so it is not a
# blocker and needs no PO action. pause_at_usage_limit <step> <target> <issue>:
#   - a new /implement goes back to "Ready for implementation"; a fix round
#     stays "In implementation" and a /review stays "In review";
#   - no mid-cycle blocker and no attempt marker is written, so the resume
#     pass (or the next selection) starts the same step again once the usage
#     limit and the allowance check let it;
#   - one "## Paused at a provider usage limit" comment records what was left
#     behind, unless it is already the story's newest comment for this step and
#     PR head; no Slack message and no actionable event;
#   - the run exits 0 and starts no other session.
# Only pushed commits survive a runner; the comment counts what was lost.

paused_comment() {
    local step="$1" target="$2" issue="$3" branch="$4" pr_number="$5" pr_head="$6" \
          reset_ts="$7" unpushed="$8" dirty="$9" next="${10}"
    local reset_text="unknown — the dispatcher checks the allowance on every poll"
    [[ -n "$reset_ts" ]] && reset_text=$(date -u -d "@$reset_ts" +%Y-%m-%dT%H:%MZ 2>/dev/null || echo "$reset_text")
    local pr_text="no PR yet"
    [[ -n "$pr_number" ]] && pr_text="#$pr_number, head \`${pr_head}\`"
    local dirty_text="no uncommitted changes"
    [[ "$dirty" == "true" ]] && dirty_text="uncommitted changes"
    printf '## Paused at a provider usage limit\n\n'
    printf -- '- Step: `/%s %s` for #%s\n' "$step" "$target" "$issue"
    printf -- '- Branch: %s\n' "${branch:-unknown}"
    printf -- '- PR: %s\n' "$pr_text"
    printf -- '- Resets: %s\n' "$reset_text"
    printf -- '- Not pushed when the session stopped: %s commit(s), %s\n\n' "$unpushed" "$dirty_text"
    printf 'Only pushed commits survive; anything not pushed is redone by the next start.\n\n'
    printf 'Next: %s\n\nNo PO action is needed.\n' "$next"
}

pause_at_usage_limit() {
    local step="$1" target="$2" issue="$3"
    local pr_json pr_number pr_head branch unpushed dirty next reset_ts rollback_failed=false
    reset_ts="$CLAUDE_LIMIT_RESET_TS"
    LAST_ACTION="pausing #$issue at a provider usage limit"
    pr_json=$(find_linked_pr_json "$issue" 2>/dev/null || true)
    pr_number=$(jq -r '.number // empty' <<< "${pr_json:-null}" 2>/dev/null || true)
    pr_head=$(jq -r '.head.sha // empty' <<< "${pr_json:-null}" 2>/dev/null || true)
    branch=$(jq -r '.head.ref // empty' <<< "${pr_json:-null}" 2>/dev/null || true)
    [[ -n "$branch" ]] || branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
    unpushed=$(git -C "$REPO_ROOT" rev-list --count '@{upstream}..HEAD' 2>/dev/null \
        || git -C "$REPO_ROOT" rev-list --count 'origin/main..HEAD' 2>/dev/null || echo 0)
    dirty=false
    [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]] && dirty=true

    if [[ "$step" == "implement" ]] && $FIX_ROUND_SESSION; then
        next="the fix round stays in \"In implementation\" and is tried again on a later poll, once the usage limit and the allowance check allow it."
    elif [[ "$step" == "implement" ]]; then
        next="#$issue goes back to \"Ready for implementation\"; /implement starts again on a later poll, once the usage limit and the allowance check allow it, and continues on the existing branch and PR."
        if ! set_project_status_checked "$TARGET_ITEM_ID" "$issue" "Ready for implementation"; then
            rollback_failed=true
            echo "Warning: Board status update failed for #$issue to 'Ready for implementation'" >&2
            next="the board write to \"Ready for implementation\" did not take effect, so #$issue is still in \"In implementation\"; stale recovery returns it to \"Ready for implementation\" after ${STALE_THRESHOLD}s of inactivity, and /implement then starts again on the existing branch and PR."
        fi
    else
        next="the story stays in \"In review\" and /$step is tried again on a later poll, once the usage limit and the allowance check allow it."
    fi

    local body newest pr_text="no PR yet"
    [[ -n "$pr_number" ]] && pr_text="#$pr_number, head \`$pr_head\`"
    body=$(paused_comment "$step" "$target" "$issue" "$branch" "$pr_number" "$pr_head" "$reset_ts" "$unpushed" "$dirty" "$next")
    newest=$(gh api --paginate "repos/$REPO/issues/$issue/comments?per_page=100" \
        | jq -rs 'add // [] | last | .body // ""' 2>/dev/null || true)
    if [[ "$newest" == "## Paused at a provider usage limit"* \
          && "$newest" == *"- Step: \`/$step $target\` for #$issue"* \
          && "$newest" == *"- PR: $pr_text"* ]]; then
        echo "#$issue: the pause record for /$step is already the newest comment; not posting it again"
    else
        post_comment "$issue" "$body" \
            || echo "Warning: could not post the pause record on #$issue" >&2
    fi
    DISPATCH_HANDLED=true
    if $rollback_failed; then
        echo "#$issue paused at a provider usage limit, but the move to \"Ready for implementation\" failed" >&2
        exit 1
    fi
    echo "#$issue paused at a provider usage limit; no other session is started in this run"
    exit 0
}

# ─── Utility: invoke an executor command (issue #254, issue #314) ────────────
# Resolves provider/model from the data-driven tables above, loads the
# corresponding .claude/commands/*.md file as the task prompt — the same
# instructions a manually-run command follows — substitutes $ARGUMENTS, and
# starts the session: Claude Code on the subscription for the "claude-code"
# provider (run_claude_code_session).
#
# The optional third argument (issue #263) lets a caller invoke a command
# under a *different* executor label than the story's own $EXECUTOR_LABEL —
# used by the "In review" resume entry to run /review under the executor
# REVIEW_EXECUTOR_ROUTING names instead of the story's own label. It defaults to $EXECUTOR_LABEL, so
# every other call site (/implement) is unaffected. /merge and /cleanup never
# come through here (issue #264).

invoke_executor_command() {
    local slash_command="$1"
    local target_arg="$2"
    local executor_label="${3:-$EXECUTOR_LABEL}"

    # First: an executor with no provider row starts nothing, and spends no
    # usage-limit count and no allowance probe.
    local provider="${EXECUTOR_PROVIDER[$executor_label]:-}"
    local model="${EXECUTOR_MODEL[$executor_label]:-}"
    if [[ -z "$provider" || -z "$model" ]]; then
        echo "Error: No provider/model entry for executor '$executor_label' in EXECUTOR_PROVIDER/EXECUTOR_MODEL." >&2
        return 1
    fi

    check_usage_limit "$slash_command"
    check_subscription_allowance "$slash_command"

    local routing_value="${EXECUTOR_ROUTING[$executor_label]:-}"
    local executor_secret="${routing_value##*:}"
    local secret_value=""
    [[ -n "$executor_secret" ]] && secret_value="${!executor_secret:-}"
    # A secret saved with stray whitespace fails with a 401 (ADR-012 trial).
    secret_value=$(claude_clean_token "$secret_value")
    if [[ -z "$executor_secret" || -z "$secret_value" ]]; then
        echo "Error: Secret '$executor_secret' is not set in the environment." >&2
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
        echo "[DRY RUN] Would start Claude Code ($model) on the subscription for /$slash_command $target_arg"
        return 0
    fi

    # Fail closed: a session that cannot be counted does not start.
    record_session_start || return 1

    case "$provider" in
        claude-code)
            run_claude_code_session "$model" "$secret_value" "$task_prompt" "$slash_command" "$target_arg"
            ;;
        *)
            echo "Error: Unknown provider '$provider' for executor '$executor_label'." >&2
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

# ─── Utility: linked PR facts for the hand-off and resume entries (#293) ─────
# find_linked_pr_json prints the linked open PR as one JSON object (number,
# draft, head.sha), or nothing. find_merged_linked_pr prints the number of a
# merged PR that closes the issue, looking at the 100 most recently updated
# closed PRs.

find_linked_pr_json() {
    local issue_num="$1"
    gh api "repos/$REPO/pulls?state=open&per_page=100" \
        | jq -c --argjson n "$issue_num" '
            map(select(
                (.body // "") | ascii_downcase |
                test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")
            )) | first // empty'
}

find_merged_linked_pr() {
    local issue_num="$1"
    gh api "repos/$REPO/pulls?state=closed&sort=updated&direction=desc&per_page=100" \
        | jq -r --argjson n "$issue_num" '
            map(select(
                .merged_at != null and
                ((.body // "") | ascii_downcase |
                 test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b"))
            )) | first | .number // empty'
}

# ─── Utility: fix-round facts from the bookkeeping job's marker (#293) ───────
# fix_round_heads prints, one per line, the PR heads for which the
# codex-review-bookkeeping job recorded findings on PR <pr>: comments by the
# job's account that carry its hidden marker with action=move-in-implementation.
# The dispatcher reads only this marker; it never parses Codex comments or
# reviews (the recognizer lives in review_policy.py). A marker in a comment by
# any other account is ignored. An unresolved account or a failed read prints
# nothing, so no fix round starts.

bookkeeping_login() {
    if [[ -z "$CODEX_BOOKKEEPING_LOGIN" ]]; then
        CODEX_BOOKKEEPING_LOGIN=$(gh api user | jq -r '.login // empty' 2>/dev/null || true)
        [[ -n "$CODEX_BOOKKEEPING_LOGIN" ]] \
            || echo "Warning: could not resolve the bookkeeping job's account; no fix round can be recognised." >&2
    fi
    echo "$CODEX_BOOKKEEPING_LOGIN"
}

fix_round_heads() {
    local pr_number="$1" login
    login=$(bookkeeping_login)
    [[ -n "$login" ]] || return 0
    gh api --paginate "repos/$REPO/issues/$pr_number/comments?per_page=100" \
        | jq -rs --arg login "$login" --arg marker "$BOOKKEEPING_FIX_MARKER" '
            [ add // [] | .[]
              | select((.user.login // "") == $login)
              | (.body // "") | split($marker)[1:][]
              | capture("^(?<head>[0-9a-fA-F]+)[ ]").head | ascii_downcase
            ] | unique | .[]' 2>/dev/null || true
}

# head_in_list <head> <newline-separated heads> — 0 when <head> is one of them.
head_in_list() {
    local head="${1,,}" line
    while IFS= read -r line; do
        [[ -n "$line" && "$line" == "$head" ]] && return 0
    done <<< "$2"
    return 1
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
                        updatedAt
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
                        updatedAt
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
    # Optional first arg: space-separated issue numbers to exclude (already handled).
    # Items already skipped as non-executable this run are always excluded
    # (issue #256), so no selection path can return the same one twice.
    local _fnrs_exclude="${1:-}${NON_EXECUTABLE_ISSUES:+ $NON_EXECUTABLE_ISSUES}"
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

# ─── Required board options (issue #293) ──────────────────────────────────────
# The hand-off and the resume entries depend on "In review" and "In merge".
# A board without one of them is a configuration error that stops the run
# before any session starts; it is not skipped with a warning.

for _required_status in "In review" "In merge"; do
    if [[ -z "$(get_status_option_id "$_required_status")" ]]; then
        echo "Error: the project board has no \"$_required_status\" Status option; the dispatcher cannot run without it." >&2
        exit 1
    fi
done

# ─── Executable issue types (issue #256) ──────────────────────────────────────
# Used by the resume pass below and by Step 1b.

EXECUTABLE_ISSUE_TYPES="story task bug"

# classify_issue_type <space-separated labels> <native issue type name>
# Prints the detected type ("type:story", or "native issue type 'Bug'") and
# returns 0 when executable. Otherwise prints why it is not and returns 1.
# The type: label wins; the native GitHub issue type is only a fallback when
# no type: label is present. Never infers type from the title or labels
# such as executor:.
classify_issue_type() {
    local labels="$1" native="$2"
    local lbl t
    local -a type_labels=()
    for lbl in $labels; do
        [[ "$lbl" == type:* ]] && type_labels+=("$lbl")
    done

    if [[ ${#type_labels[@]} -gt 1 ]]; then
        echo "unsupported type: conflicting type labels ${type_labels[*]}"
        return 1
    fi

    if [[ ${#type_labels[@]} -eq 1 ]]; then
        t="${type_labels[0]#type:}"
        if [[ " $EXECUTABLE_ISSUE_TYPES " == *" $t "* ]]; then
            echo "${type_labels[0]}"
            return 0
        fi
        case "$t" in
            epic|feature) echo "detected type: ${type_labels[0]}" ;;
            *)            echo "unsupported type: ${type_labels[0]}" ;;
        esac
        return 1
    fi

    if [[ -n "$native" ]]; then
        t=$(echo "$native" | tr '[:upper:]' '[:lower:]')
        if [[ " $EXECUTABLE_ISSUE_TYPES " == *" $t "* ]]; then
            echo "native issue type '$native'"
            return 0
        fi
        echo "unsupported type: native issue type '$native'"
        return 1
    fi

    echo "type missing: no type: label or native issue type"
    return 1
}

# ─── Utility: board facts from the current $BOARD_DATA snapshot ──────────────

board_status_of() {
    local issue_num="$1"
    echo "$BOARD_DATA" | jq -r --argjson n "$issue_num" '
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select((.content | type) == "object" and .content.number == $n)
        | [.fieldValues.nodes[] | select((.field.name? // "") == "Status") | .name][0] // empty' | head -1
}

board_items_in_status() {
    local status_name="$1"
    echo "$BOARD_DATA" | jq -c --arg s "$status_name" '[
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select(
            (.content | type) == "object" and
            (.content.number != null) and
            ([ .fieldValues.nodes[]
               | select((.field.name? // "") == "Status" and (.name? // "") == $s)
             ] | length > 0)
          )
        | { id: .id, number: .content.number, title: .content.title }
    ]'
}

# ─── Utility: /merge and /cleanup, shared by Steps E3/E4 and the resume pass ─
# One merge attempt per PR head (issue #293): when /merge fails, the mid-cycle
# blocker records the head it was tried on. The story stays "In merge", and
# the resume pass skips it until the PR has a new head or the PO posts a
# standalone `/unblock` line after the blocker. Without this a blocked merge
# would start a model session on every poll.

# /merge and /cleanup start no model session (issue #264): the scripts decide
# from GitHub and git facts alone, so there is no allowance check, no executor
# budget increment and no executor tier. Their step-by-step parity with
# .claude/commands/merge.md and cleanup.md is in
# docs/DISPATCHER-MERGE-CLEANUP-MAP.md.
run_phase_script() {
    local script="$1" label="$2"
    shift 2
    if $DRY_RUN; then
        echo "[DRY RUN] Would run $(basename "$script") $* --repo $REPO ($label, no model session)"
        return 0
    fi
    bash "$script" "$@" --repo "$REPO"
}

# Merge ownership evidence (docs/MERGE-POLICY.md, issue #264): right after the
# dispatcher's own /implement session, the PR carries a marker naming the head
# that session produced. scripts/dispatcher-merge.sh merges only a head with
# this marker on a comment of the dispatcher's own account, so a head pushed by
# anyone else (a manual /implement, a human) is never merged by the dispatcher.
merge_owner_marker() {
    dispatcher_marker "action=merge-owner pr=$1 head=$2"
}

record_merge_owner() {
    local issue_num="$1" head_before="${2:-}" pr_json pr_number head_sha
    pr_json=$(find_linked_pr_json "$issue_num" 2>/dev/null) || pr_json=""
    pr_number=$(jq -r '.number // empty' <<< "$pr_json" 2>/dev/null)
    head_sha=$(jq -r '.head.sha // empty' <<< "$pr_json" 2>/dev/null)
    if [[ -z "$pr_number" || -z "$head_sha" ]]; then
        echo "Warning: no linked PR head for #$issue_num; merge ownership not recorded (the merge will block)." >&2
        return 0
    fi
    if [[ -n "$head_before" && "$head_sha" == "$head_before" ]]; then
        echo "Note: /implement pushed no new head on PR #$pr_number; merge ownership not recorded for $head_sha." >&2
        return 0
    fi
    local marker attempt
    marker=$(merge_owner_marker "$pr_number" "$head_sha")
    for attempt in 1 2 3; do
        post_comment "$pr_number" "$marker" && return 0
        sleep "${COMMENT_RETRY_DELAY:-2}"
    done
    # Issue #348: leave the marker as an evidence-pending comment, so a later run
    # posts it for exactly this head before the merge (ensure_merge_owner).
    echo "Warning: could not record merge ownership on PR #$pr_number; recording it as pending for head ${head_sha:0:7}." >&2
    local pending
    pending=$(evidence_pending_comment "merge-owner" "$head_sha" "$marker")
    for attempt in 1 2 3; do
        post_comment "$pr_number" "$pending" && return 0
        sleep "${COMMENT_RETRY_DELAY:-2}"
    done
    # Neither form is on GitHub, so no later run could tell that this head is
    # the session's own. Fail the run (an unexpected exit, so the dispatcher
    # sends its dispatcher_error) instead of advancing without ownership.
    echo "Error: could not record merge ownership or its pending copy on PR #$pr_number; stopping before the hand-off so the story does not advance without it." >&2
    return 1
}

# ensure_merge_owner <pr> <head> — before a merge: posts a pending owner marker
# for exactly this head (issue #348). 0: go on to the merge script, which still
# decides; 1: the marker is still missing, do not merge in this run.
ensure_merge_owner() {
    local pr_number="$1" head_sha="$2"
    [[ -n "$head_sha" ]] || return 0
    evidence_retry "$pr_number" "merge-owner" "pr$pr_number-${head_sha:0:7}" "$ISSUE_NUMBER" "$TARGET_TITLE" "$head_sha"
    [[ $? -eq 0 ]]
}

# settle_merge_confirmation <pr> — after a completed merge: posts a pending
# confirmation once. 0: the confirmation is on the PR (or nothing is pending),
# cleanup may follow; 1: it is still missing. The story then stays "In merge":
# the next run retries it, and after the give-up blocker it waits for the PO.
settle_merge_confirmation() {
    local pr_number="$1" rc own gave
    evidence_retry "$pr_number" "merge-confirmation" "pr$pr_number" "$ISSUE_NUMBER" "$TARGET_TITLE"
    rc=$?
    [[ $rc -eq 0 ]] || return "$rc"
    # No pending comment does not prove the confirmation exists: the merge
    # script can fail after the merge before it wrote either (for example when
    # the summary cannot be generated). Require the dispatcher's own
    # confirmation (its exact `Merge gate:` line) on the PR.
    if own=$(bot_comments_json "$pr_number") \
        && jq -e 'any(.[]; ((.body // "") | contains("action=evidence-pending") | not)
                           and ((.body // "") | test("(^|\n)Merge gate:")))' <<< "$own" >/dev/null; then
        return 0
    fi
    # The PO accepted the gap: the give-up blocker was posted and then unblocked.
    gave=$(evidence_gave_up_marker "merge-confirmation-missing" "pr$pr_number")
    if own=$(bot_comments_json "$ISSUE_NUMBER") \
        && jq -e --arg g "$gave" 'any(.[]; (.body // "") | contains($g))' <<< "$own" >/dev/null \
        && ! marker_holds "$ISSUE_NUMBER" "$gave"; then
        echo "${LOG_PREFIX}[RESUME] #$ISSUE_NUMBER: the missing merge confirmation on PR #$pr_number was waived with /unblock"
        return 0
    fi
    # The give-up blocker stands: no further failure is recorded until /unblock.
    if marker_holds "$ISSUE_NUMBER" "$gave"; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$ISSUE_NUMBER: PR #$pr_number is merged but carries no merge confirmation from the dispatcher; retrying stopped after $EVIDENCE_RETRY_MAX runs, waiting for a PO /unblock line"
        return 2
    fi
    echo "${LOG_PREFIX}[RESUME SKIP] #$ISSUE_NUMBER: PR #$pr_number is merged but carries no merge confirmation from the dispatcher; cleanup waits" >&2
    evidence_record_failure "$ISSUE_NUMBER" "$TARGET_TITLE" "merge-confirmation-missing" "pr$pr_number" \
        "the merge confirmation on PR #$pr_number (no confirmation and no pending text exist)"
    rc=$?
    return $(( rc == 0 ? 1 : rc ))
}

# evidence_is_pending <target> <item> — 0 when the dispatcher's own account left
# an evidence-pending comment for <item> on <target>.
evidence_is_pending() {
    local own
    own=$(bot_comments_json "$1") || return 1
    jq -e --arg m "action=evidence-pending item=$2 " \
        'any(.[]; (.body // "") | contains($m))' <<< "$own" >/dev/null
}

pr_is_merged() {
    [[ "$(gh api "repos/$REPO/pulls/$1" --jq '(.merged_at != null) | tostring' 2>/dev/null)" == "true" ]]
}

merge_attempt_marker() {
    dispatcher_marker "action=merge-attempt pr=$1 head=$2"
}

run_merge_or_block() {
    local pr_number="$1" head_sha="$2" marker=""
    [[ -n "$head_sha" ]] && marker=$(merge_attempt_marker "$pr_number" "$head_sha")
    LAST_ACTION="running the merge script for PR #$pr_number"
    if ! run_phase_script "$MERGE_SCRIPT" "/merge" --pr "$pr_number"; then
        # The script exits 1 after a completed merge only when the confirmation
        # comment was not posted. That is not a merge blocker (#348): the
        # evidence-pending comment it left is retried by settle_merge_confirmation.
        # Only when the script left the pending comment: a failure before it
        # (for example the summary generation) must still block.
        if pr_is_merged "$pr_number" && evidence_is_pending "$pr_number" "merge-confirmation"; then
            echo "${LOG_PREFIX}[RESUME] #$ISSUE_NUMBER: PR #$pr_number is merged but its confirmation comment was not posted; retried on a later run"
            return 0
        fi
        post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "merge" "$marker"
        DISPATCH_HANDLED=true
        exit 1
    fi
    echo "✓ /merge completed for PR #$pr_number"
}

# cleanup_evidence_recorded <issue> — 0 when the dispatcher's own account left
# the cleanup closing summary on the story, or left its pending copy and the
# story carries the label that leads a later run back to it.
cleanup_evidence_recorded() {
    local own
    own=$(bot_comments_json "$1") || return 1
    if jq -e 'any(.[]; (.body // "") | test("(^|\n)Command: cleanup\n"))' <<< "$own" >/dev/null; then
        return 0
    fi
    jq -e 'any(.[]; (.body // "") | contains("action=evidence-pending item=cleanup-summary "))' <<< "$own" >/dev/null \
        && label_pending_cleanup "$1"
}

finish_story() {
    LAST_ACTION="invoking /cleanup for #$ISSUE_NUMBER"
    echo ""
    echo "── E4: /cleanup for #$ISSUE_NUMBER ─────────────────────────────────────"
    set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "Done"
    # Cleanup failure is non-fatal: story is already Done; log and continue to loop.
    if ! run_phase_script "$CLEANUP_SCRIPT" "/cleanup" --issue "$ISSUE_NUMBER"; then
        echo "Warning: /cleanup failed for #$ISSUE_NUMBER; story is Done but cleanup did not complete." >&2
        # Issue #348: when neither the closing summary nor its labelled pending
        # text is on the story, nothing leads a later run back to it. Put the
        # story back to "In merge" (the merged-PR resume reruns cleanup) until
        # the give-up limit.
        if ! cleanup_evidence_recorded "$ISSUE_NUMBER"; then
            local rc=0
            evidence_record_failure "$ISSUE_NUMBER" "$TARGET_TITLE" "cleanup-summary-unrecorded" "issue$ISSUE_NUMBER" \
                "the cleanup closing summary on #$ISSUE_NUMBER (neither it nor a pending copy could be posted)" || rc=$?
            if [[ $rc -eq 1 ]]; then
                echo "${LOG_PREFIX}[RESUME] #$ISSUE_NUMBER: the cleanup closing summary is not on GitHub; back to \"In merge\" so the next run reruns cleanup" >&2
                set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "In merge"
                return 0
            fi
        fi
    fi
    echo "✓ Story #$ISSUE_NUMBER completed (Done)."

    # Feature/epic closure check (issue #179)
    LAST_ACTION="feature/epic closure check after #$ISSUE_NUMBER Done"
    notify_closure_if_complete "$ISSUE_NUMBER" || true
}

# ─── Utility: hand a story to the Codex review loop after /implement (#293) ──
# For a story whose executor has a CODEX_REVIEW_LOOP_ROUTING row. The
# dispatcher starts no /review session, adds nothing to a review budget and
# posts no review request (/implement posts it, issue #292). It only makes
# sure the story is where the bookkeeping job expects it:
#   - no linked open PR, or a draft PR: mid-cycle blocker for /implement, the
#     story stays "In implementation", exit 1;
#   - "In review" is written only when the story is still "In implementation"
#     and no fix round is due. It is never written over "In review" or
#     "In merge": Codex may have answered, and the bookkeeping job acted,
#     before this point;
#   - a fix round that is due on the head the PR already had before the
#     session means the session pushed no commit. The blocker records that
#     head, so the same findings do not start a session on every poll. This
#     holds whatever status the session left the story in;
#   - a fix round that is due on a new head means Codex answered, and the
#     bookkeeping job acted, while the session was still running. Nothing is
#     written and nothing is recorded: the next run starts that fix round.
#
# Argument: the PR head before the /implement session, or empty when the story
# had no open PR then.

fix_round_attempt_marker() {
    dispatcher_marker "action=fix-round-attempt pr=$1 head=$2"
}

# A pending hand-off is matched by PR, not by head: a commit may land between
# the failed write and the run that retries it. It is cancelled by the "done"
# marker the retry posts, so it is completed once.
hand_off_pending_marker() {
    dispatcher_marker "action=hand-off-pending pr=$1 head=$2"
}

hand_off_pending_prefix() {
    echo "<!-- hpw:dispatcher action=hand-off-pending pr=$1 "
}

hand_off_done_marker() {
    dispatcher_marker "action=hand-off-done pr=$1"
}

# set_project_status_checked — like set_project_status(), but returns 1 when
# the write did not happen, so the caller can keep a retryable state.
set_project_status_checked() {
    local item_id="$1" issue_num="$2" status_name="$3" option_id
    option_id=$(get_status_option_id "$status_name")
    [[ -n "$item_id" && -n "$STATUS_FIELD_ID" && -n "$option_id" ]] || return 1
    if $DRY_RUN; then
        echo "[DRY RUN] Would set #$issue_num to \"$status_name\" on project board"
        return 0
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
      -f optionId="$option_id" >/dev/null
}

fix_round_due() {
    local pr_number="$1" head_sha="$2"
    local heads
    [[ -n "$head_sha" ]] || return 1
    heads=$(fix_round_heads "$pr_number")
    head_in_list "$head_sha" "$heads"
}

# record_pending_hand_off <pr> <head> — the session finished but the story is
# not "In review" and could not be put there. The bookkeeping job acts only on
# "In review", so this must not look like a finished hand-off: one recovery
# comment carries the pending marker, the run exits 1, and a later run
# completes the hand-off without a new session (complete_pending_hand_off()).
record_pending_hand_off() {
    local pr_number="$1" pr_head="$2"
    if ! marker_holds "$ISSUE_NUMBER" "$(hand_off_pending_prefix "$pr_number")" 0 "$(hand_off_done_marker "$pr_number")"; then
        post_comment "$ISSUE_NUMBER" "## Recovery Comment
Detected: /implement finished for #$ISSUE_NUMBER on PR #$pr_number, but the board status is not \"In review\" and could not be set.
Action: none yet. A later dispatcher run sets the status without starting /implement again; the Codex review loop acts only on a story in \"In review\".

$(hand_off_pending_marker "$pr_number" "$pr_head")"
    fi
    DISPATCH_HANDLED=true
    exit 1
}

# complete_pending_hand_off <issue> <item-id> <pr> <draft> — writes "In review"
# for a story whose earlier hand-off is still pending, and posts the "done"
# marker. Does nothing when no pending marker holds or the PR is a draft. A
# story it handled is not started as new work in this run; when the write
# fails again the story is also kept out of the next-story search.
HAND_OFF_RESUMED_ISSUES=""

complete_pending_hand_off() {
    local issue_num="$1" item_id="$2" pr_number="$3" pr_draft="$4"
    [[ "$pr_draft" != "true" ]] || return 0
    marker_holds "$issue_num" "$(hand_off_pending_prefix "$pr_number")" 1 "$(hand_off_done_marker "$pr_number")" || return 0

    echo "${LOG_PREFIX}[RESUME] #$issue_num: completing the hand-off on PR #$pr_number, setting \"In review\""
    HAND_OFF_RESUMED_ISSUES="${HAND_OFF_RESUMED_ISSUES:+$HAND_OFF_RESUMED_ISSUES }$issue_num"
    if set_project_status_checked "$item_id" "$issue_num" "In review"; then
        RESUME_SESSIONS=$(( RESUME_SESSIONS + 1 ))
        post_comment "$issue_num" "## Recovery Comment
Detected: a hand-off for #$issue_num on PR #$pr_number that could not set the board status earlier.
Action: status set to \"In review\".

$(hand_off_done_marker "$pr_number")" || true
    else
        echo "Warning: could not set #$issue_num to \"In review\"; the hand-off is retried on the next run." >&2
        NON_EXECUTABLE_ISSUES="${NON_EXECUTABLE_ISSUES:+$NON_EXECUTABLE_ISSUES }$issue_num"
    fi
    return 0
}

hand_off_to_codex_review_loop() {
    local head_before="${1:-}"
    local reviewer="${CODEX_REVIEW_LOOP_ROUTING[$EXECUTOR_LABEL]}"
    local pr_json pr_number pr_draft pr_head status
    LAST_ACTION="hand-off to the Codex review loop for #$ISSUE_NUMBER"
    pr_json=$(find_linked_pr_json "$ISSUE_NUMBER")
    pr_number=$(jq -r '.number // empty' <<< "$pr_json")
    pr_draft=$(jq -r '.draft // false' <<< "$pr_json")
    pr_head=$(jq -r '.head.sha // empty' <<< "$pr_json")

    if $DRY_RUN; then
        local pr_label="the PR /implement opens"
        [[ -n "$pr_number" ]] && pr_label="PR #$pr_number"
        echo "[DRY RUN] Would hand #$ISSUE_NUMBER to the Codex review loop ($reviewer) on $pr_label: no /review session; \"In review\" is written only if the story is still \"In implementation\" and no fix round is due"
        return 0
    fi

    if [[ -z "$pr_number" ]]; then
        echo "Error: No linked open PR found for #$ISSUE_NUMBER after /implement." >&2
        post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "implement"
        DISPATCH_HANDLED=true
        exit 1
    fi
    if [[ "$pr_draft" == "true" ]]; then
        if pr_conflicts_with_main "$pr_number"; then
            echo "Error: PR #$pr_number for #$ISSUE_NUMBER is still a draft after /implement: it conflicts with main, so no CI run can start." >&2
            post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "implement" "" \
                "PR #$pr_number conflicts with main (merge conflict with main); GitHub starts no CI run for a conflicting PR, so the required pytest check cannot run until it is resolved" \
                "the PR branch is merged with main and the conflict resolved"
        else
            echo "Error: PR #$pr_number for #$ISSUE_NUMBER is still a draft after /implement." >&2
            post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "implement"
        fi
        DISPATCH_HANDLED=true
        exit 1
    fi

    BOARD_DATA=$(fetch_board_data) || {
        echo "Error: Failed to re-read the project board for the hand-off of #$ISSUE_NUMBER." >&2; exit 1
    }
    status=$(board_status_of "$ISSUE_NUMBER")
    local due=false
    fix_round_due "$pr_number" "$pr_head" && due=true

    # Checked whatever the status is: a session that pushed nothing may still
    # have moved the story to "In review", and the bookkeeping job would then
    # move it back for the same head and start the same round again.
    if $due && [[ -n "$head_before" && "${head_before,,}" == "${pr_head,,}" ]]; then
        echo "Error: /implement for #$ISSUE_NUMBER pushed no new commit to PR #$pr_number; the findings recorded for head ${pr_head:0:7} are still open." >&2
        post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "implement" \
            "$(fix_round_attempt_marker "$pr_number" "$pr_head")"
        DISPATCH_HANDLED=true
        exit 1
    fi

    if [[ "$status" == "Ready for implementation" || "$status" == "Ready" ]]; then
        # Neither the dispatcher's own "In implementation" write nor the
        # session's status write took effect. Reporting a hand-off here would
        # let the next-story search pick this story again and start another
        # /implement session for work that is already on the PR.
        echo "Error: #$ISSUE_NUMBER is still \"$status\" after /implement; the board status did not advance." >&2
        record_pending_hand_off "$pr_number" "$pr_head"
    elif [[ "$status" != "In implementation" ]]; then
        echo "#$ISSUE_NUMBER is \"$status\" on the board; the dispatcher leaves it there."
    elif $due; then
        echo "#$ISSUE_NUMBER: findings are already recorded for the new head ${pr_head:0:7}; the story stays \"In implementation\" and the next run starts the fix round."
    elif ! set_project_status_checked "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "In review"; then
        echo "Error: could not set #$ISSUE_NUMBER to \"In review\" after /implement." >&2
        record_pending_hand_off "$pr_number" "$pr_head"
    fi
    echo "[HANDOFF] #$ISSUE_NUMBER: handed to the Codex review loop ($reviewer) on PR #$pr_number; no /review session started"
}

# ─── Resume pass (issue #293) ─────────────────────────────────────────────────
# Runs once at the start of every run, before new work, and decides everything
# from GitHub facts read now (board status, linked PR, comment markers).
# Order: stories "In merge", then due fix rounds, then hand-offs that are
# still pending.
#
# load_story_context sets the per-story globals the session helpers read
# (ISSUE_NUMBER, TARGET_*, EXECUTOR_LABEL). It returns 1 with
# STORY_SKIP_REASON set when the item is not an executable story with a known
# executor: label.

RESUME_SESSIONS=0
STORY_SKIP_REASON=""
STORY_EXECUTOR_LABEL_COUNT=0
LOG_PREFIX=""
$DRY_RUN && LOG_PREFIX="[DRY RUN] "

load_story_context() {
    local issue_num="$1" item_id="$2" detail lbl
    STORY_SKIP_REASON=""
    TARGET=$(gh api "repos/$REPO/issues/$issue_num") || {
        STORY_SKIP_REASON="the issue could not be read"; return 1
    }
    ISSUE_NUMBER="$issue_num"
    TARGET_ITEM_ID="$item_id"
    TARGET_TITLE=$(echo "$TARGET" | jq -r '.title')
    TARGET_BODY=$(echo "$TARGET" | jq -r '.body // ""')
    TARGET_LABELS=$(echo "$TARGET" | jq -r '[.labels[].name] | join(" ")')
    TARGET_NATIVE_TYPE=$(echo "$TARGET" | jq -r '.type.name? // ""')
    if ! detail=$(classify_issue_type "$TARGET_LABELS" "$TARGET_NATIVE_TYPE"); then
        STORY_SKIP_REASON="not an executable Story, Task, or Bug ($detail)"
        return 1
    fi
    EXECUTOR_LABEL=""
    STORY_EXECUTOR_LABEL_COUNT=0
    for lbl in $TARGET_LABELS; do
        if [[ "$lbl" == executor:* ]]; then
            [[ -z "$EXECUTOR_LABEL" ]] && EXECUTOR_LABEL="${lbl#executor:}"
            STORY_EXECUTOR_LABEL_COUNT=$(( STORY_EXECUTOR_LABEL_COUNT + 1 ))
        fi
    done
    if [[ -z "$EXECUTOR_LABEL" || -z "${EXECUTOR_ROUTING[$EXECUTOR_LABEL]+x}" ]]; then
        STORY_SKIP_REASON="no known executor: label"
        return 1
    fi
}

# A story "In merge", whatever its executor. The board status is not trusted
# on its own: the merge gates inside /merge decide whether the PR merges.
resume_in_merge_story() {
    local issue_num="$1" item_id="$2"
    local pr_json pr_number pr_draft pr_head merged_pr
    if ! load_story_context "$issue_num" "$item_id"; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: $STORY_SKIP_REASON" >&2
        return 0
    fi
    pr_json=$(find_linked_pr_json "$issue_num")
    pr_number=$(jq -r '.number // empty' <<< "$pr_json")
    pr_draft=$(jq -r '.draft // false' <<< "$pr_json")
    pr_head=$(jq -r '.head.sha // empty' <<< "$pr_json")

    if [[ -n "$pr_number" && "$pr_draft" != "true" ]]; then
        if [[ -n "$pr_head" ]] && marker_holds "$issue_num" "$(merge_attempt_marker "$pr_number" "$pr_head")"; then
            echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: /merge was already tried on PR #$pr_number at head ${pr_head:0:7}; waiting for a new head or a PO /unblock line"
            return 0
        fi
        if ! ensure_merge_owner "$pr_number" "$pr_head"; then
            echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: the merge owner marker for PR #$pr_number at head ${pr_head:0:7} is still missing; /merge waits for the retry"
            return 0
        fi
        echo ""
        echo "${LOG_PREFIX}[RESUME] #$issue_num: /merge PR #$pr_number"
        RESUME_SESSIONS=$(( RESUME_SESSIONS + 1 ))
        run_merge_or_block "$pr_number" "$pr_head"
        refresh_board_after_resumed_story
        settle_merge_confirmation "$pr_number" || return 0
        finish_story
        return 0
    fi

    if [[ -z "$pr_number" ]]; then
        merged_pr=$(find_merged_linked_pr "$issue_num")
        if [[ -n "$merged_pr" ]]; then
            echo ""
            echo "${LOG_PREFIX}[RESUME] #$issue_num: PR #$merged_pr is already merged; Done and /cleanup"
            RESUME_SESSIONS=$(( RESUME_SESSIONS + 1 ))
            refresh_board_after_resumed_story
            settle_merge_confirmation "$merged_pr" || return 0
            finish_story
            return 0
        fi
    fi

    echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: \"In merge\" with no open non-draft PR and no merged PR that closes it"
    post_blocker_once "$issue_num" "$TARGET_TITLE" "external" \
        "$(dispatcher_marker "action=in-merge-without-pr")" \
        "#$issue_num has a linked open PR that is ready for review, or its board status is corrected" \
        "none started by the dispatcher; the story is \"In merge\" with no open non-draft PR and no merged PR that closes it"
}

# A story "In implementation" for which the bookkeeping job recorded findings
# on the PR's current head. The story already holds its place within the WIP
# limit, so the WIP and file-overlap checks are not run against it again; the
# allowance check inside invoke_executor_command() still applies.
resume_fix_round() {
    local issue_num="$1" item_id="$2"
    local pr_json pr_number pr_draft pr_head heads round attempt_marker
    load_story_context "$issue_num" "$item_id" || return 0
    [[ "$STORY_EXECUTOR_LABEL_COUNT" -eq 1 && -n "${CODEX_REVIEW_LOOP_ROUTING[$EXECUTOR_LABEL]:-}" ]] || return 0

    pr_json=$(find_linked_pr_json "$issue_num")
    pr_number=$(jq -r '.number // empty' <<< "$pr_json")
    pr_draft=$(jq -r '.draft // false' <<< "$pr_json")
    pr_head=$(jq -r '.head.sha // empty' <<< "$pr_json")
    [[ -n "$pr_number" && -n "$pr_head" ]] || return 0

    heads=$(fix_round_heads "$pr_number")
    if ! head_in_list "$pr_head" "$heads"; then
        # No fix round is due. If an earlier hand-off could not write
        # "In review", finish it now; otherwise the bookkeeping job would
        # never act on Codex's answer.
        complete_pending_hand_off "$issue_num" "$item_id" "$pr_number" "$pr_draft"
        return 0
    fi

    # One marker per head: the count is the number of this round.
    round=$(wc -l <<< "$heads" | tr -d '[:space:]')
    if [[ "$round" -gt "$CODEX_FIX_ROUND_MAX" ]]; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: fix round $round on PR #$pr_number is above the maximum of $CODEX_FIX_ROUND_MAX; no session started"
        post_blocker_once "$issue_num" "$TARGET_TITLE" "resource" \
            "$(dispatcher_marker "action=fix-round-max pr=$pr_number")" \
            "the findings on PR #$pr_number are fixed by hand, or CODEX_FIX_ROUND_MAX is raised and the PO posts /unblock" \
            "PR #$pr_number has had $CODEX_FIX_ROUND_MAX automatic fix rounds and Codex still has findings on head ${pr_head:0:7}; branch and commits intact"
        return 0
    fi

    attempt_marker=$(fix_round_attempt_marker "$pr_number" "$pr_head")
    if marker_holds "$issue_num" "$attempt_marker"; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: the fix round was already tried on PR #$pr_number at head ${pr_head:0:7}; waiting for a new head or a PO /unblock line"
        return 0
    fi

    echo ""
    echo "${LOG_PREFIX}[RESUME] #$issue_num: fix round $round of $CODEX_FIX_ROUND_MAX, /implement on PR #$pr_number"
    RESUME_SESSIONS=$(( RESUME_SESSIONS + 1 ))
    LAST_ACTION="invoking /implement for #$issue_num (fix round $round)"
    # The allowance check must not put this story back to "Ready for
    # implementation": it is unfinished work that holds its place.
    FIX_ROUND_SESSION=true
    local session_rc=0
    invoke_executor_command "implement" "$issue_num" || session_rc=$?
    [[ $session_rc -eq $CLAUDE_LIMIT_STOP_RC ]] && pause_at_usage_limit "implement" "$issue_num" "$issue_num"
    if [[ $session_rc -ne 0 ]]; then
        check_and_notify_decision_blocker "$issue_num" "$TARGET_TITLE"
        post_mid_cycle_blocker "$issue_num" "$TARGET_TITLE" "implement" "$attempt_marker"
        DISPATCH_HANDLED=true
        exit 1
    fi
    FIX_ROUND_SESSION=false
    echo "✓ /implement completed for #$issue_num"
    record_merge_owner "$issue_num" "$pr_head" || exit 1
    hand_off_to_codex_review_loop "$pr_head"
}

# A story still "Ready for implementation" whose hand-off is pending: the
# status writes of its run failed. It is completed here, before new work, so
# it is not started a second time.
resume_pending_hand_off() {
    local issue_num="$1" item_id="$2"
    local pr_json pr_number
    load_story_context "$issue_num" "$item_id" || return 0
    [[ "$STORY_EXECUTOR_LABEL_COUNT" -eq 1 && -n "${CODEX_REVIEW_LOOP_ROUTING[$EXECUTOR_LABEL]:-}" ]] || return 0
    pr_json=$(find_linked_pr_json "$issue_num")
    pr_number=$(jq -r '.number // empty' <<< "$pr_json")
    [[ -n "$pr_number" ]] || return 0
    complete_pending_hand_off "$issue_num" "$item_id" "$pr_number" "$(jq -r '.draft // false' <<< "$pr_json")"
}

# A story "In review" whose executor has a REVIEW_EXECUTOR_ROUTING row (issue
# #315, F308.9): its PR was implemented outside the dispatcher, so no Codex
# review loop answers it. /review runs on the PR under the executor the table
# names, through invoke_executor_command(), so the usage limit and the
# allowance check apply. When /review leaves the story "In merge", the merge
# and cleanup scripts run as for any "In merge" story. When it leaves another
# status, the entry ends: fixes are made outside the dispatcher.
# One /review attempt per PR head: a failed /review records the head in its
# blocker, and a later run skips that head until the PR has a new head or the
# PO posts /unblock (same rule as "one merge attempt per head").
review_attempt_marker() {
    dispatcher_marker "action=review-attempt pr=$1 head=$2"
}

resume_review_story() {
    local issue_num="$1" item_id="$2"
    local review_label pr_json pr_number pr_draft pr_head marker status
    if ! load_story_context "$issue_num" "$item_id"; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: $STORY_SKIP_REASON" >&2
        return 0
    fi
    # A story that uses the Codex review loop waits for Codex Cloud: no session.
    [[ "$STORY_EXECUTOR_LABEL_COUNT" -eq 1 && -n "${CODEX_REVIEW_LOOP_ROUTING[$EXECUTOR_LABEL]:-}" ]] && return 0
    if [[ "$STORY_EXECUTOR_LABEL_COUNT" -ne 1 ]]; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: \"In review\" needs exactly one executor: label to pick a reviewer; it has $STORY_EXECUTOR_LABEL_COUNT"
        return 0
    fi
    review_label="${REVIEW_EXECUTOR_ROUTING[$EXECUTOR_LABEL]:-}"
    if [[ -z "$review_label" ]]; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: no review path for executor:$EXECUTOR_LABEL in REVIEW_EXECUTOR_ROUTING or CODEX_REVIEW_LOOP_ROUTING"
        return 0
    fi

    pr_json=$(find_linked_pr_json "$issue_num")
    pr_number=$(jq -r '.number // empty' <<< "$pr_json")
    pr_draft=$(jq -r '.draft // false' <<< "$pr_json")
    pr_head=$(jq -r '.head.sha // empty' <<< "$pr_json")
    if [[ -z "$pr_number" ]]; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: no linked open PR"
        return 0
    fi
    if [[ "$pr_draft" == "true" ]]; then
        echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: PR #$pr_number is a draft"
        return 0
    fi
    marker=""
    if [[ -n "$pr_head" ]]; then
        marker=$(review_attempt_marker "$pr_number" "$pr_head")
        if marker_holds "$issue_num" "$marker"; then
            echo "${LOG_PREFIX}[RESUME SKIP] #$issue_num: /review was already tried on PR #$pr_number head ${pr_head:0:7}; waiting for a new head or /unblock"
            return 0
        fi
    fi

    echo ""
    echo "${LOG_PREFIX}[RESUME] #$issue_num: /review on PR #$pr_number under $review_label"
    RESUME_SESSIONS=$(( RESUME_SESSIONS + 1 ))
    LAST_ACTION="invoking /review for #$issue_num"
    local session_rc=0
    invoke_executor_command "review" "$pr_number" "$review_label" || session_rc=$?
    [[ $session_rc -eq $CLAUDE_LIMIT_STOP_RC ]] && pause_at_usage_limit "review" "$pr_number" "$issue_num"
    if [[ $session_rc -ne 0 ]]; then
        check_and_notify_decision_blocker "$issue_num" "$TARGET_TITLE"
        post_mid_cycle_blocker "$issue_num" "$TARGET_TITLE" "review" "$marker"
        DISPATCH_HANDLED=true
        exit 1
    fi
    $DRY_RUN && return 0
    echo "✓ /review completed for PR #$pr_number"

    # Record the head as reviewed before anything else: when /review leaves the
    # story "In review" (a failed board write, a finding awaiting PO waiver), the
    # next poll must not review the same head again.
    if [[ -n "$marker" ]]; then
        post_comment "$issue_num" "Dispatcher: /review completed on PR #$pr_number at head ${pr_head:0:7}; no further /review runs on this head until the PR has a new head or the PO posts /unblock.

$marker" || echo "Warning: could not record the /review attempt for PR #$pr_number head ${pr_head:0:7}; the next run may review it again." >&2
    fi

    refresh_board_after_resumed_story
    status=$(board_status_of "$issue_num")
    if [[ "$status" == "In merge" ]]; then
        resume_in_merge_story "$issue_num" "$item_id"
        return 0
    fi
    echo "${LOG_PREFIX}[RESUME] #$issue_num: /review left the story in \"${status:-unknown}\"; no merge"
}

# The closure check of each finished story reads $BOARD_DATA. Re-read it after
# every story the resume pass finishes, so the last two stories of a feature
# finished in one pass see each other as Done.
refresh_board_after_resumed_story() {
    $DRY_RUN && return 0
    BOARD_DATA=$(fetch_board_data) || {
        echo "Error: Failed to fetch project board data." >&2; exit 1
    }
}

run_resume_pass() {
    local items count i
    retry_pending_cleanup_summaries
    items=$(board_items_in_status "In merge")
    count=$(jq 'length' <<< "$items")
    for ((i=0; i < count; i++)); do
        resume_in_merge_story "$(jq -r ".[$i].number" <<< "$items")" "$(jq -r ".[$i].id" <<< "$items")"
    done

    items=$(board_items_in_status "In implementation")
    count=$(jq 'length' <<< "$items")
    for ((i=0; i < count; i++)); do
        resume_fix_round "$(jq -r ".[$i].number" <<< "$items")" "$(jq -r ".[$i].id" <<< "$items")"
    done

    items=$(board_items_in_status "Ready for implementation")
    count=$(jq 'length' <<< "$items")
    for ((i=0; i < count; i++)); do
        resume_pending_hand_off "$(jq -r ".[$i].number" <<< "$items")" "$(jq -r ".[$i].id" <<< "$items")"
    done

    items=$(board_items_in_status "In review")
    count=$(jq 'length' <<< "$items")
    for ((i=0; i < count; i++)); do
        resume_review_story "$(jq -r ".[$i].number" <<< "$items")" "$(jq -r ".[$i].id" <<< "$items")"
    done
}

REQUESTED_ISSUE="$ISSUE_NUMBER"
if ! $RESUME_PASS_DONE; then
    LAST_ACTION="resume pass"
    echo "Checking for resumable work (\"In merge\", fix rounds, Codex-implemented PRs \"In review\")..."
    run_resume_pass
    RESUME_PASS_DONE=true
    ISSUE_NUMBER="$REQUESTED_ISSUE"
    if [[ $RESUME_SESSIONS -gt 0 && $DRY_RUN == false ]]; then
        echo "Re-fetching board after the resume pass..."
        BOARD_DATA=$(fetch_board_data) || {
            echo "Error: Failed to fetch project board data." >&2; exit 1
        }
    fi
fi

if $RESUME_ONLY; then
    echo "Resume pass finished ($RESUME_SESSIONS resumed). Dispatcher exiting cleanly."
    DISPATCH_HANDLED=true
    exit 0
fi

# A story whose pending hand-off the resume pass just handled already has its
# work on the PR. It is not started as new work in this run.
if [[ " $HAND_OFF_RESUMED_ISSUES " == *" $ISSUE_NUMBER "* ]]; then
    echo "#$ISSUE_NUMBER: its hand-off was handled by the resume pass; /implement is not started again. Dispatcher exiting cleanly."
    DISPATCH_HANDLED=true
    exit 0
fi

# ─── Utility: skip a story for this run and go on (issues #256, #315) ────────
# Adds the story to the run's skip list (carried across the recursive exec by
# --non-executable-issues), then continues with the next ready story or exits
# 0. It writes nothing: no board status, comment, blocker, Slack message,
# session count or allowance probe. It never returns.

skip_story_and_continue() {
    local skipped="$1" action="$2"
    NON_EXECUTABLE_ISSUES="${NON_EXECUTABLE_ISSUES:+$NON_EXECUTABLE_ISSUES }$skipped"
    LAST_ACTION="$action for #$skipped; searching for next story"
    NEXT_ISSUE=$(find_next_ready_story || true)
    if [[ -z "$NEXT_ISSUE" ]]; then
        echo "No more executable \"Ready for implementation\" stories after $action. Dispatcher exiting cleanly."
        DISPATCH_HANDLED=true
        exit 0
    fi
    echo "Found next story: #$NEXT_ISSUE — re-running full pre-flight + execution cycle."
    _NEXT_ARGS=(--repo "$REPO" --issue "$NEXT_ISSUE" --resume-pass-done \
        --non-executable-issues "$NON_EXECUTABLE_ISSUES")
    $DRY_RUN && _NEXT_ARGS+=(--dry-run)
    DISPATCH_HANDLED=true
    drain_actionable_events
    exec bash "$0" "${_NEXT_ARGS[@]}"
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
TARGET_NATIVE_TYPE=$(echo "$TARGET" | jq -r '.type.name? // ""')

# ─── Step 1b: Executable issue-type guard (issue #256) ───────────────────────
# Only Stories, Tasks, and Bugs are executable units for /implement. Anything
# else sitting in "Ready for implementation" (an Epic, a Feature, or an item
# with missing or unsupported type metadata) is skipped with one warning,
# excluded for the rest of this run, and selection continues. This runs before
# Step 2 so a non-executable item never reaches the executor-label check,
# which stays fatal for executable items.

LAST_ACTION="issue-type check for #$ISSUE_NUMBER"
if ! ISSUE_TYPE_DETAIL=$(classify_issue_type "$TARGET_LABELS" "$TARGET_NATIVE_TYPE"); then
    echo "Warning: #$ISSUE_NUMBER is not an executable Story, Task, or Bug ($ISSUE_TYPE_DETAIL); skipping it for this run and continuing selection." >&2
    skip_story_and_continue "$ISSUE_NUMBER" "non-executable skip"
fi
echo "Issue type: $ISSUE_TYPE_DETAIL (executable)"

# ─── Step 2: Executor label routing ──────────────────────────────────────────

# format_executor_hint — "executor:a, executor:b, ..." from EXECUTOR_ROUTING,
# sorted so the hint is stable across runs.
format_executor_hint() {
    printf '%s\n' "${!EXECUTOR_ROUTING[@]}" | sort \
        | awk '{ printf "%sexecutor:%s", (NR > 1 ? ", " : ""), $0 }'
}

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
    echo "       Add one of: $(format_executor_hint)" >&2
    exit 1
fi

ROUTING_ENTRY="${EXECUTOR_ROUTING[$EXECUTOR_LABEL]+x}"
if [[ -z "$ROUTING_ENTRY" ]]; then
    echo "Error: Unrecognised executor label 'executor:$EXECUTOR_LABEL' on #$ISSUE_NUMBER." >&2
    echo "       Known executors: $(printf 'executor:%s  ' "${!EXECUTOR_ROUTING[@]}")" >&2
    exit 1
fi

# An executor with no EXECUTOR_PROVIDER row is not started for /implement
# (issue #315, ADR-012): a table lookup, not a branch on the executor name.
if [[ -z "${EXECUTOR_PROVIDER[$EXECUTOR_LABEL]:-}" ]]; then
    echo "Warning: #$ISSUE_NUMBER has executor:$EXECUTOR_LABEL, which the dispatcher does not start for /implement (no subscription mechanism, ADR-012); skipping it for this run and continuing selection." >&2
    skip_story_and_continue "$ISSUE_NUMBER" "executor skip"
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
        body: (.content.body // ""), updatedAt: .content.updatedAt,
        projectUpdatedAt: .updatedAt }
]')

IN_IMPL_COUNT=$(echo "$IN_IMPL_ITEMS" | jq 'length')
echo "Stories in \"In implementation\": $IN_IMPL_COUNT"

# ─── Step 4: Comment-triggered resume, then stale detection and recovery ──────
# Per docs/PROJECT-STATUS.md Recovery Protocol:
#   Stale = In implementation + no activity >2h + no handoff note (both required)
# Per docs/PROJECT-STATUS.md Decision blockers / Resolution (issue #262):
#   A decision blocker resumes immediately once the PO posts a standalone
#   `/unblock` line after the blocker declaration — see
#   check_and_resolve_decision_blocker_comment() above, run first in the loop
#   below so it is not gated on STALE_THRESHOLD.

READY_FOR_IMPL_OPTION_ID=$(get_status_option_id "Ready for implementation")

# Return contract (issue #262 review): 0 means the board transition is
# confirmed done (or, under --dry-run, confirmed simulated) — safe for a
# caller to treat as "the story is really moving to Ready for
# implementation". 1 means the mutation did NOT happen (unresolved
# field/option IDs, or a failed GraphQL call) — a caller must NOT record
# any "this is resolved/consumed" state on a 1, or a transient/config
# failure would be indistinguishable from success and could permanently
# strand a story. Callers that only want best-effort board sync with no
# such downstream state (e.g. the stale-recovery loop below) should guard
# the call with `|| true` so a warning here does not abort the script
# under `set -euo pipefail`.
set_project_status_ready() {
    local item_id="$1"
    local issue_num="$2"
    if [[ -z "$STATUS_FIELD_ID" || -z "$READY_FOR_IMPL_OPTION_ID" ]]; then
        echo "Warning: Could not resolve Status field/option IDs; skipping board update for #$issue_num." >&2
        return 1
    fi
    if $DRY_RUN; then
        echo "[DRY RUN] Would set #$issue_num to \"Ready for implementation\" on project board"
        return 0
    fi
    if gh api graphql -f query='
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
      -f optionId="$READY_FOR_IMPL_OPTION_ID" >/dev/null; then
        return 0
    fi
    echo "Warning: Board status update failed for #$issue_num; manual update required." >&2
    return 1
}

LAST_ACTION="stale story detection"
STALE_COUNT=0
RESUME_COUNT=0
for ((i=0; i < IN_IMPL_COUNT; i++)); do
    item=$(echo "$IN_IMPL_ITEMS" | jq ".[$i]")
    item_id=$(echo "$item" | jq -r '.id')
    issue_num=$(echo "$item" | jq -r '.number')
    issue_title=$(echo "$item" | jq -r '.title')
    updated_at=$(echo "$item" | jq -r '.updatedAt')
    project_updated_at=$(echo "$item" | jq -r '.projectUpdatedAt')

    # ── Comment-triggered resume for decision blockers (issue #262) ──────────
    # Runs before the time-based staleness check below: a PO's resolving
    # comment must resume the story immediately rather than wait out
    # STALE_THRESHOLD. Once resumed, this story is no longer "In
    # implementation", so the staleness check below is skipped for it.
    if check_and_resolve_decision_blocker_comment "$issue_num" "$item_id" "$project_updated_at"; then
        RESUME_COUNT=$(( RESUME_COUNT + 1 ))
        continue
    fi

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

    # A story the dispatcher itself stopped at a fix-round bound (issue #293)
    # is waiting for the PO, not stale. Rolling it back would start /implement
    # again as a ready story and pass the bound.
    if [[ -n "$pr_number" ]]; then
        pr_head_sha=$(echo "$linked_pr" | jq -r '.head.sha // empty')
        if marker_holds "$issue_num" "$(dispatcher_marker "action=fix-round-max pr=$pr_number")" 1 \
           || { [[ -n "$pr_head_sha" ]] \
                && marker_holds "$issue_num" "$(fix_round_attempt_marker "$pr_number" "$pr_head_sha")" 1; }; then
            echo "#$issue_num is held by a fix-round blocker; not treating it as stale."
            continue
        fi
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
    set_project_status_ready "$item_id" "$issue_num" || true

    STALE_COUNT=$(( STALE_COUNT + 1 ))
done

# Recalculate active (non-stale) WIP count after recovery rollbacks and
# comment-triggered resumes (issue #262)
# Re-query the board to get updated state
if [[ $(( STALE_COUNT + RESUME_COUNT )) -gt 0 && $DRY_RUN == false ]]; then
    echo "Re-fetching board after stale recovery / comment-triggered resume..."
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
# Policy: at most WIP_LIMIT_IMPLEMENTATION parallel stories In implementation
# (docs/PROJECT-STATUS.md, docs/DISPATCHER-CONFIG.md)

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

# ─── Utility: go on to the next Ready for implementation story ───────────────
# Called when this story's run ends: after E4, and after the hand-off to the
# Codex review loop (issue #293). It never returns: it either exits 0 or
# replaces this process with the next story's run.

continue_with_next_ready_story() {
    LAST_ACTION="searching for next Ready for implementation story"
    echo ""
    echo "Searching for next \"Ready for implementation\" story..."
    NEXT_ISSUE=$(find_next_ready_story || true)

    if [[ -z "$NEXT_ISSUE" ]]; then
        echo "No more \"Ready for implementation\" stories. Dispatcher exiting cleanly."
        DISPATCH_HANDLED=true
        exit 0
    fi

    echo "Found next story: #$NEXT_ISSUE — re-running full pre-flight + execution cycle."

    _NEXT_ARGS=(--repo "$REPO" --issue "$NEXT_ISSUE" --resume-pass-done)
    [[ -n "$NON_EXECUTABLE_ISSUES" ]] && _NEXT_ARGS+=(--non-executable-issues "$NON_EXECUTABLE_ISSUES")
    $DRY_RUN && _NEXT_ARGS+=(--dry-run)

    if $DRY_RUN; then
        echo "[DRY RUN] Would exec: bash $0 ${_NEXT_ARGS[*]}"
        DISPATCH_HANDLED=true
        exit 0
    fi

    DISPATCH_HANDLED=true
    drain_actionable_events
    exec bash "$0" "${_NEXT_ARGS[@]}"
}

# ─── Step E1: /implement ──────────────────────────────────────────────────────

LAST_ACTION="invoking /implement for #$ISSUE_NUMBER"
echo ""
echo "── E1: /implement #$ISSUE_NUMBER ───────────────────────────────────────"
set_project_status "$TARGET_ITEM_ID" "$ISSUE_NUMBER" "In implementation"
# The PR head before the session, if the story already has an open PR: the
# hand-off uses it to tell "pushed nothing" from "Codex already answered".
HEAD_BEFORE_IMPLEMENT=$(find_linked_pr_json "$ISSUE_NUMBER" | jq -r '.head.sha // empty')
SESSION_RC=0
invoke_executor_command "implement" "$ISSUE_NUMBER" || SESSION_RC=$?
[[ $SESSION_RC -eq $CLAUDE_LIMIT_STOP_RC ]] && pause_at_usage_limit "implement" "$ISSUE_NUMBER" "$ISSUE_NUMBER"
if [[ $SESSION_RC -ne 0 ]]; then
    check_and_notify_decision_blocker "$ISSUE_NUMBER" "$TARGET_TITLE"
    post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "implement"
    DISPATCH_HANDLED=true
    exit 1
fi
echo "✓ /implement completed for #$ISSUE_NUMBER"
record_merge_owner "$ISSUE_NUMBER" "$HEAD_BEFORE_IMPLEMENT" || exit 1

# ─── Review path (issue #293) ─────────────────────────────────────────────────
# Table lookup, not a branch on an executor name: a story whose executor has a
# CODEX_REVIEW_LOOP_ROUTING row is handed to the Codex review loop and this
# run goes on to the next ready story. Every other story has no review path.

if [[ -n "${CODEX_REVIEW_LOOP_ROUTING[$EXECUTOR_LABEL]:-}" ]]; then
    echo ""
    echo "── Hand-off: #$ISSUE_NUMBER to the Codex review loop ───────────────────"
    hand_off_to_codex_review_loop "$HEAD_BEFORE_IMPLEMENT"
    continue_with_next_ready_story
fi

# ─── No review path ───────────────────────────────────────────────────────────
# An executor in neither review table has no review step: stop with a mid-cycle
# blocker so a typo or a new tier never skips review silently. The review of a
# story with a REVIEW_EXECUTOR_ROUTING row (a Codex-implemented PR) is a resume
# entry, not a step of this run (issue #315).

LAST_ACTION="review path for #$ISSUE_NUMBER"
echo "Error: No review path for executor 'executor:$EXECUTOR_LABEL' in REVIEW_EXECUTOR_ROUTING or CODEX_REVIEW_LOOP_ROUTING." >&2
post_mid_cycle_blocker "$ISSUE_NUMBER" "$TARGET_TITLE" "review"
DISPATCH_HANDLED=true
exit 1
}; exit $?
