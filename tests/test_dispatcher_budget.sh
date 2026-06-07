#!/usr/bin/env bash
# tests/test_dispatcher_budget.sh
#
# Tests for scripts/dispatcher-budget.sh (issue #202).
#
# Scenarios covered:
#   1.  check exits 0 when usage is below cap
#   2.  check exits 1 when usage equals or exceeds cap
#   3.  check prints remaining tokens to stdout as a bare integer
#   4.  check with missing BUDGET_DAILY_* env var exits non-zero with stderr error message
#   5.  increment creates counter file on first call (file absent before call)
#   6.  increment accumulates — second increment adds to first, not replaces
#   7.  check after increment reflects updated usage
#   8.  BUDGET_COUNTER_DIR override is respected (test isolation)
#   9.  Fresh counter directory with no prior file: check treats usage as 0
#  10.  All four executor types recognised: haiku, sonnet, opus, codex
#
# Usage: bash tests/test_dispatcher_budget.sh
# Requires: bash 4+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUDGET_SCRIPT="$REPO_ROOT/scripts/dispatcher-budget.sh"

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

assert_equals() {
    local label="$1"
    local expected="$2"
    local actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        echo "      Expected: $expected"
        echo "      Got:      $actual"
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
        echo "      In: $haystack"
        FAIL=$(( FAIL + 1 ))
    fi
}

assert_file_exists() {
    local label="$1"
    local file="$2"
    if [[ -f "$file" ]]; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        echo "      File not found: $file"
        FAIL=$(( FAIL + 1 ))
    fi
}

# ─── Test 1: check exits 0 when usage is below cap ────────────────────────────

test_check_exits_0_when_below_cap() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local exit_code=0
    BUDGET_COUNTER_DIR="$tmpdir" BUDGET_DAILY_HAIKU=100000 \
        bash "$BUDGET_SCRIPT" check haiku >/dev/null 2>&1 || exit_code=$?

    assert_exit "check exits 0 when no usage (below cap)" 0 "$exit_code"
}

# ─── Test 2: check exits 1 when usage equals or exceeds cap ──────────────────

test_check_exits_1_when_cap_reached() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local today
    today=$(date -u +%Y-%m-%d)
    printf '%s:100000\n' "$today" > "$tmpdir/budget-sonnet"

    local exit_code=0
    BUDGET_COUNTER_DIR="$tmpdir" BUDGET_DAILY_SONNET=100000 \
        bash "$BUDGET_SCRIPT" check sonnet >/dev/null 2>&1 || exit_code=$?

    assert_exit "check exits 1 when usage equals cap" 1 "$exit_code"

    # Also test when usage exceeds cap
    printf '%s:150000\n' "$today" > "$tmpdir/budget-sonnet"
    exit_code=0
    BUDGET_COUNTER_DIR="$tmpdir" BUDGET_DAILY_SONNET=100000 \
        bash "$BUDGET_SCRIPT" check sonnet >/dev/null 2>&1 || exit_code=$?

    assert_exit "check exits 1 when usage exceeds cap" 1 "$exit_code"
}

# ─── Test 3: check prints remaining tokens to stdout as a bare integer ────────

test_check_prints_remaining_tokens() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local today
    today=$(date -u +%Y-%m-%d)
    printf '%s:250000\n' "$today" > "$tmpdir/budget-opus"

    local output
    output=$(BUDGET_COUNTER_DIR="$tmpdir" BUDGET_DAILY_OPUS=400000 \
        bash "$BUDGET_SCRIPT" check opus 2>/dev/null)

    assert_equals "check prints remaining tokens as bare integer" "150000" "$output"
}

# ─── Test 4: check with missing BUDGET_DAILY_* exits non-zero with stderr msg ─

test_check_missing_env_var_exits_nonzero() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local exit_code=0
    local stderr_output
    stderr_output=$(BUDGET_COUNTER_DIR="$tmpdir" \
        bash "$BUDGET_SCRIPT" check haiku 2>&1 >/dev/null) || exit_code=$?

    if [[ "$exit_code" -ne 0 ]]; then
        echo "PASS: check with missing env var exits non-zero (exit $exit_code)"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: check with missing env var should exit non-zero, got 0"
        FAIL=$(( FAIL + 1 ))
    fi

    assert_contains "check with missing env var prints error to stderr" \
        "$stderr_output" "BUDGET_DAILY_HAIKU"
}

