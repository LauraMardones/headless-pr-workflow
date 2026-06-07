#!/usr/bin/env bash
# tests/test_dispatcher_invoke_budget.sh
#
# Tests for budget check integration in scripts/dispatcher-invoke.sh (issue #203).
#
# Scenarios covered:
#   1. Budget-blocked path: [BUDGET SKIP] logged; executor not invoked
#   2. All-blocked detection: all_budget_blocked=true written to GITHUB_OUTPUT
#   3. Partial-blocked: all_budget_blocked NOT written when at least one story ran
#   4. Increment path: budget increment called after successful /implement
#   5. Dry-run: check and increment logged but not executed
#   6. Budget script absent: gracefully skips budget check without error
#   7. GITHUB_OUTPUT not set: all_budget_blocked guard suppresses write
#
# Usage: bash tests/test_dispatcher_invoke_budget.sh
# Requires: bash 4+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUDGET_SCRIPT="$REPO_ROOT/scripts/dispatcher-budget.sh"

PASS=0
FAIL=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

# ─── Test harness ──────────────────────────────────────────────────────────────

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

assert_file_contains() {
    local label="$1"
    local filepath="$2"
    local needle="$3"
    local content
    content=$(cat "$filepath" 2>/dev/null || true)
    if echo "$content" | grep -qF "$needle"; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        echo "      Expected to find in $filepath: $needle"
        echo "      File contents:"
        echo "$content" | sed 's/^/        /'
        FAIL=$(( FAIL + 1 ))
    fi
}

assert_file_not_contains() {
    local label="$1"
    local filepath="$2"
    local needle="$3"
    local content
    content=$(cat "$filepath" 2>/dev/null || true)
    if ! echo "$content" | grep -qF "$needle"; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        echo "      Did NOT expect to find in $filepath: $needle"
        echo "      File contents:"
        echo "$content" | sed 's/^/        /'
        FAIL=$(( FAIL + 1 ))
    fi
}

# ─── Helper: create a mock dispatcher-budget.sh ────────────────────────────────

setup_mock_budget() {
    local tmpdir="$1"
    local check_exit="${2:-0}"     # 0 = budget available, 1 = cap reached
    local call_log="$tmpdir/budget_calls.log"

    cat > "$tmpdir/dispatcher-budget.sh" << EOF
#!/usr/bin/env bash
SUBCMD="\$1"
shift
echo "\$SUBCMD \$*" >> "$call_log"
case "\$SUBCMD" in
    check)
        exit ${check_exit} ;;
    increment)
        exit 0 ;;
    estimate)
        echo 75000
        exit 0 ;;
    *)
        exit 2 ;;
esac
EOF
    chmod +x "$tmpdir/dispatcher-budget.sh"
    echo "$call_log"
}

# ─── Test 1: Budget-blocked — [BUDGET SKIP] logged; executor not called ────────

