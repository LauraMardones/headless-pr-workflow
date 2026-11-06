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
#   HPW_BIN                       the hpw launcher (default scripts/hpw); tests set a fake
#
# State files (in the per-day actions/cache entry, see dispatcher.yml), read and
# written only through `hpw dispatcher state` (ADR-014 → Block 3, issue #434):
#   sessions  — "YYYY-MM-DD:<count>"; a file dated another day reads as 0
#               (the normal start of a new day). A malformed or unreadable file
#               starts no session: [USAGE LIMIT ERROR], exit 2.
#   notified  — "YYYY-MM-DD"; a missing, stale, malformed or unreadable file
#               means the notification is due again.
#
# Requires: bash 4+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${USAGE_LIMIT_STATE_DIR:-$REPO_ROOT/.dispatcher-usage}"
TODAY="${USAGE_LIMIT_TODAY:-$(date -u +%Y-%m-%d)}"

HPW_BIN="${HPW_BIN:-$SCRIPT_DIR/hpw}"

# state_block <operation> <args...> — Block 3 (`hpw dispatcher state`) on this
# script's state directory and day. Prints its JSON line; returns its exit code.
state_block() {
    USAGE_LIMIT_STATE_DIR="$STATE_DIR" USAGE_LIMIT_TODAY="$TODAY" "$HPW_BIN" dispatcher state "$@"
}

# state_write <name> <content> — Block 3 write through a temporary data file.
# Returns 1 and logs the block's error when the state was not written.
state_write() {
    local name="$1" content="$2" data out rc=0
    data=$(mktemp) || { echo "[USAGE LIMIT ERROR] cannot write $name state: no temporary file" >&2; return 1; }
    printf '%s\n' "$content" > "$data"
    out=$(state_block write --name "$name" --data-file "$data") || rc=$?
    rm -f "$data"
    if (( rc != 0 )); then
        echo "[USAGE LIMIT ERROR] cannot write $name state ($(jq -r '.error // "unknown error"' <<< "$out" 2>/dev/null || echo unknown error))" >&2
        return 1
    fi
}

# Prints the day's count. A missing file or one dated another day (the normal
# start of a new day) is 0. A malformed or unreadable file returns 1 with a
# [USAGE LIMIT ERROR] line: no session starts on a counter that cannot be trusted.
read_count() {
    local out rc=0 result
    out=$(state_block read --name usage-sessions) || rc=$?
    result=$(jq -r '.result // empty' <<< "$out" 2>/dev/null) || result=""
    case "$result" in
        ok)             jq -r '.data.count' <<< "$out" ;;
        missing|stale)  echo 0 ;;
        *)
            echo "[USAGE LIMIT ERROR] the session counter cannot be trusted ($(jq -r '.error // "no answer from the state block"' <<< "$out" 2>/dev/null || echo "no answer from the state block")) — starting nothing" >&2
            return 1 ;;
    esac
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
        count=$(read_count) || exit 2
        echo "$count $LIMIT"
        if (( count >= LIMIT )); then
            exit 1
        fi
        echo "[USAGE LIMIT] $count of $LIMIT sessions today — start" >&2
        ;;
    record)
        count=$(read_count) || exit 2
        state_write usage-sessions "$TODAY:$((count + 1))" || exit 1
        ;;
    status)
        limit=null
        if [[ "${USAGE_LIMIT_SESSIONS_PER_DAY-}" =~ ^[0-9]+$ ]]; then
            limit=$((10#$USAGE_LIMIT_SESSIONS_PER_DAY))
        fi
        count=$(read_count) || count=0  # a status line only informs; check and record refuse on a bad counter
        printf '{"sessions_today":%d,"limit":%s}\n' "$count" "$limit"
        ;;
    notify-due)
        # Due unless the block reads today's marker; any other result notifies again.
        out=$(state_block read --name usage-notified 2>/dev/null) || true
        [[ "$(jq -r '.result // empty' <<< "$out" 2>/dev/null)" != ok ]]
        ;;
    mark-notified)
        state_write usage-notified "$TODAY" || exit 1
        ;;
    *)
        echo "Usage: dispatcher-usage-limit.sh check|record|status|notify-due|mark-notified" >&2
        exit 2
        ;;
esac