# ─── Test 5: increment creates counter file on first call ────────────────────

test_increment_creates_counter_file() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    BUDGET_COUNTER_DIR="$tmpdir" \
        bash "$BUDGET_SCRIPT" increment haiku 25000

    assert_file_exists "increment creates counter file on first call" \
        "$tmpdir/budget-haiku"
}

# ─── Test 6: increment accumulates ───────────────────────────────────────────

test_increment_accumulates() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    BUDGET_COUNTER_DIR="$tmpdir" bash "$BUDGET_SCRIPT" increment sonnet 25000
    BUDGET_COUNTER_DIR="$tmpdir" bash "$BUDGET_SCRIPT" increment sonnet 50000

    local today
    today=$(date -u +%Y-%m-%d)
    local stored
    stored=$(< "$tmpdir/budget-sonnet")

    assert_equals "increment accumulates (25000 + 50000 = 75000)" \
        "${today}:75000" "$stored"
}

# ─── Test 7: check after increment reflects updated usage ────────────────────

test_check_after_increment_reflects_usage() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    BUDGET_COUNTER_DIR="$tmpdir" bash "$BUDGET_SCRIPT" increment codex 2000000

    local remaining
    remaining=$(BUDGET_COUNTER_DIR="$tmpdir" BUDGET_DAILY_CODEX=10000000 \
        bash "$BUDGET_SCRIPT" check codex 2>/dev/null)

    assert_equals "check after increment reflects updated usage (10M - 2M = 8M)" \
        "8000000" "$remaining"
}

# ─── Test 8: BUDGET_COUNTER_DIR override is respected ────────────────────────

test_budget_counter_dir_override() {
    local dir_a dir_b
    dir_a=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    dir_b=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    BUDGET_COUNTER_DIR="$dir_a" bash "$BUDGET_SCRIPT" increment haiku 10000

    # dir_b should have no file — increment went to dir_a
    if [[ ! -f "$dir_b/budget-haiku" ]]; then
        echo "PASS: BUDGET_COUNTER_DIR isolates counter to the specified directory"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: counter file appeared in wrong directory"
        FAIL=$(( FAIL + 1 ))
    fi

    # dir_a should have the counter file
    assert_file_exists "BUDGET_COUNTER_DIR: counter created in overridden directory" \
        "$dir_a/budget-haiku"
}

# ─── Test 9: fresh counter directory — check treats usage as 0 ───────────────

test_fresh_directory_treats_usage_as_zero() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local remaining
    remaining=$(BUDGET_COUNTER_DIR="$tmpdir" BUDGET_DAILY_OPUS=400000 \
        bash "$BUDGET_SCRIPT" check opus 2>/dev/null)

    assert_equals "fresh counter dir: check treats usage as 0 (full cap available)" \
        "400000" "$remaining"
}

# ─── Test 10: all four executor types recognised ─────────────────────────────

test_all_four_executor_types_recognised() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    local today
    today=$(date -u +%Y-%m-%d)

    for executor in haiku sonnet opus codex; do
        local exit_code=0
        local env_var="BUDGET_DAILY_$(echo "$executor" | tr '[:lower:]' '[:upper:]')"
        BUDGET_COUNTER_DIR="$tmpdir" \
            eval "${env_var}=999999 bash \"\$BUDGET_SCRIPT\" check \"$executor\"" \
            >/dev/null 2>&1 || exit_code=$?
        assert_exit "executor type '$executor' recognised by check (exits 0)" 0 "$exit_code"

        exit_code=0
        BUDGET_COUNTER_DIR="$tmpdir" \
            bash "$BUDGET_SCRIPT" increment "$executor" 1 || exit_code=$?
        assert_exit "executor type '$executor' recognised by increment (exits 0)" 0 "$exit_code"
    done
}

# ─── Run all tests ────────────────────────────────────────────────────────────

test_check_exits_0_when_below_cap
test_check_exits_1_when_cap_reached
test_check_prints_remaining_tokens
test_check_missing_env_var_exits_nonzero
test_increment_creates_counter_file
test_increment_accumulates
test_check_after_increment_reflects_usage
test_budget_counter_dir_override
test_fresh_directory_treats_usage_as_zero
test_all_four_executor_types_recognised

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
