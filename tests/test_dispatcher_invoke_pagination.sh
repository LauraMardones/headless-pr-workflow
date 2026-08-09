#!/usr/bin/env bash
# tests/test_dispatcher_invoke_pagination.sh
#
# Regression test for the fetch_board_data() helper in dispatcher-invoke.sh
# (same class of bug as issue #248 / dispatcher-poll.sh, but this helper
# backs the main board fetch, the stale-recovery re-fetch, and
# find_next_ready_story() in dispatcher-invoke.sh). Before this helper
# existed, each of those three queries was hard-capped at the first 100
# project items, so an item added after that page (e.g. #240) was silently
# invisible to get_project_item_id() and find_next_ready_story().
#
# This test extracts the real fetch_board_data() function body from
# scripts/dispatcher-invoke.sh (rather than reimplementing it) and runs it
# against a mocked `gh`, proving:
#   1. an item present only on page 2 is surfaced in the returned data,
#   2. the page-2 request carries page 1's real endCursor (not hardcoded),
#      passed as a proper GraphQL variable (-f after=...), and
#   3. pagination stops once hasNextPage is false (exactly one page-2 call).
#
# Usage: bash tests/test_dispatcher_invoke_pagination.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INVOKE_SCRIPT="$REPO_ROOT/scripts/dispatcher-invoke.sh"

PASS=0
FAIL=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }

# ─── Extract the real fetch_board_data() function body from the script ───────
# Sourcing the whole script is not viable here: it parses argv, requires
# --repo/--issue, and runs its full pre-flight/execution body at load time.
# Extracting just the function under test keeps this a real regression test
# against the shipped implementation rather than a hand-reimplementation of it.

FUNC_FILE="$GLOBAL_TMP/fetch_board_data.sh"
awk '/^fetch_board_data\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT" > "$FUNC_FILE"

if ! grep -q "^fetch_board_data() {" "$FUNC_FILE"; then
    echo "FAIL: could not extract fetch_board_data() from $INVOKE_SCRIPT (function renamed or removed?)"
    exit 1
fi

# ─── Mock gh: fakes the two graphql call shapes fetch_board_data() makes ─────
#
#   -f after=cursor1  -> page 2: hasNextPage=false, item #240 ready-for-impl
#   (no after arg)     -> page 1: hasNextPage=true, endCursor=cursor1,
#                         one non-matching item (#100, Done)
#
# Every call (joined argv) is logged to CALL_LOG so the test can assert on
# call count and on the exact cursor used for the page-2 request.

CALL_LOG="$GLOBAL_TMP/gh-calls.log"
: > "$CALL_LOG"

run_fetch_board_data() {
    bash -c '
        set -euo pipefail
        OWNER="acme"
        REPO_NAME="example"

        gh() {
            local args="$*"
            echo "$args" >> "'"$CALL_LOG"'"
            if [[ "$args" == *"after=cursor1"* ]]; then
                cat <<'"'"'JSON'"'"'
{"data":{"repository":{"projectsV2":{"nodes":[{"id":"PVT_1","fields":{"nodes":[]},"items":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[{"id":"ITEM_240","content":{"number":240,"title":"Story: Define delivery-baseline","body":"","updatedAt":"2026-08-09T00:00:00Z"},"fieldValues":{"nodes":[{"name":"Ready for implementation","field":{"name":"Status"}}]}}]}}]}}}}
JSON
            else
                cat <<'"'"'JSON'"'"'
{"data":{"repository":{"projectsV2":{"nodes":[{"id":"PVT_1","fields":{"nodes":[{"id":"FIELD_STATUS","name":"Status","options":[{"id":"OPT1","name":"Ready for implementation"}]}]},"items":{"pageInfo":{"hasNextPage":true,"endCursor":"cursor1"},"nodes":[{"id":"ITEM_100","content":{"number":100,"title":"Old done story","body":"","updatedAt":"2026-08-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"Done","field":{"name":"Status"}}]}}]}}]}}}}
JSON
            fi
        }

        source "'"$FUNC_FILE"'"
        fetch_board_data
    '
}

# ─── Test: run the real fetch_board_data() against the mock ──────────────────

test_pagination_surfaces_page_two_item() {
    local board_data
    board_data=$(run_fetch_board_data)

    # 1. Item present only on page 2 is surfaced.
    local found_number
    found_number=$(echo "$board_data" | jq -r '
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select(.content.number == 240) | .content.number')
    if [[ "$found_number" == "240" ]]; then
        echo "PASS: item present only on page 2 (#240) is surfaced"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: expected #240 in board items, got: $board_data"
        FAIL=$(( FAIL + 1 ))
    fi

    # 2. Page 1's item is also present (aggregation across pages worked).
    local page_one_number
    page_one_number=$(echo "$board_data" | jq -r '
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select(.content.number == 100) | .content.number')
    if [[ "$page_one_number" == "100" ]]; then
        echo "PASS: page 1 item (#100) is retained alongside page 2 item"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: expected #100 (from page 1) in aggregated board items, got: $board_data"
        FAIL=$(( FAIL + 1 ))
    fi

    # 3. The page-2 request used page 1's real endCursor, not a hardcoded value.
    if grep -qF 'after=cursor1' "$CALL_LOG"; then
        echo "PASS: page-2 request carried page 1's endCursor (cursor1)"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: no request found using endCursor 'cursor1'"
        FAIL=$(( FAIL + 1 ))
    fi

    # 4. Pagination stopped at hasNextPage=false: exactly one page-2 request.
    local cursor_calls
    cursor_calls=$(grep -cF 'after=cursor1' "$CALL_LOG")
    if [[ "$cursor_calls" -eq 1 ]]; then
        echo "PASS: exactly one request used the page-2 cursor (loop terminated on hasNextPage=false)"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: expected exactly 1 request with the page-2 cursor, got $cursor_calls"
        FAIL=$(( FAIL + 1 ))
    fi
}

echo "Running dispatcher-invoke fetch_board_data pagination regression tests..."
echo ""

test_pagination_surfaces_page_two_item

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
