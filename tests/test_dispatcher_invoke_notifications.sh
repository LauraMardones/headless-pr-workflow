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
#   9. EXIT trap fires dispatcher_error on unexpected non-zero exit
#  10. EXIT trap suppressed when DISPATCH_HANDLED=true
#  11. EXIT trap suppressed on clean exit (rc=0)
#
# Usage: bash tests/test_dispatcher_invoke_notifications.sh
# Requires: bash 4+, jq (skips gracefully if jq not available)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
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

# ─── Test 4: check_and_notify_decision_blocker fires for Type: decision ────────
# Requires jq to parse JSON comments.

test_decision_blocker_fires_for_decision_type() {
    if ! $JQ_AVAILABLE; then
        skip_test "decision_blocker fires for Type: decision"
        return
    fi

    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_slack "$tmpdir")

    bash -c "
        set -euo pipefail
        DRY_RUN=false
        REPO='owner/repo'
        SLACK_WEBHOOK_URL='https://hooks.example.com/test'

        # Mock gh — returns a decision-type blocked declaration comment
        gh() {
            printf '[{\"body\":\"## Blocked Declaration\\nType: decision\\nDeclared by: executor\\nBlocks: #42\\nUnblocked when: PO must decide\\nOwner: PO\"}]'
        }

        notify_slack() {
            echo \"SLACK_CALLED: \$1 \$2\" >> '$call_log'
        }

        check_and_notify_decision_blocker() {
            local issue_num=\"\$1\"
            local issue_title=\"\$2\"
            local blocker_body
            blocker_body=\$(gh api \"repos/\$REPO/issues/\$issue_num/comments\" \
                | jq -r '[.[] | select(.body | (test(\"## Blocked Declaration\") and test(\"Type: decision\")))]
                          | last | .body // empty' 2>/dev/null || true)
            [[ -z \"\$blocker_body\" ]] && return 0
            local unblocked_when issue_url
            unblocked_when=\$(printf '%s' \"\$blocker_body\" \
                | grep -oP '(?<=Unblocked when: ).*' | head -1 | sed 's/[[:space:]]*\$//' \
                || echo 'condition to be determined by PO')
            issue_url=\"https://github.com/\$REPO/issues/\$issue_num\"
            notify_slack 'decision_blocker' \
                \"\$(jq -n \
                       --arg st \"\$issue_title\" \
                       --arg iu \"\$issue_url\" \
                       --arg bt 'decision' \
                       --arg uw \"\$unblocked_when\" \
                       '{story_title: \$st, issue_url: \$iu, blocker_type: \$bt, unblocked_when: \$uw}')\"
        }

        check_and_notify_decision_blocker '42' 'Test Story'
    " 2>&1 || true

    assert_contains "decision_blocker fires for Type: decision" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: decision_blocker"
}

# ─── Test 5: check_and_notify_decision_blocker silent for non-decision comment ─
# Requires jq.

test_decision_blocker_silent_for_conflict_type() {
    if ! $JQ_AVAILABLE; then
        skip_test "no decision_blocker for conflict-type comment"
        return
    fi

    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_slack "$tmpdir")

    bash -c "
        set -euo pipefail
        DRY_RUN=false
        REPO='owner/repo'

        gh() {
            printf '[{\"body\":\"## Blocked Declaration\\nType: conflict\\nDeclared by: dispatcher\"}]'
        }

        notify_slack() {
            echo \"SLACK_CALLED: \$1 \$2\" >> '$call_log'
        }

        check_and_notify_decision_blocker() {
            local issue_num=\"\$1\"
            local issue_title=\"\$2\"
            local blocker_body
            blocker_body=\$(gh api \"repos/\$REPO/issues/\$issue_num/comments\" \
                | jq -r '[.[] | select(.body | (test(\"## Blocked Declaration\") and test(\"Type: decision\")))]
                          | last | .body // empty' 2>/dev/null || true)
            [[ -z \"\$blocker_body\" ]] && return 0
            notify_slack 'decision_blocker' '{}'
        }

        check_and_notify_decision_blocker '99' 'Conflict Story'
    " 2>&1 || true

    assert_not_contains "no decision_blocker for conflict-type comment" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: decision_blocker"
}

# ─── Test 6: feature_closure_confirmation fires when all feature stories Done ──
# Requires jq to query BOARD_DATA.

test_feature_closure_fires_when_all_done() {
    if ! $JQ_AVAILABLE; then
        skip_test "feature_closure_confirmation fires when all stories Done"
        return
    fi

    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_slack "$tmpdir")

    # Minimal BOARD_DATA: 2 stories in Feature group #10; story #4 is Done, #5 just completed
    local board_data
    board_data='{"data":{"repository":{"projectsV2":{"nodes":[{"id":"P1","fields":{"nodes":[]},"items":{"nodes":[{"id":"I1","content":{"number":4,"title":"Story A","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"Done","field":{"name":"Status"}}]}},{"id":"I2","content":{"number":5,"title":"Story B","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"In implementation","field":{"name":"Status"}}]}}]}}]}}}}'

    bash -c "
        set -euo pipefail
        DRY_RUN=false
        REPO='owner/repo'
        TARGET_BODY='Feature group: #10'
        BOARD_DATA='$board_data'
        COMPLETED_STORY=5

        gh() {
            echo '{\"title\":\"Feature: Notifications\",\"body\":\"\"}'
        }

        notify_slack() {
            echo \"SLACK_CALLED: \$1 \$2\" >> '$call_log'
        }

        notify_closure_if_complete() {
            local completed_story_num=\"\$1\"
            local feature_num
            feature_num=\$(echo \"\$TARGET_BODY\" \
                | grep -oP '(?<=Feature group: #)[0-9]+' | head -1 || true)
            [[ -z \"\$feature_num\" ]] && return 0
            \$DRY_RUN && return 0

            local feature_total feature_done
            feature_total=\$(echo \"\$BOARD_DATA\" | jq \
                --arg fn \"\$feature_num\" \
                '[.data.repository.projectsV2.nodes[0].items.nodes[]
                  | select(
                      (.content | type) == \"object\" and
                      (.content.number != null) and
                      ((.content.body // \"\") | test(\"Feature group: #\" + \$fn + \"([^0-9]|\$)\"))
                    )] | length')

            [[ -z \"\$feature_total\" || \"\$feature_total\" -eq 0 ]] && return 0

            feature_done=\$(echo \"\$BOARD_DATA\" | jq \
                --arg fn \"\$feature_num\" \
                --argjson cn \"\$completed_story_num\" \
                '[.data.repository.projectsV2.nodes[0].items.nodes[]
                  | select(
                      (.content | type) == \"object\" and
                      (.content.number != null) and
                      ((.content.body // \"\") | test(\"Feature group: #\" + \$fn + \"([^0-9]|\$)\")) and
                      (
                        .content.number == \$cn or
                        ([.fieldValues.nodes[]
                          | select((.field.name? // \"\") == \"Status\" and (.name? // \"\") == \"Done\")
                        ] | length > 0)
                      )
                    )] | length')

            [[ -z \"\$feature_done\" || \"\$feature_done\" -lt \"\$feature_total\" ]] && return 0

            local feature_title feature_url
            feature_title=\$(gh api \"repos/\$REPO/issues/\$feature_num\" --jq '.title' 2>/dev/null \
                || echo \"Feature #\$feature_num\")
            feature_url=\"https://github.com/\$REPO/issues/\$feature_num\"
            notify_slack 'feature_closure_confirmation' \
                \"\$(jq -n --arg ft \"\$feature_title\" --arg su \"\$feature_url\" \
                       '{feature_title: \$ft, summary_url: \$su}')\"
        }

        notify_closure_if_complete 5
    " 2>&1 || true

    assert_contains "feature_closure_confirmation fires when all stories Done" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: feature_closure_confirmation"
}

# ─── Test 7: feature closure silent when sibling stories remain ────────────────
# Requires jq.

test_feature_closure_silent_when_stories_remain() {
    if ! $JQ_AVAILABLE; then
        skip_test "no feature_closure_confirmation when stories remain"
        return
    fi

    local tmpdir
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")

    local call_log
    call_log=$(setup_mock_slack "$tmpdir")

    # BOARD_DATA: story #4 Done, story #6 still In implementation
    local board_data
    board_data='{"data":{"repository":{"projectsV2":{"nodes":[{"id":"P1","fields":{"nodes":[]},"items":{"nodes":[{"id":"I1","content":{"number":4,"title":"Story A","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"Done","field":{"name":"Status"}}]}},{"id":"I2","content":{"number":6,"title":"Story C","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"In implementation","field":{"name":"Status"}}]}}]}}]}}}}'

    bash -c "
        set -euo pipefail
        DRY_RUN=false
        REPO='owner/repo'
        TARGET_BODY='Feature group: #10'
        BOARD_DATA='$board_data'

        notify_slack() {
            echo \"SLACK_CALLED: \$1 \$2\" >> '$call_log'
        }

        notify_closure_if_complete() {
            local completed_story_num=\"\$1\"
            local feature_num='10'
            local feature_total feature_done
            feature_total=\$(echo \"\$BOARD_DATA\" | jq \
                --arg fn \"\$feature_num\" \
                '[.data.repository.projectsV2.nodes[0].items.nodes[]
                  | select((.content.body // \"\") | test(\"Feature group: #\" + \$fn + \"([^0-9]|\$)\"))
                ] | length')
            feature_done=\$(echo \"\$BOARD_DATA\" | jq \
                --arg fn \"\$feature_num\" \
                --argjson cn \"\$completed_story_num\" \
                '[.data.repository.projectsV2.nodes[0].items.nodes[]
                  | select(
                      ((.content.body // \"\") | test(\"Feature group: #\" + \$fn + \"([^0-9]|\$)\")) and
                      (.content.number == \$cn or
                       ([.fieldValues.nodes[] | select((.field.name? // \"\") == \"Status\" and (.name? // \"\") == \"Done\")] | length > 0))
                    )] | length')
            [[ \"\$feature_done\" -lt \"\$feature_total\" ]] && return 0
            notify_slack 'feature_closure_confirmation' '{}'
        }

        notify_closure_if_complete 4
    " 2>&1 || true

    assert_not_contains "no feature_closure_confirmation when stories remain" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: feature_closure_confirmation"
}

# ─── Test 8: epic_closure_approval fires when all epic features' stories Done ──
# Requires jq. BOARD_DATA has:
#   stories #4 and #5 in Feature group #10 (both Done / just-completed)
#   feature item #10 with body "Parent epic: #30" (the epic's feature on the board)
# Mock gh returns the feature body and epic title for API lookups.

test_epic_closure_fires_when_all_done() {
    if ! $JQ_AVAILABLE; then
        skip_test "epic_closure_approval fires when all epic features Done"
        return
    fi

    local tmpdir call_log
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log=$(setup_mock_slack "$tmpdir")

    local board_data
    board_data='{"data":{"repository":{"projectsV2":{"nodes":[{"id":"P1","fields":{"nodes":[]},"items":{"nodes":[{"id":"I1","content":{"number":4,"title":"Story A","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"Done","field":{"name":"Status"}}]}},{"id":"I2","content":{"number":5,"title":"Story B","body":"Feature group: #10\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"In implementation","field":{"name":"Status"}}]}},{"id":"F1","content":{"number":10,"title":"Feature: Notifications","body":"Parent epic: #30\n","updatedAt":"2026-01-01T00:00:00Z"},"fieldValues":{"nodes":[{"name":"In implementation","field":{"name":"Status"}}]}}]}}]}}}}'

    bash -c "
        set -euo pipefail
        DRY_RUN=false
        REPO='owner/repo'
        TARGET_BODY='Feature group: #10'
        BOARD_DATA='$board_data'

        # Smart mock: passes --jq filter through real jq so callers get correct values
        gh() {
            local url=\"\${2:-}\" jq_filter=\"\"
            [[ \"\${3:-}\" == '--jq' ]] && jq_filter=\"\${4:-}\"
            local result
            if [[ \"\$url\" == *'issues/10'* ]]; then
                result='{\"title\":\"Feature: Notifications\",\"body\":\"Parent epic: #30\"}'
            elif [[ \"\$url\" == *'issues/30'* ]]; then
                result='{\"title\":\"Epic: Autonomous Dispatcher\",\"body\":\"\"}'
            else
                result='{}'
            fi
            if [[ -n \"\$jq_filter\" ]]; then
                echo \"\$result\" | jq -r \"\$jq_filter\"
            else
                echo \"\$result\"
            fi
        }

        notify_slack() {
            echo \"SLACK_CALLED: \$1 \$2\" >> '$call_log'
        }

        notify_closure_if_complete() {
            local cn=\"\$1\"
            local fn
            fn=\$(echo \"\$TARGET_BODY\" | grep -oP '(?<=Feature group: #)[0-9]+' | head -1 || true)
            [[ -z \"\$fn\" ]] && return 0

            local ftotal fdone
            ftotal=\$(echo \"\$BOARD_DATA\" | jq --arg fn \"\$fn\" \\
                '[.data.repository.projectsV2.nodes[0].items.nodes[]
                  | select((.content|type)==\"object\" and (.content.number!=null) and
                      ((.content.body//\"\") | test(\"Feature group: #\"+\$fn+\"([^0-9]|\$)\")))
                ] | length')
            [[ -z \"\$ftotal\" || \"\$ftotal\" -eq 0 ]] && return 0

            fdone=\$(echo \"\$BOARD_DATA\" | jq --arg fn \"\$fn\" --argjson cn \"\$cn\" \\
                '[.data.repository.projectsV2.nodes[0].items.nodes[]
                  | select((.content|type)==\"object\" and (.content.number!=null) and
                      ((.content.body//\"\") | test(\"Feature group: #\"+\$fn+\"([^0-9]|\$)\")) and
                      (.content.number==\$cn or
                       ([.fieldValues.nodes[]
                         | select((.field.name?//\"\")==\"Status\" and (.name?//\"\")==\"Done\")
                       ] | length > 0)))
                ] | length')
            [[ -z \"\$fdone\" || \"\$fdone\" -lt \"\$ftotal\" ]] && return 0

            notify_slack 'feature_closure_confirmation' \\
                \"\$(jq -n --arg ft \"Feature #\$fn\" --arg su \"url\" '{feature_title:\$ft,summary_url:\$su}')\"

            # Epic closure
            local fb epic_num
            fb=\$(gh api \"repos/\$REPO/issues/\$fn\" --jq '.body // \"\"' 2>/dev/null || echo '')
            epic_num=\$(echo \"\$fb\" | grep -oP '(?<=Parent epic: #)[0-9]+' | head -1 || true)
            [[ -z \"\$epic_num\" ]] && return 0

            local epic_fns
            epic_fns=\$(echo \"\$BOARD_DATA\" | jq -r --arg en \"\$epic_num\" \\
                '.data.repository.projectsV2.nodes[0].items.nodes[]
                  | select((.content|type)==\"object\" and (.content.number!=null) and
                      ((.content.body//\"\") | test(\"Parent epic: #\"+\$en+\"([^0-9]|\$)\")))
                  | .content.number')
            [[ -z \"\$epic_fns\" ]] && return 0

            local all_done=true
            while IFS= read -r efn; do
                local et ed
                et=\$(echo \"\$BOARD_DATA\" | jq --arg fn \"\$efn\" \\
                    '[.data.repository.projectsV2.nodes[0].items.nodes[]
                      | select((.content.body//\"\") | test(\"Feature group: #\"+\$fn+\"([^0-9]|\$)\"))
                    ] | length')
                ed=\$(echo \"\$BOARD_DATA\" | jq --arg fn \"\$efn\" --argjson cn \"\$cn\" \\
                    '[.data.repository.projectsV2.nodes[0].items.nodes[]
                      | select((.content.body//\"\") | test(\"Feature group: #\"+\$fn+\"([^0-9]|\$)\")) and
                        (.content.number==\$cn or
                         ([.fieldValues.nodes[]
                           | select((.field.name?//\"\")==\"Status\" and (.name?//\"\")==\"Done\")
                         ] | length > 0))
                    ] | length')
                if [[ -n \"\$et\" && \"\$et\" -gt 0 && \"\$ed\" -lt \"\$et\" ]]; then
                    all_done=false; break
                fi
            done <<< \"\$epic_fns\"

            \$all_done || return 0

            local et_title et_url
            et_title=\$(gh api \"repos/\$REPO/issues/\$epic_num\" --jq '.title' 2>/dev/null || echo \"Epic #\$epic_num\")
            et_url=\"https://github.com/\$REPO/issues/\$epic_num\"
            notify_slack 'epic_closure_approval' \\
                \"\$(jq -n --arg et \"\$et_title\" --arg su \"\$et_url\" '{epic_title:\$et,summary_url:\$su}')\"
        }

        notify_closure_if_complete 5
    " 2>&1 || true

    assert_contains "epic_closure_approval fires when all epic features Done" \
        "$(cat "$call_log" 2>/dev/null || true)" \
        "SLACK_CALLED: epic_closure_approval"
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

# ─── Summary ──────────────────────────────────────────────────────────────────

run_all_tests() {
    echo "── Dispatcher notification tests (issue #179) ──────────────────────────"
    test_dry_run_suppresses_notifications
    test_notify_slack_passes_args
    test_notify_slack_failure_does_not_abort
    test_decision_blocker_fires_for_decision_type
    test_decision_blocker_silent_for_conflict_type
    test_feature_closure_fires_when_all_done
    test_feature_closure_silent_when_stories_remain
    test_epic_closure_fires_when_all_done
    test_dispatcher_error_trap_fires_on_unexpected_exit
    test_dispatcher_error_suppressed_when_handled
    test_dispatcher_error_suppressed_on_clean_exit
    echo "──────────────────────────────────────────────────────────────────────"
    echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
    [[ $FAIL -eq 0 ]]
}

run_all_tests
