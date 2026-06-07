#!/usr/bin/env bash
# scripts/dispatcher-budget.sh
#
# Daily token budget tracker for the dispatcher.
#
# Subcommands:
#   check <executor_type>                     — exit 0 if budget available, exit 1 if cap reached
#   increment <executor_type> <tokens>        — add tokens to the daily counter
#   estimate <size_label>                     — print estimated token cost for a story size label
#
# Counter files: .dispatcher-budget/budget-{executor_type}
# Override directory via BUDGET_COUNTER_DIR env var (test isolation).
#
# Exit codes for check:
#   0   — budget available (remaining > 0)
#   1   — cap reached (usage >= cap)
#   2   — configuration error (missing env var, unrecognised executor type, bad args)
#
# Exit codes for increment:
#   0   — counter updated
#   2   — bad arguments or I/O error
#
# Exit codes for estimate:
#   0   — printed token estimate
#   2   — bad arguments
#
# Implements: issue #202
# Requires: bash 4+

set -euo pipefail

# ─── Executor type → BUDGET_DAILY_* env var mapping ─────────────────────────

declare -A EXECUTOR_ENV_MAP=(
    [haiku]=BUDGET_DAILY_HAIKU
    [sonnet]=BUDGET_DAILY_SONNET
    [opus]=BUDGET_DAILY_OPUS
    [codex]=BUDGET_DAILY_CODEX
)

# ─── Token estimate constants (story size label → estimated tokens) ──────────
# Decided 2026-06-07, Option A — conservative estimates.
# Used by dispatcher-invoke.sh (Story #203) when calling increment.

declare -A TOKEN_ESTIMATE_MAP=(
    [size:small]=25000
    [small]=25000
    [size:medium]=75000
    [medium]=75000
    [size:large]=150000
    [large]=150000
)
TOKEN_ESTIMATE_DEFAULT=75000

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COUNTER_DIR="${BUDGET_COUNTER_DIR:-$REPO_ROOT/.dispatcher-budget}"

TODAY=$(date -u +%Y-%m-%d)

# ─── Helpers ─────────────────────────────────────────────────────────────────

usage() {
    cat >&2 <<'EOF'
Usage:
  bash scripts/dispatcher-budget.sh check <executor_type>
  bash scripts/dispatcher-budget.sh increment <executor_type> <estimated_tokens>
  bash scripts/dispatcher-budget.sh estimate <size_label>

Executor types: haiku, sonnet, opus, codex
Size labels:    size:small, size:medium, size:large (or small, medium, large)
EOF
}

validate_executor_type() {
    local executor_type="$1"
    if [[ -z "${EXECUTOR_ENV_MAP[$executor_type]+_}" ]]; then
        echo "Error: unrecognised executor type '$executor_type'. Valid types: haiku, sonnet, opus, codex" >&2
        exit 2
    fi
}

get_daily_cap() {
    local executor_type="$1"
    local env_var="${EXECUTOR_ENV_MAP[$executor_type]}"
    if [[ -z "${!env_var+_}" ]]; then
        echo "Error: environment variable $env_var is not set (required for executor type '$executor_type')" >&2
        exit 2
    fi
    local cap="${!env_var}"
    if [[ -z "$cap" ]]; then
        echo "Error: environment variable $env_var is empty (required for executor type '$executor_type')" >&2
        exit 2
    fi
    printf '%s\n' "$cap"
}

get_counter_file() {
    local executor_type="$1"
    printf '%s/budget-%s\n' "$COUNTER_DIR" "$executor_type"
}

get_current_usage() {
    local counter_file="$1"
    if [[ ! -f "$counter_file" ]]; then
        echo "0"
        return
    fi
    local stored_date stored_usage
    IFS=: read -r stored_date stored_usage < "$counter_file"
    if [[ "$stored_date" != "$TODAY" ]]; then
        echo "0"
    else
        echo "${stored_usage:-0}"
    fi
}

# ─── Subcommands ─────────────────────────────────────────────────────────────

cmd_check() {
    if [[ $# -lt 1 ]]; then
        echo "Error: 'check' requires <executor_type>" >&2
        usage
        exit 2
    fi
    local executor_type="$1"
    validate_executor_type "$executor_type"

    local cap
    cap=$(get_daily_cap "$executor_type")

    local counter_file
    counter_file=$(get_counter_file "$executor_type")

    local usage_count
    usage_count=$(get_current_usage "$counter_file")

    local remaining
    remaining=$(( cap - usage_count ))

    if [[ "$remaining" -le 0 ]]; then
        echo "0"
        exit 1
    fi

    echo "$remaining"
    exit 0
}

cmd_increment() {
    if [[ $# -lt 2 ]]; then
        echo "Error: 'increment' requires <executor_type> <estimated_tokens>" >&2
        usage
        exit 2
    fi
    local executor_type="$1"
    local estimated_tokens="$2"

    validate_executor_type "$executor_type"

    if ! [[ "$estimated_tokens" =~ ^[0-9]+$ ]]; then
        echo "Error: estimated_tokens must be a non-negative integer, got '$estimated_tokens'" >&2
        exit 2
    fi

    local counter_file
    counter_file=$(get_counter_file "$executor_type")

    mkdir -p "$COUNTER_DIR"

    local current_usage
    current_usage=$(get_current_usage "$counter_file")

    local new_usage
    new_usage=$(( current_usage + estimated_tokens ))

    printf '%s:%d\n' "$TODAY" "$new_usage" > "$counter_file"
    exit 0
}

cmd_estimate() {
    if [[ $# -lt 1 ]]; then
        echo "Error: 'estimate' requires <size_label>" >&2
        usage
        exit 2
    fi
    local size_label="$1"
    local tokens="${TOKEN_ESTIMATE_MAP[$size_label]+_}"
    if [[ -n "$tokens" ]]; then
        echo "${TOKEN_ESTIMATE_MAP[$size_label]}"
    else
        echo "$TOKEN_ESTIMATE_DEFAULT"
    fi
    exit 0
}

# ─── Main ────────────────────────────────────────────────────────────────────

if [[ $# -lt 1 ]]; then
    echo "Error: subcommand required (check|increment|estimate)" >&2
    usage
    exit 2
fi

SUBCOMMAND="$1"
shift

case "$SUBCOMMAND" in
    check)     cmd_check "$@" ;;
    increment) cmd_increment "$@" ;;
    estimate)  cmd_estimate "$@" ;;
    *)
        echo "Error: unknown subcommand '$SUBCOMMAND'" >&2
        usage
        exit 2
        ;;
esac
