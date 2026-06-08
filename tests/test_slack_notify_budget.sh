#!/usr/bin/env bash
# tests/test_slack_notify_budget.sh
#
# Tests for the budget_cap_reached event type in scripts/slack-notify.sh (issue #204).
#
# Scenarios covered:
#   1. budget_cap_reached with all four remaining values present — exits 0, curl called
#   2. budget_cap_reached with a missing field — defaults to "(unknown)" without crashing
#   3. budget_cap_reached is in KNOWN_EVENTS — unknown-event branch not triggered
#   4. Workflow condition: all_budget_blocked=true in invoke outputs — notification step runs
#   5. Workflow condition: all_budget_blocked absent/false — notification step is skipped
#
# Usage: bash tests/test_slack_notify_budget.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NOTIFY_SCRIPT="$REPO_ROOT/scripts/slack-notify.sh"

PASS=0
FAIL=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

# ─── Test harness ─────────────────────────────────────────────────────────────

assert_exit() {
    local label="$1"
    local expected_exit="$2"
    local actual_exit="$3"
    if [[ "$actual_exit" -eq "$expected_exit" ]]; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        echo "      Expected exit $expected_exit, got $actual_exit"
        FAIL=$(( FAIL + 1 ))
    fi
}

assert_contains() {
    local label="$1"
    local haystack="$2"
    local needle="$3"
    if echo "$haystack" | grep -qF "$needle"; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        echo "      Expected to find: $needle"
        echo "      In output:"
        echo "$haystack" | sed 's/^/        /'
        FAIL=$(( FAIL + 1 ))
    fi
}

assert_not_contains() {
    local label="$1"
    local haystack="$2"
    local needle="$3"
    if ! echo "$haystack" | grep -qF "$needle"; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        echo "      Did NOT expect to find: $needle"
        echo "      In output:"
        echo "$haystack" | sed 's/^/        /'
        FAIL=$(( FAIL + 1 ))
    fi
}

# ─── Helper: create a mock curl stub ──────────────────────────────────────────

setup_mock_curl() {
    local tmpdir="$1"
    local call_log="$tmpdir/curl_calls.log"
    mkdir -p "$tmpdir/bin"
    cat > "$tmpdir/bin/curl" << 'EOF'
#!/usr/bin/env bash
# Capture all arguments and the POST body
echo "CURL_CALLED: $*" >> "$CURL_CALL_LOG"
# Capture --data argument value
while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--data" ]]; then
        echo "CURL_BODY: $2" >> "$CURL_CALL_LOG"
    fi
    shift
done
exit "${MOCK_CURL_EXIT:-0}"
EOF
    chmod +x "$tmpdir/bin/curl"
    export CURL_CALL_LOG="$call_log"
    echo "$call_log"
}

# ─── Test 1: budget_cap_reached with all four values — exits 0, curl called ───

test_budget_cap_all_fields() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local call_log
    call_log=$(setup_mock_curl "$tmpdir")

    local ctx
    ctx='{"remaining_haiku":"750000","remaining_sonnet":"0","remaining_opus":"400000","remaining_codex":"10000000","settings_url":"https://github.com/LauraMardones/headless-pr-workflow/settings/variables/actions"}'

    local exit_code=0
    PATH="$tmpdir/bin:$PATH" \
        SLACK_WEBHOOK_URL="https://hooks.slack.com/test" \
        CURL_CALL_LOG="$call_log" \
        bash "$NOTIFY_SCRIPT" budget_cap_reached "$ctx" >/dev/null 2>&1 || exit_code=$?

    assert_exit "budget_cap_reached: exits 0 with all fields" 0 "$exit_code"

    local curl_log=""
    [[ -f "$call_log" ]] && curl_log=$(cat "$call_log")
    assert_contains "budget_cap_reached: curl was called" "$curl_log" "CURL_CALLED"
}

# ─── Test 2: budget_cap_reached with missing field — defaults gracefully ───────

