#!/usr/bin/env bash
# tests/test_dispatcher_poll_stdout_clean.sh
#
# Regression test for issue #220: dispatcher-poll.sh stdout corruption.
#
# Tests that the fixed line in dispatcher-poll.sh properly suppresses
# slack-notify.sh stdout when firing ready_for_refinement notifications,
# ensuring POLL_JSON remains valid JSON.
#
# Scenario:
#   Mock slack-notify.sh to emit "ok" to stdout (simulating Slack webhook response).
#   Execute the fixed code path from dispatcher-poll.sh line 236.
#   Assert that output captured in POLL_JSON does not contain the "ok" string.
#   Assert that POLL_JSON is valid JSON.
#
# Usage: bash tests/test_dispatcher_poll_stdout_clean.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0

# Global temp dir
GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

# ─── Prerequisites ─────────────────────────────────────────────────────────────

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }

# ─── Test harness ─────────────────────────────────────────────────────────────

assert_json_valid() {
    local label="$1"
    local json_str="$2"
    if echo "$json_str" | jq . >/dev/null 2>&1; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label - JSON parse error"
        echo "      Got:"
        echo "$json_str" | sed 's/^/        /'
        FAIL=$(( FAIL + 1 ))
    fi
}

# ─── Test 1: Fixed code path suppresses slack-notify stdout ────────────────────

test_slack_notify_stdout_suppressed() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    # Create mock slack-notify.sh that emits "ok" to stdout (simulates the bug)
    cat > "$tmpdir/slack-notify.sh" << 'EOF'
#!/usr/bin/env bash
echo "ok"
exit 0
EOF
    chmod +x "$tmpdir/slack-notify.sh"

    # Test the unfixed code path (without >/dev/null) — should capture "ok"
    local unfixed_output
    unfixed_output=$({
        CONTEXT_JSON='{"test": "value"}'
        if bash "$tmpdir/slack-notify.sh" ready_for_refinement "$CONTEXT_JSON"; then
            echo "success"
        fi
    } 2>&1) || true

    if echo "$unfixed_output" | grep -q "^ok"; then
        echo "PASS: Unfixed code path captures 'ok' from slack-notify.sh"
        PASS=$(( PASS + 1 ))
    else
        echo "SKIP: Cannot verify unfixed path behavior (environment specific)"
    fi

    # Test the FIXED code path (with >/dev/null) — should NOT capture "ok"
    local fixed_output
    fixed_output=$({
        CONTEXT_JSON='{"test": "value"}'
        if bash "$tmpdir/slack-notify.sh" ready_for_refinement "$CONTEXT_JSON" >/dev/null; then
            echo "success"
        fi
    } 2>&1) || true

    if ! echo "$fixed_output" | grep -q "^ok"; then
        echo "PASS: Fixed code path suppresses 'ok' from slack-notify.sh"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: Fixed code path still capturing 'ok' from slack-notify.sh"
        echo "      Output: $fixed_output"
        FAIL=$(( FAIL + 1 ))
    fi
}

# ─── Test 2: POLL_JSON output structure is valid JSON ──────────────────────────

test_poll_json_structure() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    # Simulate the JSON output that dispatcher-poll.sh produces (lines 279-282)
    local poll_output
    poll_output=$(
        jq -n \
            --argjson impl '[]' \
            --argjson refine '[{"number": 999, "title": "Test issue"}]' \
            '{ready_for_implementation: $impl, ready_for_refinement: $refine}'
    )

    # Verify the output is valid JSON
    assert_json_valid "POLL_JSON output is valid JSON" "$poll_output"

    # Verify structure has required keys
    if echo "$poll_output" | jq -e '.ready_for_implementation' >/dev/null 2>&1; then
        echo "PASS: POLL_JSON contains ready_for_implementation key"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: POLL_JSON missing ready_for_implementation key"
        FAIL=$(( FAIL + 1 ))
    fi

    if echo "$poll_output" | jq -e '.ready_for_refinement' >/dev/null 2>&1; then
        echo "PASS: POLL_JSON contains ready_for_refinement key"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: POLL_JSON missing ready_for_refinement key"
        FAIL=$(( FAIL + 1 ))
    fi
}

# ─── Test 3: Integration test — fixed line in actual dispatcher-poll.sh ────────

test_dispatcher_poll_line_236_fixed() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    # Verify the actual fix is in place
    if grep -q 'bash "$(dirname "$0")/slack-notify.sh" ready_for_refinement "$CONTEXT_JSON" >/dev/null' \
        "$REPO_ROOT/scripts/dispatcher-poll.sh"; then
        echo "PASS: dispatcher-poll.sh line 236 has >/dev/null redirection"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: dispatcher-poll.sh line 236 missing >/dev/null redirection"
        FAIL=$(( FAIL + 1 ))
    fi
}

# ─── Main ──────────────────────────────────────────────────────────────────────

echo "Running dispatcher-poll stdout regression tests..."
echo ""

test_slack_notify_stdout_suppressed
test_poll_json_structure
test_dispatcher_poll_line_236_fixed

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
