#!/usr/bin/env bash
# tests/test_dispatcher_invoke_notifications.sh
#
# Tests for Slack notification wiring in scripts/dispatcher-invoke.sh (issue #179).
#
# Scenarios covered:
#   1. --dry-run: notification calls logged, not executed
#   2. notify_slack passes correct args to slack-notify.sh
#   3. slack-notify.sh exits non-zero → invoke loop continues (|| true guard)
#   4. decision_blocker: executor posts Type: decision comment → Slack fired
#   5. conflict-type comment → no decision_blocker Slack call
#   6. feature_closure_confirmation: last story in feature Done → Slack fired
#   7. feature closure silent when sibling stories remain
#   8. epic_closure_approval: last feature in epic Done → Slack fired (requires jq)
#   (closure tests call the real notify_closure_if_complete; number parsing and no grep -P: #427)
#   9. EXIT trap fires dispatcher_error on unexpected non-zero exit
#  10. EXIT trap suppressed when DISPATCH_HANDLED=true
#  11. EXIT trap suppressed on clean exit (rc=0)
#  12. decision_blocker context carries Question/Options/Recommendation, legacy fallback (#296)
#  13. check_and_notify_decision_blocker passes the extracted fields to notify_slack (#296)
#
# Usage: bash tests/test_dispatcher_invoke_notifications.sh
# Requires: bash 4+, jq (skips gracefully if jq not available)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# The WIP and queue limits have no default in the scripts (issue #358).
export WIP_LIMIT_IMPLEMENTATION=2 READY_BUFFER_MIN=3 READY_BUFFER_MAX=5
INVOKE_SCRIPT="$REPO_ROOT/scripts/dispatcher-invoke.sh"

PASS=0
FAIL=0
SKIP=0

# Global temp dir — all test temp dirs are created under this and cleaned up on exit
GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

# ─── Prerequisites ─────────────────────────────────────────────────────────────

JQ_AVAILABLE=false
command -v jq >/dev/null 2>&1 && JQ_AVAILABLE=true

# ─── Test harness ─────────────────────────────────────────────────────────────

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

skip_test() {
    local label="$1"
    local reason="${2:-jq not available}"
    echo "SKIP: $label ($reason)"
    SKIP=$(( SKIP + 1 ))
}

# ─── Helper: create mock slack-notify.sh in a temp dir ────────────────────────

setup_mock_slack() {
    local tmpdir="$1"
    local call_log="$tmpdir/slack_calls.log"
    cat > "$tmpdir/slack-notify.sh" << EOF
#!/usr/bin/env bash
echo "SLACK_CALLED: \$1 \$2" >> "$call_log"
exit "\${MOCK_SLACK_EXIT:-0}"
EOF
    chmod +x "$tmpdir/slack-notify.sh"
    echo "$call_log"
}

# ─── Test 1: --dry-run suppresses Slack calls and logs instead ────────────────

test_dry_run_suppresses_notifications() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_slack "$tmpdir")

    local output
    output=$(bash -c "
        DRY_RUN=true
        LAST_ACTION='test'
        DISPATCH_HANDLED=false
        SLACK_WEBHOOK_URL=''

        notify_slack() {
            local event_type=\"\$1\"
            local context_json=\"\$2\"
            if \$DRY_RUN; then
                echo \"[DRY RUN] Would notify Slack: \$event_type \$context_json\"
                return 0
            fi
            SLACK_WEBHOOK_URL=\"\${SLACK_WEBHOOK_URL:-}\" \
                bash '$tmpdir/slack-notify.sh' \"\$event_type\" \"\$context_json\" || true
        }

        notify_slack 'decision_blocker' '{\"story_title\":\"Test\"}'
    " 2>&1)

    assert_contains "dry-run logs instead of calling" "$output" \
        "[DRY RUN] Would notify Slack: decision_blocker"
    assert_not_contains "dry-run does not write call log" \
        "$(cat "$call_log" 2>/dev/null || true)" "SLACK_CALLED"
}

# ─── Test 2: notify_slack passes args to slack-notify.sh ──────────────────────

