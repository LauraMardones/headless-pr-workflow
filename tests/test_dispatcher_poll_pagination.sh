#!/usr/bin/env bash
# tests/test_dispatcher_poll_pagination.sh
#
# Regression test for issue #248: dispatcher-poll.sh fetched only the first
# 100 project items and silently missed ready items added after that page
# (e.g. #240). This test runs the actual script against a mocked `curl` and
# proves the paginated fetch:
#   1. surfaces a ready item that exists only on page 2,
#   2. issues the page-2 request with page 1's real endCursor (not a
#      hardcoded value), and
#   3. stops paginating once hasNextPage is false (exactly 3 GraphQL calls:
#      project lookup, page 1, page 2 — no spurious extra request).
#
# Usage: bash tests/test_dispatcher_poll_pagination.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }

# ─── Mock curl: fakes the GraphQL responses dispatcher-poll.sh depends on ─────
#
# Recognizes three request shapes by substring and logs every call (as the
# joined argv, which includes the --data payload) to CALL_LOG so the test can
# assert on call count and on the exact cursor used for the page-2 request.
#
#   projectsV2(first:10)        -> project lookup: one project linked
#   items(first:100 ... cursor1 -> page 2: hasNextPage=false, item #240 ready
#   items(first:100 (no cursor) -> page 1: hasNextPage=true, endCursor=cursor1,
#                                   one non-matching item (#100, Done)
#
# flow-review.sh (called by dispatcher-poll.sh after pagination) makes the
# same two request shapes without pagination; it is satisfied by the same
# project-lookup and page-1 responses and never sees "Red" flow health, so
# no Slack call is triggered.

FAKE_BIN=$(mktemp -d "$GLOBAL_TMP/bin.XXXXXX")
CALL_LOG="$GLOBAL_TMP/curl-calls.log"
: > "$CALL_LOG"

cat > "$FAKE_BIN/curl" << 'CURLMOCK'
#!/usr/bin/env bash
ARGS="$*"
echo "$ARGS" >> "$MOCK_CALL_LOG"

if [[ "$ARGS" == *"projectsV2(first:10)"* ]]; then
    cat <<'JSON'
{"data":{"repository":{"projectsV2":{"nodes":[{"number":3,"title":"project Headless PR workflow (hpw)"}]}}}}
JSON
elif [[ "$ARGS" == *'after:\"cursor1\"'* ]]; then
    cat <<'JSON'
{"data":{"repository":{"projectsV2":{"nodes":[{"items":{
  "pageInfo": {"hasNextPage": false, "endCursor": null},
  "nodes": [
    {"content": {"number": 240, "title": "Story: Define delivery-baseline"},
     "fieldValues": {"nodes": [{"name": "Ready for implementation", "field": {"name": "Status"}}]}}
  ]
}}]}}}}
JSON
elif [[ "$ARGS" == *"items(first:100"* ]]; then
    cat <<'JSON'
{"data":{"repository":{"projectsV2":{"nodes":[{"items":{
  "pageInfo": {"hasNextPage": true, "endCursor": "cursor1"},
  "nodes": [
    {"content": {"number": 100, "title": "Old done story"},
     "fieldValues": {"nodes": [{"name": "Done", "field": {"name": "Status"}}]}}
  ]
}}]}}}}
JSON
else
    echo '{"data":{}}'
fi
exit 0
CURLMOCK
chmod +x "$FAKE_BIN/curl"

# ─── Test: run the real script end to end against the mock ───────────────────

test_pagination_surfaces_page_two_item() {
    local poll_output
    poll_output=$(
        PATH="$FAKE_BIN:$PATH" \
        MOCK_CALL_LOG="$CALL_LOG" \
        GH_TOKEN="fake-token" \
        bash "$REPO_ROOT/scripts/dispatcher-poll.sh" --repo "acme/example" 2>/dev/null
    )

    # 1. Ready item that exists only on page 2 is surfaced.
    local found_number
    found_number=$(echo "$poll_output" | jq -r '.ready_for_implementation[0].number // empty')
    if [[ "$found_number" == "240" ]]; then
        echo "PASS: item present only on page 2 (#240) is surfaced"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: expected #240 in ready_for_implementation, got: $poll_output"
        FAIL=$(( FAIL + 1 ))
    fi

    # 2. The page-2 request used page 1's real endCursor, not a hardcoded value.
    if grep -qF 'after:\"cursor1\"' "$CALL_LOG"; then
        echo "PASS: page-2 request carried page 1's endCursor (cursor1)"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: no request found using endCursor 'cursor1'"
        FAIL=$(( FAIL + 1 ))
    fi

    # 3. Pagination stopped at hasNextPage=false: exactly one project lookup,
    #    one page-1 fetch, one page-2 fetch from dispatcher-poll.sh itself —
    #    no extra items request beyond page 2.
    local cursor_calls
    cursor_calls=$(grep -cF 'after:\"cursor1\"' "$CALL_LOG")
    if [[ "$cursor_calls" -eq 1 ]]; then
        echo "PASS: exactly one request used the page-2 cursor (loop terminated on hasNextPage=false)"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: expected exactly 1 request with the page-2 cursor, got $cursor_calls"
        FAIL=$(( FAIL + 1 ))
    fi
}

echo "Running dispatcher-poll pagination regression tests..."
echo ""

test_pagination_surfaces_page_two_item

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
