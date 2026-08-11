#!/usr/bin/env bash
# tests/test_dispatcher_invoke_resume.sh
#
# Tests for comment-triggered resume of decision blockers in
# scripts/dispatcher-invoke.sh (issue #262 — Epic #160 Success Criterion #7).
#
# check_and_resolve_decision_blocker_comment() detects a PO comment resolving
# a "## Blocked Declaration (Type: decision)" and resumes the story
# immediately (recovery comment + board status set to "Ready for
# implementation"), instead of waiting for the time-based stale-recovery
# loop. Resolving-comment rule: a comment authored by the PO
# ($DECISION_BLOCKER_PO_LOGIN), posted strictly after the blocker
# declaration, containing a standalone line that trims to exactly
# "/unblock".
#
# Scenarios covered:
#   1. Regression: no decision blocker declared at all -> no resume (falls
#      through unchanged to the existing stale-recovery path)
#   2. Positive: decision blocker + later PO comment with decision text and
#      a standalone /unblock line -> resume (recovery comment posted, board
#      status set to "Ready for implementation")
#   3. Negative: PO comment uses "unblock"/"unblocked" in ordinary prose,
#      with no line that trims to exactly /unblock -> no resume
#   4. Negative: non-PO author posts a standalone /unblock line -> no resume
#   5. Negative: PO's /unblock comment was posted BEFORE the blocker
#      declaration -> no resume
#   6. Negative: decision blocker declared, no comment follows at all ->
#      no resume
#
# Usage: bash tests/test_dispatcher_invoke_resume.sh
# Requires: bash 4+, jq (skips gracefully if jq not available)

set -euo pipefail

PASS=0
FAIL=0
SKIP=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

JQ_AVAILABLE=false
command -v jq >/dev/null 2>&1 && JQ_AVAILABLE=true

# ─── Test harness ─────────────────────────────────────────────────────────────

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
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
    local label="$1" haystack="$2" needle="$3"
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
    local label="$1" reason="${2:-jq not available}"
    echo "SKIP: $label ($reason)"
    SKIP=$(( SKIP + 1 ))
}

# ─── Helper: run check_and_resolve_decision_blocker_comment against a mocked
# `gh` that returns the given comments JSON array, capturing side effects
# (post_comment / set_project_status_ready calls) to a log file. Mirrors the
# real implementation in scripts/dispatcher-invoke.sh exactly.