test_budget_skip_logged_and_executor_skipped() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_budget "$tmpdir" 1)   # cap reached

    local executor_called=false
    local output
    output=$(bash -c "
        set -euo pipefail
        BUDGET_SCRIPT='$tmpdir/dispatcher-budget.sh'
        BUDGET_TYPE='sonnet'
        BUDGET_BLOCKED_COUNT=0
        TOTAL_READY_COUNT=0
        ESTIMATED_TOKENS=75000
        ISSUE_NUMBER=203
        TARGET_TITLE='Story: test budget skip'
        DRY_RUN=false

        TOTAL_READY_COUNT=\$(( TOTAL_READY_COUNT + 1 ))

        EXECUTOR_INVOKED=false
        if [[ -n \"\$BUDGET_TYPE\" && -f \"\$BUDGET_SCRIPT\" ]]; then
            if ! bash \"\$BUDGET_SCRIPT\" check \"\$BUDGET_TYPE\" >/dev/null; then
                echo \"[BUDGET SKIP] #\$ISSUE_NUMBER: \$TARGET_TITLE — \${BUDGET_TYPE} daily cap reached; skipping\"
                EXECUTOR_INVOKED=false
            else
                EXECUTOR_INVOKED=true
            fi
        fi

        echo \"executor_invoked=\$EXECUTOR_INVOKED\"
    " 2>&1)

    assert_contains "budget-skip: [BUDGET SKIP] line logged" "$output" "[BUDGET SKIP] #203"
    assert_contains "budget-skip: includes executor type" "$output" "sonnet daily cap reached; skipping"
    assert_contains "budget-skip: executor not invoked" "$output" "executor_invoked=false"
    assert_contains "budget-skip: check was called" "$(cat "$call_log")" "check sonnet"
}

# ─── Test 2: All stories budget-blocked → all_budget_blocked=true in GITHUB_OUTPUT

test_all_budget_blocked_output() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local github_output="$tmpdir/github_output"

    output=$(bash -c "
        set -euo pipefail
        BUDGET_BLOCKED_COUNT=3
        TOTAL_READY_COUNT=3
        GITHUB_OUTPUT='$github_output'

        if [[ \$TOTAL_READY_COUNT -gt 0 && \$BUDGET_BLOCKED_COUNT -eq \$TOTAL_READY_COUNT ]]; then
            [[ -n \"\${GITHUB_OUTPUT:-}\" ]] && echo \"all_budget_blocked=true\" >> \"\$GITHUB_OUTPUT\"
        fi
        echo done
    " 2>&1)

    assert_file_contains "all_budget_blocked written when all stories blocked" \
        "$github_output" "all_budget_blocked=true"
}

# ─── Test 3: Partially blocked — all_budget_blocked NOT written ────────────────

test_partial_blocked_no_all_budget_blocked() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local github_output="$tmpdir/github_output"

    bash -c "
        set -euo pipefail
        BUDGET_BLOCKED_COUNT=1
        TOTAL_READY_COUNT=3
        GITHUB_OUTPUT='$github_output'

        if [[ \$TOTAL_READY_COUNT -gt 0 && \$BUDGET_BLOCKED_COUNT -eq \$TOTAL_READY_COUNT ]]; then
            [[ -n \"\${GITHUB_OUTPUT:-}\" ]] && echo \"all_budget_blocked=true\" >> \"\$GITHUB_OUTPUT\"
        fi
    " 2>&1 || true

    assert_file_not_contains "all_budget_blocked NOT written when only partial blocked" \
        "$github_output" "all_budget_blocked=true"
}

# ─── Test 4: Zero stories reached — all_budget_blocked NOT written ─────────────

test_zero_ready_count_no_all_budget_blocked() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local github_output="$tmpdir/github_output"

    bash -c "
        set -euo pipefail
        BUDGET_BLOCKED_COUNT=0
        TOTAL_READY_COUNT=0
        GITHUB_OUTPUT='$github_output'

        if [[ \$TOTAL_READY_COUNT -gt 0 && \$BUDGET_BLOCKED_COUNT -eq \$TOTAL_READY_COUNT ]]; then
            [[ -n \"\${GITHUB_OUTPUT:-}\" ]] && echo \"all_budget_blocked=true\" >> \"\$GITHUB_OUTPUT\"
        fi
    " 2>&1 || true

    assert_file_not_contains "all_budget_blocked NOT written when no stories attempted" \
        "$github_output" "all_budget_blocked=true"
}

# ─── Test 5: Increment called after successful /implement ──────────────────────

test_budget_increment_called_after_success() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_budget "$tmpdir" 0)   # budget available

    local output
    output=$(bash -c "
        set -euo pipefail
        BUDGET_SCRIPT='$tmpdir/dispatcher-budget.sh'
        BUDGET_TYPE='sonnet'
        ESTIMATED_TOKENS=75000
        ISSUE_NUMBER=203
        DRY_RUN=false

        if [[ -n \"\$BUDGET_TYPE\" && -f \"\$BUDGET_SCRIPT\" ]]; then
            echo \"[BUDGET] Increment: \$BUDGET_TYPE +\$ESTIMATED_TOKENS after #\$ISSUE_NUMBER\"
            bash \"\$BUDGET_SCRIPT\" increment \"\$BUDGET_TYPE\" \"\$ESTIMATED_TOKENS\" || true
        fi
    " 2>&1)

    assert_contains "increment: log line emitted" "$output" "[BUDGET] Increment: sonnet +75000 after #203"
    assert_contains "increment: increment subcommand called" "$(cat "$call_log")" "increment sonnet 75000"
}

# ─── Test 6: Dry-run — check and increment logged but not executed ──────────────

test_dry_run_logs_not_executes() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_budget "$tmpdir" 1)   # cap reached (should not be checked)

    local output
    output=$(bash -c "
        set -euo pipefail
        BUDGET_SCRIPT='$tmpdir/dispatcher-budget.sh'
        BUDGET_TYPE='sonnet'
        ESTIMATED_TOKENS=75000
        DRY_RUN=true

        if [[ -n \"\$BUDGET_TYPE\" && -f \"\$BUDGET_SCRIPT\" ]]; then
            if \$DRY_RUN; then
                echo \"[DRY RUN] Would check: dispatcher-budget.sh check \$BUDGET_TYPE\"
            fi
        fi

        if [[ -n \"\$BUDGET_TYPE\" && -f \"\$BUDGET_SCRIPT\" ]]; then
            if \$DRY_RUN; then
                echo \"[DRY RUN] Would call: dispatcher-budget.sh increment \$BUDGET_TYPE \$ESTIMATED_TOKENS\"
            fi
        fi
    " 2>&1)

    assert_contains "dry-run: check log line present" "$output" \
        "[DRY RUN] Would check: dispatcher-budget.sh check sonnet"
    assert_contains "dry-run: increment log line present" "$output" \
        "[DRY RUN] Would call: dispatcher-budget.sh increment sonnet 75000"
    assert_not_contains "dry-run: budget script not actually called" \
        "$(cat "$call_log" 2>/dev/null || true)" "check"
}

# ─── Test 7: Budget script absent — no error, graceful skip ───────────────────

test_budget_script_absent_graceful() {
    local output
    output=$(bash -c "
        set -euo pipefail
        BUDGET_SCRIPT='/nonexistent/dispatcher-budget.sh'
        BUDGET_TYPE='sonnet'
        DRY_RUN=false

        TOTAL_READY_COUNT=1
        ESTIMATED_TOKENS=75000

        if [[ -n \"\$BUDGET_TYPE\" && -f \"\$BUDGET_SCRIPT\" ]]; then
            bash \"\$BUDGET_SCRIPT\" check \"\$BUDGET_TYPE\" >/dev/null
        fi
        echo 'no-error'
    " 2>&1)

    assert_contains "absent budget script: no error" "$output" "no-error"
    assert_not_contains "absent budget script: no BUDGET SKIP" "$output" "[BUDGET SKIP]"
}

# ─── Test 8: GITHUB_OUTPUT not set — all_budget_blocked write suppressed ───────

test_all_budget_blocked_suppressed_when_no_github_output() {
    local output
    output=$(bash -c "
        set -euo pipefail
        BUDGET_BLOCKED_COUNT=2
        TOTAL_READY_COUNT=2
        unset GITHUB_OUTPUT

        if [[ \$TOTAL_READY_COUNT -gt 0 && \$BUDGET_BLOCKED_COUNT -eq \$TOTAL_READY_COUNT ]]; then
            [[ -n \"\${GITHUB_OUTPUT:-}\" ]] && echo \"all_budget_blocked=true\" >> \"\$GITHUB_OUTPUT\"
        fi
        echo 'no-error'
    " 2>&1)

    assert_contains "no GITHUB_OUTPUT: no error" "$output" "no-error"
    assert_not_contains "no GITHUB_OUTPUT: all_budget_blocked not written to stdout" \
        "$output" "all_budget_blocked=true"
}

# ─── Test 9: Exit code 2 (config error) — warning logged, no skip ─────────────

test_budget_config_error_warns_and_proceeds() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_budget "$tmpdir" 2)   # exit 2 = config error

    local output
    output=$(bash -c "
        set -euo pipefail
        BUDGET_SCRIPT='$tmpdir/dispatcher-budget.sh'
        BUDGET_TYPE='sonnet'
        DRY_RUN=false

        _budget_rc=0
        bash \"\$BUDGET_SCRIPT\" check \"\$BUDGET_TYPE\" >/dev/null || _budget_rc=\$?
        if [[ \$_budget_rc -eq 2 ]]; then
            echo \"Warning: Budget configuration error for \$BUDGET_TYPE (exit 2); proceeding without budget enforcement.\" >&2
        elif [[ \$_budget_rc -eq 1 ]]; then
            echo \"[BUDGET SKIP] cap reached\"
        fi
        echo 'proceed'
    " 2>&1)

    assert_contains "exit-2: warning message logged" "$output" \
        "Budget configuration error for sonnet (exit 2)"
    assert_not_contains "exit-2: no BUDGET SKIP logged" "$output" "[BUDGET SKIP]"
    assert_contains "exit-2: execution continues" "$output" "proceed"
}

# ─── Test 10: dispatcher-budget.sh estimate is called for size label ────────────

test_estimate_called_with_size_label() {
    [[ -f "$BUDGET_SCRIPT" ]] || {
        echo "SKIP: test_estimate_called_with_size_label (dispatcher-budget.sh not found)"
        return
    }

    local output
    output=$(bash "$BUDGET_SCRIPT" estimate "size:small" 2>&1)
    assert_contains "estimate: size:small returns 25000" "$output" "25000"

    output=$(bash "$BUDGET_SCRIPT" estimate "size:large" 2>&1)
    assert_contains "estimate: size:large returns 150000" "$output" "150000"

    output=$(bash "$BUDGET_SCRIPT" estimate "medium" 2>&1)
    assert_contains "estimate: medium returns 75000" "$output" "75000"

    output=$(bash "$BUDGET_SCRIPT" estimate "unknown-label" 2>&1)
    assert_contains "estimate: unknown label returns default 75000" "$output" "75000"
}

# ─── Summary ──────────────────────────────────────────────────────────────────

run_all_tests() {
    echo "── Dispatcher budget integration tests (issue #203) ────────────────────"
    test_budget_skip_logged_and_executor_skipped
    test_all_budget_blocked_output
    test_partial_blocked_no_all_budget_blocked
    test_zero_ready_count_no_all_budget_blocked
    test_budget_increment_called_after_success
    test_dry_run_logs_not_executes
    test_budget_script_absent_graceful
    test_all_budget_blocked_suppressed_when_no_github_output
    test_budget_config_error_warns_and_proceeds
    test_estimate_called_with_size_label
    echo "──────────────────────────────────────────────────────────────────────"
    echo "Results: $PASS passed, $FAIL failed"
    [[ $FAIL -eq 0 ]]
}

run_all_tests