test_notify_slack_passes_args() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_slack "$tmpdir")

    bash -c "
        DRY_RUN=false
        SLACK_WEBHOOK_URL='https://hooks.example.com/test'

        notify_slack() {
            local event_type=\"\$1\"
            local context_json=\"\$2\"
            SLACK_WEBHOOK_URL=\"\${SLACK_WEBHOOK_URL:-}\" \
                bash '$tmpdir/slack-notify.sh' \"\$event_type\" \"\$context_json\" || true
        }

        notify_slack 'feature_closure_confirmation' '{\"feature_title\":\"F1\"}'
    " 2>&1 || true

    assert_contains "slack-notify.sh called with correct event type" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: feature_closure_confirmation"
}

# ─── Test 3: notify_slack || true — adapter failure does not abort ─────────────

test_notify_slack_failure_does_not_abort() {
    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_slack "$tmpdir")

    local output
    output=$(MOCK_SLACK_EXIT=1 bash -c "
        set -euo pipefail
        DRY_RUN=false
        MOCK_SLACK_EXIT=1
        SLACK_WEBHOOK_URL='https://hooks.example.com/test'

        notify_slack() {
            MOCK_SLACK_EXIT=1 \
                bash '$tmpdir/slack-notify.sh' \"\$1\" \"\$2\" || true
        }

        notify_slack 'dispatcher_error' '{\"error_description\":\"test\",\"last_action\":\"test\"}'
        echo 'reached_after_notify'
    " 2>&1 || true)

    assert_contains "script continues after adapter failure" "$output" "reached_after_notify"
}

# ─── Tests 4–5: check_and_notify_decision_blocker, real function ──────────────
# Both tests extract the production check_and_notify_decision_blocker and
# register_decision_blocker_event from dispatcher-invoke.sh, so a change to the
# Type: decision filter or the registration is caught here. Requires jq.

