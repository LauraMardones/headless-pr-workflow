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
# "/unblock". Comments are fetched with `gh api --paginate` so a
# declaration or resolving comment past the first page is not missed.
# Idempotency: a blocker already resumed (a $DECISION_BLOCKER_RESUME_MARKER
# recovery comment already posted at/after the declaration) is not
# re-triggered on a later poll while the story is actively back in
# progress.
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
#   7. Idempotency: blocker already resumed on a prior poll (recovery
#      comment already present after the declaration) -> no re-trigger,
#      even though the same qualifying /unblock comment is still in the
#      thread
#   8. Pagination: declaration + resolving comment split across two pages
#      of the comments endpoint -> still detected (gh api --paginate output
#      merged via `jq -s add`)
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

# ─── Helper: define check_and_resolve_decision_blocker_comment() exactly as
# in scripts/dispatcher-invoke.sh, wired to a mocked `gh`. Shared by all
# tests via bash -c heredocs below so each test controls what `gh` returns.

FUNCTION_DEF='
        DECISION_BLOCKER_PO_LOGIN="LauraMardones"
        DECISION_BLOCKER_RESUME_MARKER="Detected: PO comment resolving the Type: decision blocker"

        post_comment() {
            echo "POST_COMMENT: #$1" >> "$CALL_LOG"
            echo "$2" | sed "s/^/  /" >> "$CALL_LOG"
        }

        set_project_status_ready() {
            echo "SET_READY: item=$1 issue=#$2" >> "$CALL_LOG"
        }

        check_and_resolve_decision_blocker_comment() {
            local issue_num="$1" item_id="$2"
            local comments
            comments=$(gh api --paginate "repos/$REPO/issues/$issue_num/comments?per_page=100" | jq -s "add")

            local blocker_created_at
            blocker_created_at=$(echo "$comments" | jq -r '"'"'
                [.[] | select(.body | (test("## Blocked Declaration") and test("Type: decision")))]
                | last | .created_at // empty'"'"')
            [[ -z "$blocker_created_at" ]] && return 1

            local already_resumed
            already_resumed=$(echo "$comments" | jq -r \
                --arg marker "$DECISION_BLOCKER_RESUME_MARKER" \
                --arg since "$blocker_created_at" \
                '"'"'[.[] | select(
                     ((.body // "") | contains($marker)) and
                     .created_at >= $since
                   )] | length > 0'"'"')
            [[ "$already_resumed" == "true" ]] && return 1

            local resolved
            resolved=$(echo "$comments" | jq -r \
                --arg login "$DECISION_BLOCKER_PO_LOGIN" \
                --arg since "$blocker_created_at" \
                '"'"'[.[] | select(
                     (.user.login // "") == $login and
                     .created_at > $since and
                     ((.body // "") | split("\n") | any(test("^\\s*/unblock\\s*$")))
                   )] | length > 0'"'"')
            [[ "$resolved" == "true" ]] || return 1

            echo "RESOLVED: #$issue_num"
            post_comment "$issue_num" "## Recovery Comment
$DECISION_BLOCKER_RESUME_MARKER (standalone \`/unblock\` line found).
Action: status set to \"Ready for implementation\".
Next executor: review the PO'"'"'s decision in the linked comment above before resuming."
            set_project_status_ready "$item_id" "$issue_num"
            return 0
        }
'

# ─── Helper: run the function against a single-page mocked `gh` ───────────────

run_resume_check() {
    local comments_json="$1" call_log="$2"
    REPO='owner/repo' CALL_LOG="$call_log" COMMENTS_JSON="$comments_json" \
        bash -c "
        set -euo pipefail
        REPO='owner/repo'
        CALL_LOG='$call_log'
        gh() { printf '%s' '$comments_json'; }
        $FUNCTION_DEF
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
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)\nResume instruction: To resume: reply on this issue with your decision, including a line that is exactly `/unblock`."},
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

# ─── Test 7: idempotency — blocker already resumed on a prior poll -> no re-trigger ──
# Regression for the review finding: a story pulled back into "In
# implementation" after a successful resume must not be re-queued again on
# the next poll just because the old declaration and old /unblock comment
# are still in the thread.

test_idempotent_already_resumed_does_not_retrigger() {
    if ! $JQ_AVAILABLE; then
        skip_test "idempotency: already resumed -> no re-trigger"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"},
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-10T11:00:00Z",
       "body":"Go with option B.\n/unblock"},
      {"user":{"login":"dispatcher-bot"},"created_at":"2026-08-10T11:05:00Z",
       "body":"## Recovery Comment\nDetected: PO comment resolving the Type: decision blocker (standalone `/unblock` line found).\nAction: status set to \"Ready for implementation\".\nNext executor: review the PO decision in the linked comment above before resuming."}
    ]'
    output=$(run_resume_check "$comments" "$call_log")

    assert_contains "idempotency: function returns 1 on second poll" "$output" "FUNCTION_RETURNED: 1"
    assert_not_contains "idempotency: no duplicate recovery comment posted" "$(cat "$call_log")" "POST_COMMENT"
    assert_not_contains "idempotency: board not mutated again" "$(cat "$call_log")" "SET_READY"
}

# ─── Test 8: pagination — declaration and resolving comment on separate pages ─
# Simulates `gh api --paginate` emitting one JSON array per page (as it does
# for a paginated array-typed REST response); the real code slurp-merges
# them with `jq -s add` before parsing.

test_pagination_across_two_pages_still_detected() {
    if ! $JQ_AVAILABLE; then
        skip_test "pagination: declaration + resolve split across pages -> still detected"; return
    fi
    local tmpdir call_log output
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    # Page 1: 100 filler comments (older) ending with the blocker declaration.
    # Page 2: the PO's later resolving comment.
    local page1 page2
    page1=$(python3 - <<'PY'
import json
filler = [
    {"user": {"login": "someone"}, "created_at": f"2026-08-01T00:{i:02d}:00Z", "body": f"noise {i}"}
    for i in range(99)
]
declaration = {
    "user": {"login": "claude-executor"},
    "created_at": "2026-08-10T10:00:00Z",
    "body": "## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)",
}
print(json.dumps(filler + [declaration]))
PY
)
    page2='[{"user":{"login":"LauraMardones"},"created_at":"2026-08-11T09:00:00Z","body":"Go with option B.\n/unblock"}]'

    output=$(REPO='owner/repo' bash -c "
        set -euo pipefail
        REPO='owner/repo'
        CALL_LOG='$call_log'
        # gh --paginate emits each page as a separate JSON document on stdout.
        gh() { printf '%s' '$page1'; printf '%s' '$page2'; }
        $FUNCTION_DEF
        if check_and_resolve_decision_blocker_comment '42' 'ITEM_ID_123'; then
            echo 'FUNCTION_RETURNED: 0'
        else
            echo 'FUNCTION_RETURNED: 1'
        fi
    " 2>&1)

    assert_contains "pagination: function returns 0 (both pages merged)" "$output" "FUNCTION_RETURNED: 0"
    assert_contains "pagination: board set to Ready for implementation" "$(cat "$call_log")" "SET_READY: item=ITEM_ID_123 issue=#42"
}

# ─── Test 9: drift guard — the tested copy above must match production ────────
# The scenarios above exercise a copy of check_and_resolve_decision_blocker_comment()
# embedded in this test file, not the live function in scripts/dispatcher-invoke.sh
# directly (the script has no source-safe guard: it runs argument parsing and
# real dispatch logic at top level as soon as it's loaded, so sourcing it here
# would require mocking the entire pre-flight/execution pipeline). To keep the
# copy from silently diverging from production, assert that production still
# contains the exact load-bearing fragments the tests above rely on: paginated
# fetch, the idempotency guard, the standalone-line regex, and PO authorship.

test_production_function_matches_tested_logic() {
    local script_dir real_fn
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    real_fn=$(sed -n '/^check_and_resolve_decision_blocker_comment() {/,/^}/p' \
        "$script_dir/scripts/dispatcher-invoke.sh")

    assert_contains "drift guard: production paginates the comments fetch" "$real_fn" \
        'gh api --paginate "repos/$REPO/issues/$issue_num/comments?per_page=100"'
    assert_contains "drift guard: production merges pages via jq slurp" "$real_fn" \
        'jq -s'
    assert_contains "drift guard: production has an idempotency (already_resumed) guard" "$real_fn" \
        'already_resumed=$(echo "$comments" | jq -r'
    assert_contains "drift guard: production matches a standalone /unblock line" "$real_fn" \
        'test("^\\s*/unblock\\s*$")'
    assert_contains "drift guard: production requires PO authorship" "$real_fn" \
        '(.user.login // "") == $login'
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
    test_idempotent_already_resumed_does_not_retrigger
    test_pagination_across_two_pages_still_detected
    test_production_function_matches_tested_logic
    echo "──────────────────────────────────────────────────────────────────────"
    echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
    [[ $FAIL -eq 0 ]]
}

run_all_tests
