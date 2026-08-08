#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUMMARY_SCRIPT="$SCRIPT_DIR/../scripts/session-summary.sh"
PASS=0
FAIL=0

pass() {
    printf 'PASS: %s\n' "$1"
    PASS=$((PASS + 1))
}

fail() {
    printf 'FAIL: %s\n' "$1"
    FAIL=$((FAIL + 1))
}

assert_equal() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        pass "$label"
    else
        fail "$label"
        printf '  expected: <%s>\n  actual:   <%s>\n' "$expected" "$actual"
    fi
}

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass "$label"
    else
        fail "$label"
        printf '  expected to find: <%s>\n  in: <%s>\n' "$needle" "$haystack"
    fi
}

assert_failure() {
    local label="$1"
    shift
    local stdout_file stderr_file status=0
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)
    "$SUMMARY_SCRIPT" "$@" >"$stdout_file" 2>"$stderr_file" || status=$?
    if [[ "$status" -eq 1 && ! -s "$stdout_file" ]] && \
       [[ "$(<"$stderr_file")" == *"Usage: session-summary.sh"* ]]; then
        pass "$label"
    else
        fail "$label"
        printf '  status=%s stdout=<%s> stderr=<%s>\n' \
            "$status" "$(<"$stdout_file")" "$(<"$stderr_file")"
    fi
    rm -f "$stdout_file" "$stderr_file"
}

test_exact_output() {
    local expected actual expected_file actual_file
    expected=$'## Session Summary\nCommand: implement\nIssue/PR: #207 / #999\nHead: abc1234\nChecks: bash-n=pass\nBlockers: none\nNext: review'
    actual=$("$SUMMARY_SCRIPT" --command implement --issue 207 --pr 999 \
        --head abc1234 --checks bash-n=pass --blockers none --next review)
    assert_equal "exact normal output" "$expected" "$actual"

    expected_file=$(mktemp)
    actual_file=$(mktemp)
    printf '%s\n' "$expected" >"$expected_file"
    "$SUMMARY_SCRIPT" --command implement --issue 207 --pr 999 \
        --head abc1234 --checks bash-n=pass --blockers none --next review \
        >"$actual_file"
    if cmp -s "$expected_file" "$actual_file"; then
        pass "exact output includes one final newline"
    else
        fail "exact output includes one final newline"
    fi
    rm -f "$expected_file" "$actual_file"
}

test_identifiers_and_cleanup() {
    local issue_only pr_only
    issue_only=$("$SUMMARY_SCRIPT" --command cleanup --issue 209 \
        --checks none --blockers none)
    assert_contains "issue-only identifier" "$issue_only" "Issue/PR: #209"
    if [[ "$issue_only" != *"Head:"* ]]; then
        pass "cleanup omits head"
    else
        fail "cleanup omits head"
    fi
    assert_contains "cleanup default next" "$issue_only" "Next: done"

    pr_only=$("$SUMMARY_SCRIPT" --command review --pr 222 --head deadbee \
        --checks tests=pass --blockers none)
    assert_contains "PR-only identifier" "$pr_only" "Issue/PR: #222"
    assert_contains "review default next" "$pr_only" "Next: merge"
}

test_commands_and_next_actions() {
    local output
    output=$("$SUMMARY_SCRIPT" --command implement --issue 1 --head abc \
        --checks pass --blockers none)
    assert_contains "implement default next" "$output" "Next: review"

    output=$("$SUMMARY_SCRIPT" --command merge --pr 1 --head abc \
        --checks pass --blockers none)
    assert_contains "merge default next" "$output" "Next: cleanup"

    output=$("$SUMMARY_SCRIPT" --command review --pr 1 --head abc \
        --checks pass --blockers finding --next implementation)
    assert_contains "review implementation override" "$output" "Next: implementation"
}

test_deviations() {
    local output
    output=$("$SUMMARY_SCRIPT" --command cleanup --issue 1 --checks pass \
        --blockers none --deviation first --deviation "second value")
    assert_contains "deviations joined in order" "$output" \
        "Deviation: first; second value"

    output=$("$SUMMARY_SCRIPT" --command cleanup --issue 1 --checks pass \
        --blockers none)
    if [[ "$output" != *"Deviation:"* ]]; then
        pass "deviation omitted when absent"
    else
        fail "deviation omitted when absent"
    fi
}

test_help() {
    local stdout_file stderr_file status=0
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)
    "$SUMMARY_SCRIPT" --help >"$stdout_file" 2>"$stderr_file" || status=$?
    assert_equal "help exits zero" "0" "$status"
    assert_contains "help prints usage" "$(<"$stdout_file")" "Usage: session-summary.sh"
    assert_equal "help stderr empty" "" "$(<"$stderr_file")"
    rm -f "$stdout_file" "$stderr_file"
}

test_failures() {
    local base=(--command implement --issue 1 --head abc --checks pass --blockers none)
    assert_failure "missing command" --issue 1 --head abc --checks pass --blockers none
    assert_failure "missing identifier" --command implement --head abc --checks pass --blockers none
    assert_failure "missing head" --command implement --issue 1 --checks pass --blockers none
    assert_failure "missing checks" --command cleanup --issue 1 --blockers none
    assert_failure "missing blockers" --command cleanup --issue 1 --checks pass
    assert_failure "invalid command" --command deploy --issue 1 --head abc --checks pass --blockers none
    assert_failure "invalid next action" "${base[@]}" --next cleanup
    assert_failure "unknown flag" "${base[@]}" --unknown value
    assert_failure "missing flag value" "${base[@]}" --next
    assert_failure "empty flag value" "${base[@]}" --next ""
    assert_failure "repeated singleton" "${base[@]}" --checks again
    assert_failure "identifier already prefixed" --command cleanup --issue '#1' --checks pass --blockers none
}

test_exact_output
test_identifiers_and_cleanup
test_commands_and_next_actions
test_deviations
test_help
test_failures

printf '\nResults: %s passed, %s failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
