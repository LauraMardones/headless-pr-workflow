#!/usr/bin/env bash
# scripts/dispatcher-usage-limit.sh
#
# Usage limit of the dispatcher (issue #320, #308): the PO sets how many
# autonomous sessions may start per UTC day in the repository variable
# USAGE_LIMIT_SESSIONS_PER_DAY. dispatcher-invoke.sh checks the limit before
# every session start and counts the session when it starts.
#
# Subcommands:
#   check          — exit 0 when the day's count is below the limit (start),
#                    exit 1 when count >= limit (refuse), exit 2 when the
#                    variable is missing or invalid. stdout: "<count> <limit>"
#                    on 0 and 1.
#   record         — add one to the day's count.
#   status         — print {"sessions_today":N,"limit":M} (limit null if invalid).
#   notify-due     — exit 0 when no notification was marked as sent today.
#   mark-notified  — write the day's notification marker (run only after the
#                    notification was sent without an error).
#
# Environment:
#   USAGE_LIMIT_SESSIONS_PER_DAY  whole number, zero or more (decimal; "08" is 8)
#   USAGE_LIMIT_STATE_DIR         optional, default <repo-root>/.dispatcher-usage
#   USAGE_LIMIT_TODAY             optional UTC date YYYY-MM-DD (test hook)
#
# State files (in the per-day actions/cache entry, see dispatcher.yml):
#   sessions  — "YYYY-MM-DD:<count>"; a file dated another day reads as 0
#   notified  — "YYYY-MM-DD"
#
# Requires: bash 4+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${USAGE_LIMIT_STATE_DIR:-$REPO_ROOT/.dispatcher-usage}"
TODAY="${USAGE_LIMIT_TODAY:-$(date -u +%Y-%m-%d)}"
SESSIONS_FILE="$STATE_DIR/sessions"
NOTIFIED_FILE="$STATE_DIR/notified"

# Prints the day's count; 0 when the file is absent, dated another day or unreadable.
read_count() {
    local d="" n=""
    [[ -f "$SESSIONS_FILE" ]] && IFS=: read -r d n < "$SESSIONS_FILE" || true
    if [[ "$d" == "$TODAY" && "$n" =~ ^[0-9]+$ ]]; then
        echo "$((10#$n))"
    else
        echo 0
    fi
}

# Sets LIMIT; returns 1 and logs [USAGE LIMIT ERROR] on a missing or invalid value.
read_limit() {
    local v="${USAGE_LIMIT_SESSIONS_PER_DAY-}"
    if [[ ! "$v" =~ ^[0-9]+$ ]]; then
        echo "[USAGE LIMIT ERROR] USAGE_LIMIT_SESSIONS_PER_DAY is not set or is not a whole number of zero or more (value: \"$v\") — starting nothing" >&2
        return 1
    fi
    LIMIT=$((10#$v))
}

case "${1:-}" in
    check)
        read_limit || exit 2
        count=$(read_count)
        echo "$count $LIMIT"
        if (( count >= LIMIT )); then
            exit 1
        fi
        echo "[USAGE LIMIT] $count of $LIMIT sessions today — start" >&2
        ;;
    record)
        mkdir -p "$STATE_DIR"
        printf '%s:%d\n' "$TODAY" "$(( $(read_count) + 1 ))" > "$SESSIONS_FILE"
        ;;
    status)
        limit=null
        if [[ "${USAGE_LIMIT_SESSIONS_PER_DAY-}" =~ ^[0-9]+$ ]]; then
            limit=$((10#$USAGE_LIMIT_SESSIONS_PER_DAY))
        fi
        printf '{"sessions_today":%d,"limit":%s}\n' "$(read_count)" "$limit"
        ;;
    notify-due)
        [[ "$(cat "$NOTIFIED_FILE" 2>/dev/null || true)" != "$TODAY" ]]
        ;;
    mark-notified)
        mkdir -p "$STATE_DIR"
        echo "$TODAY" > "$NOTIFIED_FILE"
        ;;
    *)
        echo "Usage: dispatcher-usage-limit.sh check|record|status|notify-due|mark-notified" >&2
        exit 2
        ;;
esac