# Runs the real functions against one mocked comment body and prints the
# registered events, one "SLACK_CALLED: <event> <context>" line each.
run_real_decision_blocker_check() {
    local comment_body="$1" tmpdir call_log fn
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    fn=$(sed -n '/^check_and_notify_decision_blocker() {/,/^}/p;/^register_decision_blocker_event() {/,/^}/p' "$INVOKE_SCRIPT")
    COMMENT_BODY="$comment_body" bash -c "
        set -euo pipefail
        REPO='owner/repo'
        source '$REPO_ROOT/scripts/lib/decision-blocker.sh'
        gh() { jq -n --arg b \"\$COMMENT_BODY\" '[{body: \$b}]'; }
        register_actionable_event() { echo \"SLACK_CALLED: \$1 \$4\" >> '$call_log'; }
        $fn
        check_and_notify_decision_blocker 42 'Test Story'
    " 2>&1 || true
    cat "$call_log" 2>/dev/null || true
}

test_decision_blocker_fires_for_decision_type() {
    if ! $JQ_AVAILABLE; then
        skip_test "decision_blocker fires for Type: decision"
        return
    fi
    local body
    body=$'## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO must decide\nOwner: PO'
    assert_contains "decision_blocker fires for Type: decision" \
        "$(run_real_decision_blocker_check "$body")" \
        "SLACK_CALLED: decision_blocker"
}

test_decision_blocker_silent_for_conflict_type() {
    if ! $JQ_AVAILABLE; then
        skip_test "no decision_blocker for conflict-type comment"
        return
    fi
    local body
    body=$'## Blocked Declaration\nType: conflict\nDeclared by: dispatcher'
    assert_not_contains "no decision_blocker for conflict-type comment" \
        "$(run_real_decision_blocker_check "$body")" \
        "SLACK_CALLED: decision_blocker"
}

# ─── Closure tests (6-8): call the real notify_closure_if_complete ─────────────
# The function is extracted from scripts/dispatcher-invoke.sh and run with a
# mocked gh and register_actionable_event (#427). Requires jq.

# run_real_closure_check <target_body> <board_data> <completed_story>
# Prints one "SLACK_CALLED: <event> <number>" line per registered event.
run_real_closure_check() {
    local target_body="$1" board_data="$2" completed="$3" tmpdir call_log fn
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    fn=$(sed -n '/^notify_closure_if_complete() {/,/^}/p' "$INVOKE_SCRIPT")
    TARGET_BODY="$target_body" BOARD_DATA="$board_data" bash -c "
        set -euo pipefail
        DRY_RUN=false
        REPO='owner/repo'
        # Smart mock: passes the --jq filter through real jq
        gh() {
            local url=\"\${2:-}\" jq_filter=\"\" result
            [[ \"\${3:-}\" == '--jq' ]] && jq_filter=\"\${4:-}\"
            if [[ \"\$url\" == *'issues/10' ]]; then
                result='{\"title\":\"Feature: Notifications\",\"body\":\"Parent epic: #30\"}'
            elif [[ \"\$url\" == *'issues/30' ]]; then
                result='{\"title\":\"Epic: Autonomous Dispatcher\",\"body\":\"\"}'
            else
                result='{\"title\":\"\",\"body\":\"\"}'
            fi
            if [[ -n \"\$jq_filter\" ]]; then
                echo \"\$result\" | jq -r \"\$jq_filter\"
            else
                echo \"\$result\"
            fi
        }
        register_actionable_event() { echo \"SLACK_CALLED: \$1 \$2\" >> '$call_log'; }
        $fn
        notify_closure_if_complete $completed
    " >/dev/null 2>&1 || true
    cat "$call_log" 2>/dev/null || true
}

CLOSURE_STORY_A='{"id":"I1","content":{"number":4,"title":"Story A","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"Done","field":{"name":"Status"}}]}}'
CLOSURE_STORY_B='{"id":"I2","content":{"number":5,"title":"Story B","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"In implementation","field":{"name":"Status"}}]}}'
CLOSURE_FEATURE='{"id":"F1","content":{"number":10,"title":"Feature: Notifications","body":"Parent epic: #30\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"In implementation","field":{"name":"Status"}}]}}'

closure_board() {
    local IFS=,
    echo "{\"data\":{\"repository\":{\"projectsV2\":{\"nodes\":[{\"id\":\"P1\",\"fields\":{\"nodes\":[]},\"items\":{\"nodes\":[$*]}}]}}}}"
}

# ─── Test 6: feature_closure_confirmation fires when all feature stories Done ──

test_feature_closure_fires_when_all_done() {
    if ! $JQ_AVAILABLE; then
        skip_test "feature_closure_confirmation fires when all stories Done"
        return
    fi
    assert_contains "feature_closure_confirmation fires when all stories Done" \
        "$(run_real_closure_check 'Feature group: #10' \
            "$(closure_board "$CLOSURE_STORY_A" "$CLOSURE_STORY_B")" 5)" \
        "SLACK_CALLED: feature_closure_confirmation 10"
}

# ─── Test 7: feature closure silent when sibling stories remain ────────────────

test_feature_closure_silent_when_stories_remain() {
    if ! $JQ_AVAILABLE; then
        skip_test "no feature_closure_confirmation when stories remain"
        return
    fi
    # Story #5 is still In implementation and #4 completes
    local sibling='{"id":"I2","content":{"number":6,"title":"Story C","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"In implementation","field":{"name":"Status"}}]}}'
    assert_not_contains "no feature_closure_confirmation when stories remain" \
        "$(run_real_closure_check 'Feature group: #10' \
            "$(closure_board "$CLOSURE_STORY_A" "$sibling")" 4)" \
        "SLACK_CALLED: feature_closure_confirmation"
}

# ─── Test 7b: feature number parsing (#427) ────────────────────────────────────

test_feature_number_parsing() {
    if ! $JQ_AVAILABLE; then
        skip_test "feature number parsing"
        return
    fi
    local board out
    board=$(closure_board "$CLOSURE_STORY_A" "$CLOSURE_STORY_B")
    assert_contains "feature number read from 'Feature group: #10 Feature: X'" \
        "$(run_real_closure_check $'Feature group: #10 Feature: X\n' "$board" 5)" \
        "SLACK_CALLED: feature_closure_confirmation 10"
    assert_contains "first 'Feature group' match wins" \
        "$(run_real_closure_check $'Feature group: #10\nFeature group: #99\n' "$board" 5)" \
        "SLACK_CALLED: feature_closure_confirmation 10"
    out=$(run_real_closure_check $'No group line here\n' "$board" 5)
    assert_not_contains "no Slack call without a 'Feature group' line" \
        "$out" "SLACK_CALLED"
    assert_not_contains "'Feature group: #100' does not match feature 10" \
        "$(run_real_closure_check 'Feature group: #100' "$board" 5)" \
        "SLACK_CALLED"
}

# ─── Test 7c: no grep -P in the dispatcher (#427) ──────────────────────────────
# Git for Windows ships GNU grep 3.0, which refuses -P without a locale.

test_dispatcher_has_no_grep_perl_regex() {
    local hits
    hits=$(grep -nE 'grep +(-[A-Za-z]*P|--perl-regexp)' "$INVOKE_SCRIPT" || true)
    assert_not_contains "scripts/dispatcher-invoke.sh has no grep -P" \
        "$hits" "grep"
}

# ─── Test 8: epic_closure_approval fires when all epic features' stories Done ──
# Stories #4 and #5 are in Feature group #10 (Done / just completed); feature
# item #10 has body "Parent epic: #30".

test_epic_closure_fires_when_all_done() {
    if ! $JQ_AVAILABLE; then
        skip_test "epic_closure_approval fires when all epic features Done"
        return
    fi
    assert_contains "epic_closure_approval fires when all epic features Done" \
        "$(run_real_closure_check 'Feature group: #10' \
            "$(closure_board "$CLOSURE_STORY_A" "$CLOSURE_STORY_B" "$CLOSURE_FEATURE")" 5)" \
        "SLACK_CALLED: epic_closure_approval 30"
}

# ─── Test 9: EXIT trap fires dispatcher_error on unexpected non-zero exit ──────
# Does not require jq — notify_slack is mocked directly.
# Exercises: _dispatcher_exit_hook logic, rc!=0 path, DISPATCH_HANDLED=false gate.

test_dispatcher_error_trap_fires_on_unexpected_exit() {
    local tmpdir call_log
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log=$(setup_mock_slack "$tmpdir")

    bash -c "
        DISPATCH_HANDLED=false
        LAST_ACTION='fetching project board'

        notify_slack() {
            echo \"SLACK_CALLED: \$1\" >> '$call_log'
        }

        _dispatcher_exit_hook() {
            local rc=\$?
            [[ \$rc -eq 0 ]] && return 0
            \$DISPATCH_HANDLED && return 0
            notify_slack 'dispatcher_error' \"exit_code=\$rc,last_action=\$LAST_ACTION\"
        }
        trap _dispatcher_exit_hook EXIT

        exit 2
    " || true

    assert_contains "dispatcher_error fires on unexpected non-zero exit" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: dispatcher_error"
}

# ─── Test 10: EXIT trap suppressed when DISPATCH_HANDLED=true ─────────────────
# Simulates the conflict-blocker path: DISPATCH_HANDLED=true before exit 1.

test_dispatcher_error_suppressed_when_handled() {
    local tmpdir call_log
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log=$(setup_mock_slack "$tmpdir")

    bash -c "
        DISPATCH_HANDLED=true
        LAST_ACTION='posting conflict blocker'

        notify_slack() {
            echo \"SLACK_CALLED: \$1\" >> '$call_log'
        }

        _dispatcher_exit_hook() {
            local rc=\$?
            [[ \$rc -eq 0 ]] && return 0
            \$DISPATCH_HANDLED && return 0
            notify_slack 'dispatcher_error' \"exit_code=\$rc\"
        }
        trap _dispatcher_exit_hook EXIT

        exit 1
    " || true

    assert_not_contains "dispatcher_error suppressed when DISPATCH_HANDLED=true" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: dispatcher_error"
}

# ─── Test 11: EXIT trap suppressed on clean exit (rc=0) ───────────────────────

test_dispatcher_error_suppressed_on_clean_exit() {
    local tmpdir call_log
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log=$(setup_mock_slack "$tmpdir")

    bash -c "
        DISPATCH_HANDLED=false
        LAST_ACTION='exiting cleanly'

        notify_slack() {
            echo \"SLACK_CALLED: \$1\" >> '$call_log'
        }

        _dispatcher_exit_hook() {
            local rc=\$?
            [[ \$rc -eq 0 ]] && return 0
            \$DISPATCH_HANDLED && return 0
            notify_slack 'dispatcher_error' \"exit_code=\$rc\"
        }
        trap _dispatcher_exit_hook EXIT

        exit 0
    " || true

    assert_not_contains "dispatcher_error suppressed on clean exit (rc=0)" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: dispatcher_error"
}

# ─── Test 12: decision_blocker context carries Question/Options/Recommendation (#296)
# Runs the real decision_blocker_context() from scripts/lib/decision-blocker.sh.

test_decision_blocker_context_fields() {
    if ! $JQ_AVAILABLE; then
        skip_test "decision_blocker context carries the decision fields"
        return
    fi
    local lib="$REPO_ROOT/scripts/lib/decision-blocker.sh" out
    local body=$'## Blocked Declaration\nType: decision\nChecked: docs; no existing decision found\nQuestion: Ship A or B?\nOptions: \nA) ship A — fast\nB) ship B — Question: is slow\nRecommendation: B because safe\nDeclared by: executor\nUnblocked when: PO decides\nOwner: PO'
    out=$(bash -c "source '$lib'; decision_blocker_context \"\$1\" 'T' 'https://x/1'" _ "$body")
    local want_opts=$'A) ship A — fast\nB) ship B — Question: is slow'
    [[ "$(jq -r .question <<< "$out")" == "Ship A or B?" ]] \
        && assert_contains "context: question parsed" "ok" "ok" \
        || assert_contains "context: question parsed" "$out" "Ship A or B?"
    [[ "$(jq -r .options <<< "$out")" == "$want_opts" ]] \
        && assert_contains "context: multi-line options preserved, label inside option text kept" "ok" "ok" \
        || assert_contains "context: multi-line options preserved" "$out" "$want_opts"
    assert_contains "context: recommendation parsed" "$(jq -r .recommendation <<< "$out")" "B because safe"
    assert_contains "context: unblocked_when kept" "$(jq -r .unblocked_when <<< "$out")" "PO decides"
    assert_contains "context: story title and url kept" "$(jq -c '[.story_title,.issue_url]' <<< "$out")" '["T","https://x/1"]'

    # Legacy declaration without the new fields → empty strings, still a valid context
    local legacy=$'## Blocked Declaration\nType: decision\nUnblocked when: PO must decide\nOwner: PO'
    out=$(bash -c "source '$lib'; decision_blocker_context \"\$1\" 'T' 'u'" _ "$legacy")
    assert_contains "legacy: new fields empty" \
        "$(jq -c '[.question,.options,.recommendation]' <<< "$out")" '["","",""]'
    assert_contains "legacy: unblocked_when kept" "$(jq -r .unblocked_when <<< "$out")" "PO must decide"
}

# ─── Test 13: wired call passes the extracted fields to notify_slack (#296) ────

test_decision_blocker_wired_passes_fields() {
    if ! $JQ_AVAILABLE; then
        skip_test "check_and_notify_decision_blocker passes the decision fields"
        return
    fi
    local tmpdir call_log fn
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    fn=$(sed -n '/^check_and_notify_decision_blocker() {/,/^}/p;/^register_decision_blocker_event() {/,/^}/p' "$INVOKE_SCRIPT")
    bash -c "
        set -euo pipefail
        REPO='owner/repo'
        source '$REPO_ROOT/scripts/lib/decision-blocker.sh'
        gh() { jq -n '[{body: \"## Blocked Declaration\nType: decision\nQuestion: Q?\nOptions:\nA) a — x\nB) b — y\nRecommendation: A: ok\nUnblocked when: soon\"}]'; }
        register_actionable_event() { echo \"SLACK_CALLED: \$1 \$4\" >> '$call_log'; }
        $fn
        check_and_notify_decision_blocker 42 'Story'
    " 2>&1 || true
    local logged
    logged=$(cat "$call_log" 2>/dev/null || true)
    assert_contains "wired: decision_blocker fired" "$logged" "SLACK_CALLED: decision_blocker"
    assert_contains "wired: question in context" "$logged" '"question":"Q?"'
    assert_contains "wired: options in context line by line" "$logged" '"options":"A) a — x\nB) b — y"'
    assert_contains "wired: recommendation in context" "$logged" '"recommendation":"A: ok"'
}

# ─── Summary ──────────────────────────────────────────────────────────────────

# ─── Test 13: a failed register turns the run red (issue #352) ───────────────
# Runs the real init block, register_actionable_event and _dispatcher_exit_hook
# from dispatcher-invoke.sh, with a dispatcher-notify-retry.sh whose register
# exits 1. One event is lost before a chained exec, one after it; the final
# process exits 0 on its own.

test_failed_register_exits_one_without_dispatcher_error() {
    local tmpdir h
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    h="$tmpdir/harness.sh"
    printf '#!/usr/bin/env bash\nexit "${MOCK_REGISTER_EXIT:-1}"\n' > "$tmpdir/dispatcher-notify-retry.sh"
    {
        echo 'set -euo pipefail'
        echo 'REPO=o/r; DRY_RUN=${DRY_RUN:-false}; DISPATCH_HANDLED=false; LAST_ACTION=x'
        echo "notify_slack() { echo \"SLACK_CALLED: \$1\" >> '$tmpdir/slack.log'; }"
        sed -n '/^if \[\[ -z "\${DISPATCH_REGISTER_FAILURES_FILE/,/^fi$/p' "$INVOKE_SCRIPT"
        sed -n '/^_dispatcher_exit_hook() {/,/^}/p' "$INVOKE_SCRIPT"
        echo 'trap _dispatcher_exit_hook EXIT'
        sed -n '/^register_actionable_event() {/,/^}/p' "$INVOKE_SCRIPT"
        cat <<'BODY'
if [[ "${1:-}" != second ]]; then
    register_actionable_event decision_blocker 41 "" '{}'
    DISPATCH_HANDLED=true
    exec bash "$0" second
fi
register_actionable_event epic_closure_approval 7 "" '{}'
exit 0
BODY
    } > "$h"
    local out rc=0
    out=$(bash "$h" 2>&1) || rc=$?
    assert_contains "failed register exits 1 at the end" "rc=$rc" "rc=1"
    assert_contains "error line names the event before the chained exec" "$out" "decision_blocker #41"
    assert_contains "error line names the event after the chained exec" "$out" "epic_closure_approval #7"
    assert_not_contains "no dispatcher_error for a failed register" \
        "$(cat "$tmpdir/slack.log" 2>/dev/null || true)" "dispatcher_error"

    rc=0
    out=$(MOCK_REGISTER_EXIT=0 bash "$h" 2>&1) || rc=$?
    assert_contains "successful register keeps exit 0" "rc=$rc" "rc=0"

    rc=0
    out=$(DRY_RUN=true MOCK_REGISTER_EXIT=0 bash "$h" 2>&1) || rc=$?
    assert_contains "dry-run register exits 0" "rc=$rc" "rc=0"
}

run_all_tests() {
    echo "── Dispatcher notification tests (issue #179) ──────────────────────────"
    test_dry_run_suppresses_notifications
    test_notify_slack_passes_args
    test_notify_slack_failure_does_not_abort
    test_decision_blocker_fires_for_decision_type
    test_decision_blocker_silent_for_conflict_type
    test_decision_blocker_context_fields
    test_decision_blocker_wired_passes_fields
    test_feature_closure_fires_when_all_done
    test_feature_closure_silent_when_stories_remain
    test_feature_number_parsing
    test_dispatcher_has_no_grep_perl_regex
    test_epic_closure_fires_when_all_done
    test_dispatcher_error_trap_fires_on_unexpected_exit
    test_dispatcher_error_suppressed_when_handled
    test_dispatcher_error_suppressed_on_clean_exit
    test_failed_register_exits_one_without_dispatcher_error
    echo "──────────────────────────────────────────────────────────────────────"
    echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
    [[ $FAIL -eq 0 ]]
}

run_all_tests
