#!/usr/bin/env bash
# tests/test_dispatcher_invoke_argmax.sh
#
# Regression test: fetch_board_data() in dispatcher-invoke.sh must accumulate
# paginated project items without embedding the growing JSON blob as a shell
# command-line argument. Production hit this at ~130 real project items
# (each carrying a full issue body): `jq -c -n --argjson a "$all_items" ...`
# exceeded the OS argument-length limit and failed with
# "Argument list too long", breaking the run before /implement was ever
# invoked. This test forces the same failure mode deterministically by
# returning one large issue body on page 1, then confirms:
#   1. fetch_board_data() exits 0 (no "Argument list too long"),
#   2. the item from the large page 1 is present in the result, and
#   3. the item from page 2 is also present (aggregation still correct).
#
# Usage: bash tests/test_dispatcher_invoke_argmax.sh
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

# ─── Extract the real fetch_board_data() function body ────────────────────────

FUNC_FILE="$GLOBAL_TMP/fetch_board_data.sh"
awk '/^fetch_board_data\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT" > "$FUNC_FILE"

if ! grep -q "^fetch_board_data() {" "$FUNC_FILE"; then
    echo "FAIL: could not extract fetch_board_data() from $INVOKE_SCRIPT (function renamed or removed?)"
    exit 1
fi

# ─── Build a page-1 response whose accumulated size alone exceeds ARG_MAX ─────
# getconf ARG_MAX is typically ~2MB on Linux CI runners; 3MB of body content
# comfortably exceeds it regardless of environment overhead already consumed.

BIG_BODY_FILE="$GLOBAL_TMP/big_body.txt"
head -c 3000000 /dev/zero | tr '\0' 'x' > "$BIG_BODY_FILE"

PAGE1_FILE="$GLOBAL_TMP/page1.json"
jq -n --rawfile body "$BIG_BODY_FILE" '
{
  "data": {"repository": {"projectsV2": {"nodes": [{
    "id": "PVT_1",
    "fields": {"nodes": [{"id": "FIELD_STATUS", "name": "Status", "options": [{"id": "OPT1", "name": "Ready for implementation"}]}]},
    "items": {
      "pageInfo": {"hasNextPage": true, "endCursor": "cursor1"},
      "nodes": [{
        "id": "ITEM_100",
        "content": {"number": 100, "title": "Large story", "body": $body, "updatedAt": "2026-08-01T00:00:00Z"},
        "fieldValues": {"nodes": [{"name": "Ready for implementation", "field": {"name": "Status"}}]}
      }]
    }
  }]}}}
}' > "$PAGE1_FILE"

PAGE2_FILE="$GLOBAL_TMP/page2.json"
jq -n '
{
  "data": {"repository": {"projectsV2": {"nodes": [{
    "id": "PVT_1",
    "fields": {"nodes": []},
    "items": {
      "pageInfo": {"hasNextPage": false, "endCursor": null},
      "nodes": [{
        "id": "ITEM_240",
        "content": {"number": 240, "title": "Story: Define delivery-baseline", "body": "", "updatedAt": "2026-08-09T00:00:00Z"},
        "fieldValues": {"nodes": [{"name": "Ready for implementation", "field": {"name": "Status"}}]}
      }]
    }
  }]}}}
}' > "$PAGE2_FILE"

if ! jq -e . "$PAGE2_FILE" >/dev/null 2>&1; then
    echo "FAIL: test fixture page2.json is not valid JSON (test bug)"
    exit 1
fi
if ! jq -e . "$PAGE1_FILE" >/dev/null 2>&1; then
    echo "FAIL: test fixture page1.json is not valid JSON (test bug)"
    exit 1
fi

CALL_LOG="$GLOBAL_TMP/gh-calls.log"
: > "$CALL_LOG"

run_fetch_board_data() {
    bash -c '
        set -euo pipefail
        OWNER="acme"
        REPO_NAME="example"

        gh() {
            local args="$*"
            echo "called" >> "'"$CALL_LOG"'"
            if [[ "$args" == *"after=cursor1"* ]]; then
                cat "'"$PAGE2_FILE"'"
            else
                cat "'"$PAGE1_FILE"'"
            fi
        }

        source "'"$FUNC_FILE"'"
        fetch_board_data
    '
}

test_large_page_does_not_exceed_arg_max() {
    local board_data rc
    rc=0
    board_data=$(run_fetch_board_data) || rc=$?

    if [[ "$rc" -eq 0 ]]; then
        echo "PASS: fetch_board_data() exits 0 against a page whose accumulated size alone exceeds ARG_MAX"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: fetch_board_data() exited $rc (expected 0) — likely 'Argument list too long' regression"
        FAIL=$(( FAIL + 1 ))
        return
    fi

    local found_large found_small
    found_large=$(echo "$board_data" | jq -r '
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select(.content.number == 100) | .content.number' 2>/dev/null || true)
    found_small=$(echo "$board_data" | jq -r '
        .data.repository.projectsV2.nodes[0].items.nodes[]
        | select(.content.number == 240) | .content.number' 2>/dev/null || true)

    if [[ "$found_large" == "100" ]]; then
        echo "PASS: item with the oversized body (#100) is present in the result"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: expected #100 in board items"
        FAIL=$(( FAIL + 1 ))
    fi

    if [[ "$found_small" == "240" ]]; then
        echo "PASS: page 2 item (#240) is also present (aggregation still correct)"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: expected #240 in board items"
        FAIL=$(( FAIL + 1 ))
    fi
}

echo "Running dispatcher-invoke fetch_board_data ARG_MAX regression test..."
echo ""

test_large_page_does_not_exceed_arg_max

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