run_resume_check() {
    local comments_json="$1" call_log="$2"
    bash -c "
        set -euo pipefail
        REPO='owner/repo'
        DECISION_BLOCKER_PO_LOGIN='LauraMardones'

        gh() { printf '%s' '$comments_json'; }

        post_comment() {
            echo \"POST_COMMENT: #\$1\" >> '$call_log'
            echo \"\$2\" | sed 's/^/  /' >> '$call_log'
        }

        set_project_status_ready() {
            echo \"SET_READY: item=\$1 issue=#\$2\" >> '$call_log'
        }

        check_and_resolve_decision_blocker_comment() {
            local issue_num=\"\$1\" item_id=\"\$2\"
            local comments
            comments=\$(gh api \"repos/\$REPO/issues/\$issue_num/comments?per_page=100\")

            local blocker_created_at
            blocker_created_at=\$(echo \"\$comments\" | jq -r '
                [.[] | select(.body | (test(\"## Blocked Declaration\") and test(\"Type: decision\")))]
                | last | .created_at // empty')
            [[ -z \"\$blocker_created_at\" ]] && return 1

            local resolved
            resolved=\$(echo \"\$comments\" | jq -r \
                --arg login \"\$DECISION_BLOCKER_PO_LOGIN\" \
                --arg since \"\$blocker_created_at\" \
                '[.[] | select(
                     (.user.login // \"\") == \$login and
                     .created_at > \$since and
                     ((.body // \"\") | split(\"\n\") | any(test(\"^\\\\s*/unblock\\\\s*\$\")))
                   )] | length > 0')
            [[ \"\$resolved\" == \"true\" ]] || return 1

            echo \"RESOLVED: #\$issue_num\"
            post_comment \"\$issue_num\" '## Recovery Comment
Detected: PO comment resolving the Type: decision blocker (standalone \`/unblock\` line found).
Action: status set to \"Ready for implementation\".
Next executor: review the PO'\''s decision in the linked comment above before resuming.'
            set_project_status_ready \"\$item_id\" \"\$issue_num\"
            return 0
        }

        if check_and_resolve_decision_blocker_comment '42' 'ITEM_ID_123'; then
            echo 'FUNCTION_RETURNED: 0'
        else
            echo 'FUNCTION_RETURNED: 1'
        fi
    " 2>&1
}

# ─── Test 1: no decision blocker declared at all -> no resume ─────────────────

test_no_blocker_no_resume() {
    if ! $JQ_AVAILABLE; then
        skip_test "no decision blocker declared -> no resume"; return
    fi
    local tmpdir call_log output
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    output=$(run_resume_check '[]' "$call_log")

    assert_contains "no blocker: function returns 1 (falls through)" "$output" "FUNCTION_RETURNED: 1"
    assert_not_contains "no blocker: board not mutated" "$(cat "$call_log")" "SET_READY"
}

# ─── Test 2: positive case — decision + standalone /unblock line -> resume ────

test_positive_resume_on_unblock_line() {
    if ! $JQ_AVAILABLE; then
        skip_test "positive: decision + /unblock line -> resume"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"},
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-11T09:00:00Z",
       "body":"Go with option B, it fits the existing convention.\n/unblock"}
    ]'
    output=$(run_resume_check "$comments" "$call_log")

    assert_contains "positive: function returns 0" "$output" "FUNCTION_RETURNED: 0"
    assert_contains "positive: recovery comment posted" "$(cat "$call_log")" "POST_COMMENT: #42"
    assert_contains "positive: board set to Ready for implementation" "$(cat "$call_log")" "SET_READY: item=ITEM_ID_123 issue=#42"
}

# ─── Test 3: "unblocked" in ordinary prose, no standalone line -> no resume ───
# The exact counterexample from the issue and docs/PROJECT-STATUS.md.

test_negative_prose_word_does_not_trigger() {
    if ! $JQ_AVAILABLE; then
        skip_test "negative: 'unblocked' in prose -> no resume"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"},
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-11T09:00:00Z",
       "body":"Should we consider the account unblocked if the user has marked the red checkbox?"}
    ]'
    output=$(run_resume_check "$comments" "$call_log")

    assert_contains "negative (prose word): function returns 1" "$output" "FUNCTION_RETURNED: 1"
    assert_not_contains "negative (prose word): board not mutated" "$(cat "$call_log")" "SET_READY"
}

# ─── Test 4: non-PO author posts a standalone /unblock line -> no resume ──────

test_negative_non_po_author_does_not_trigger() {
    if ! $JQ_AVAILABLE; then
        skip_test "negative: non-PO author -> no resume"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"},
      {"user":{"login":"some-other-contributor"},"created_at":"2026-08-11T09:00:00Z",
       "body":"I think this is fine.\n/unblock"}
    ]'
    output=$(run_resume_check "$comments" "$call_log")

    assert_contains "negative (non-PO): function returns 1" "$output" "FUNCTION_RETURNED: 1"
    assert_not_contains "negative (non-PO): board not mutated" "$(cat "$call_log")" "SET_READY"
}

# ─── Test 5: PO's /unblock comment predates the blocker declaration -> no resume ──

test_negative_comment_before_declaration_does_not_trigger() {
    if ! $JQ_AVAILABLE; then
        skip_test "negative: comment predates declaration -> no resume"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-09T09:00:00Z",
       "body":"Unrelated earlier comment.\n/unblock"},
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"}
    ]'
    output=$(run_resume_check "$comments" "$call_log")

    assert_contains "negative (predates declaration): function returns 1" "$output" "FUNCTION_RETURNED: 1"
    assert_not_contains "negative (predates declaration): board not mutated" "$(cat "$call_log")" "SET_READY"
}

# ─── Test 6: decision blocker declared, no comment follows at all -> no resume ────

test_negative_no_followup_comment_does_not_trigger() {
    if ! $JQ_AVAILABLE; then
        skip_test "negative: no follow-up comment -> no resume"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"}
    ]'
    output=$(run_resume_check "$comments" "$call_log")

    assert_contains "negative (no follow-up): function returns 1" "$output" "FUNCTION_RETURNED: 1"
    assert_not_contains "negative (no follow-up): board not mutated" "$(cat "$call_log")" "SET_READY"
}

# ─── Summary ──────────────────────────────────────────────────────────────────

run_all_tests() {
    echo "── Dispatcher decision-blocker comment-resume tests (issue #262) ───────"
    test_no_blocker_no_resume
    test_positive_resume_on_unblock_line
    test_negative_prose_word_does_not_trigger
    test_negative_non_po_author_does_not_trigger
    test_negative_comment_before_declaration_does_not_trigger
    test_negative_no_followup_comment_does_not_trigger
    echo "──────────────────────────────────────────────────────────────────────"
    echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
    [[ $FAIL -eq 0 ]]
}

run_all_tests