test_budget_cap_missing_field() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local call_log
    call_log=$(setup_mock_curl "$tmpdir")

    # Omit remaining_sonnet to test graceful default
    local ctx
    ctx='{"remaining_haiku":"750000","remaining_opus":"400000","remaining_codex":"10000000","settings_url":"https://github.com/LauraMardones/headless-pr-workflow/settings/variables/actions"}'

    local exit_code=0
    output=$(PATH="$tmpdir/bin:$PATH" \
        SLACK_WEBHOOK_URL="https://hooks.slack.com/test" \
        CURL_CALL_LOG="$call_log" \
        bash "$NOTIFY_SCRIPT" budget_cap_reached "$ctx" 2>&1) || exit_code=$?

    assert_exit "budget_cap_reached missing field: exits 0" 0 "$exit_code"

    local curl_log=""
    [[ -f "$call_log" ]] && curl_log=$(cat "$call_log")
    assert_contains "budget_cap_reached missing field: curl was still called" "$curl_log" "CURL_CALLED"
    assert_contains "budget_cap_reached missing field: (unknown) used for missing value" "$curl_log" "(unknown)"
}

# ─── Test 3: budget_cap_reached is in KNOWN_EVENTS — no unknown-event warning ─

test_budget_cap_in_known_events() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local call_log
    call_log=$(setup_mock_curl "$tmpdir")

    local ctx
    ctx='{"remaining_haiku":"750000","remaining_sonnet":"0","remaining_opus":"400000","remaining_codex":"10000000","settings_url":"https://github.com/LauraMardones/headless-pr-workflow/settings/variables/actions"}'

    local stderr_output
    stderr_output=$(PATH="$tmpdir/bin:$PATH" \
        SLACK_WEBHOOK_URL="https://hooks.slack.com/test" \
        CURL_CALL_LOG="$call_log" \
        bash "$NOTIFY_SCRIPT" budget_cap_reached "$ctx" 2>&1 >/dev/null) || true

    assert_not_contains "budget_cap_reached: no unknown-event warning" \
        "$stderr_output" "Unrecognised event type"
}

# ─── Test 4: workflow condition — all_budget_blocked=true triggers step ────────

test_workflow_condition_all_blocked_true() {
    # Verify the dispatcher.yml step has the correct if condition
    local workflow_file="$REPO_ROOT/.github/workflows/dispatcher.yml"
    local workflow_content
    workflow_content=$(cat "$workflow_file")

    assert_contains "workflow: Notify budget cap reached step exists" \
        "$workflow_content" "Notify budget cap reached"
    assert_contains "workflow: step condition checks all_budget_blocked == true" \
        "$workflow_content" "steps.invoke.outputs.all_budget_blocked == 'true'"
    assert_contains "workflow: step condition checks guard enabled" \
        "$workflow_content" "steps.guard.outputs.enabled == 'true'"
}

# ─── Test 5: workflow condition — all_budget_blocked absent means step skipped ─

test_workflow_condition_not_all_blocked() {
    # The step condition requires all_budget_blocked == 'true'; if absent or 'false'
    # GitHub Actions evaluates the if: to false and skips the step.
    # Verify the condition does NOT use != 'false' or similar permissive logic.
    local workflow_file="$REPO_ROOT/.github/workflows/dispatcher.yml"
    # Extract the if: condition line for the notify step
    local notify_if_line
    notify_if_line=$(grep -A1 'Notify budget cap reached' "$workflow_file" | grep 'if:')

    assert_contains "workflow: condition uses == true (not != false)" "$notify_if_line" "== 'true'"
    assert_not_contains "workflow: condition does not use != false" "$notify_if_line" "!= 'false'"

}
# ─── Run all tests ─────────────────────────────────────────────────────────────

echo "Running tests/test_slack_notify_budget.sh"
echo "=========================================="

test_budget_cap_all_fields
test_budget_cap_missing_field
test_budget_cap_in_known_events
test_workflow_condition_all_blocked_true
test_workflow_condition_not_all_blocked

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
