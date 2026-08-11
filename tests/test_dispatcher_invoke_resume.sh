#!/usr/bin/env bash
# tests/test_dispatcher_invoke_resume.sh
#
# Tests for comment-triggered resume of decision blockers in
# scripts/dispatcher-invoke.sh (issue #262 — Epic #160 Success Criterion #7).
#
# Unlike other tests/test_dispatcher_invoke_*.sh files, this file does NOT
# hand-copy the functions under test into the test harness (a prior version
# of this file did, and review flagged that the copy could silently diverge
# from production while staying green). scripts/dispatcher-invoke.sh has no
# source-safe guard — it runs argument parsing and real dispatch logic at
# top level as soon as it's loaded — so it cannot be `source`d directly.
# Instead, extract_function() below pulls the exact, current text of
# check_and_resolve_decision_blocker_comment(), set_project_status_ready(),
# and their constants straight out of the production file at test-run time
# and evals them verbatim inside a mocked harness (mocked `gh` and
# `post_comment`, the actual I/O boundary). This exercises the real
# production logic byte-for-byte; drift is structurally impossible since
# there is no separate copy to drift from.
#
# check_and_resolve_decision_blocker_comment() detects a PO comment resolving
# a "## Blocked Declaration (Type: decision)" and resumes the story
# immediately (board status set to "Ready for implementation"), instead of
# waiting for the time-based stale-recovery loop. Resolving-comment rule: a
# comment authored by the PO ($DECISION_BLOCKER_PO_LOGIN), posted strictly
# after the blocker declaration, containing a standalone line that trims to
# exactly "/unblock". Comments are fetched with `gh api --paginate` so a
# declaration or resolving comment past the first page is not missed.
#
# Idempotency uses a two-phase CLAIM/CONFIRM marker pair rather than a
# single post-then-mutate or mutate-then-post ordering (either ordering
# alone leaves a window where one of the two non-atomic external writes —
# the board mutation, the marker comment — succeeds and the other doesn't):
#   - CLAIM ($DECISION_BLOCKER_CLAIM_MARKER) is posted before the mutation
#     is attempted (skipped on retry if already posted for this
#     declaration); a claim failing to post is safe, nothing was mutated
#     yet.
#   - CONFIRM ($DECISION_BLOCKER_RESUME_MARKER) is posted only after
#     set_project_status_ready() confirms the mutation happened, and only
#     CONFIRM gates "already_resumed" — a bare claim never suppresses a
#     retry, so a failed mutation (unresolved Status field/option IDs, or a
#     failed GraphQL call) is retried on the next poll instead of being
#     silently and permanently skipped.
#
# Scenarios covered:
#   1. Regression: no decision blocker declared at all -> no resume (falls
#      through unchanged to the existing stale-recovery path)
#   2. Positive: decision blocker + later PO comment with decision text and
#      a standalone /unblock line -> resume (CLAIM then CONFIRM comments
#      posted, board status set to "Ready for implementation")
#   3. Negative: PO comment uses "unblock"/"unblocked" in ordinary prose,
#      with no line that trims to exactly /unblock -> no resume
#   4. Negative: non-PO author posts a standalone /unblock line -> no resume
#   5. Negative: PO's /unblock comment was posted BEFORE the blocker
#      declaration -> no resume
#   6. Negative: decision blocker declared, no comment follows at all ->
#      no resume
#   7. Idempotency: blocker already CONFIRMED-resumed on a prior poll -> no
#      re-trigger, even though the same qualifying /unblock comment is
#      still in the thread
#   8. Pagination: declaration + resolving comment split across two pages
#      of the comments endpoint -> still detected (gh api --paginate output
#      merged via `jq -s add`)
#   9. Board transition failure (missing Status field/option IDs) -> CLAIM
#      posted, but no CONFIRM marker -> safe to retry
#  10. Board transition failure (GraphQL mutation call fails) -> same as
#      above, distinct failure path exercised directly
#  11. Retry: a failed transition (scenario 10) succeeds on a later poll
#      once the mutation itself succeeds, and does not re-post a second,
#      duplicate CLAIM comment
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

# ─── Extract the real production functions/constants under test ───────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCHER_SCRIPT="$SCRIPT_DIR/scripts/dispatcher-invoke.sh"

extract_function() {
    sed -n "/^$1() {/,/^}/p" "$DISPATCHER_SCRIPT"
}

REAL_CONSTANTS=$(grep -E '^DECISION_BLOCKER_(PO_LOGIN|CLAIM_MARKER|RESUME_MARKER)=' "$DISPATCHER_SCRIPT")
REAL_SETTER_FN=$(extract_function set_project_status_ready)
REAL_CHECK_FN=$(extract_function check_and_resolve_decision_blocker_comment)

if [[ -z "$REAL_CONSTANTS" || -z "$REAL_SETTER_FN" || -z "$REAL_CHECK_FN" ]]; then
    echo "FATAL: could not extract check_and_resolve_decision_blocker_comment(), set_project_status_ready(), or DECISION_BLOCKER_* constants from $DISPATCHER_SCRIPT — a rename or reformat broke this test's extraction. Update extract_function()/REAL_CONSTANTS above to match." >&2
    exit 1
fi

# ─── Build the runner script once: mocked gh/post_comment (the I/O boundary)
# plus the verbatim-extracted production logic. Per-test scenarios are
# driven entirely by environment variables read at the top (COMMENTS_JSON,
# MOCK_GRAPHQL_RC, STATUS_FIELD_ID, READY_FOR_IMPL_OPTION_ID, DRY_RUN,
# CALL_LOG), never by editing this script.

RUNNER="$GLOBAL_TMP/runner.sh"

cat > "$RUNNER" <<'RUNNER_HEADER'
#!/usr/bin/env bash
set -euo pipefail

REPO='owner/repo'
PROJECT_ID='PROJECT_1'
STATUS_FIELD_ID="${STATUS_FIELD_ID-FIELD_1}"
READY_FOR_IMPL_OPTION_ID="${READY_FOR_IMPL_OPTION_ID-OPT_READY}"
DRY_RUN="${DRY_RUN:-false}"

post_comment() {
    echo "POST_COMMENT: #$1" >> "$CALL_LOG"
    echo "$2" | sed 's/^/  /' >> "$CALL_LOG"
    if [[ "$2" == *"$DECISION_BLOCKER_RESUME_MARKER"* ]]; then
        return "${MOCK_CONFIRM_RC:-0}"
    fi
}

# Dispatches on the mocked call shape used by production code:
#   gh api --paginate "repos/.../comments?per_page=100"  -> comments fetch
#   gh api graphql -f query=... ...                       -> board mutation
gh() {
    if [[ "$1" == "api" && "$2" == "--paginate" ]]; then
        printf '%s' "$COMMENTS_JSON"
        return 0
    elif [[ "$1" == "api" && "$2" == "graphql" ]]; then
        return "${MOCK_GRAPHQL_RC:-0}"
    fi
    echo "unexpected gh invocation: $*" >&2
    return 1
}
RUNNER_HEADER

{
    printf '\n# ─── Extracted verbatim from %s ───\n' "$DISPATCHER_SCRIPT"
    printf '%s\n' "$REAL_CONSTANTS"
    printf '\n%s\n' "$REAL_SETTER_FN"
    printf '\n%s\n' "$REAL_CHECK_FN"
} >> "$RUNNER"

cat >> "$RUNNER" <<'RUNNER_FOOTER'

if check_and_resolve_decision_blocker_comment "${TEST_ISSUE_NUM:-42}" "${TEST_ITEM_ID:-ITEM_ID_123}" "${TEST_PROJECT_UPDATED_AT:-2026-08-10T09:00:00Z}"; then
    echo 'FUNCTION_RETURNED: 0'
else
    echo 'FUNCTION_RETURNED: 1'
fi
RUNNER_FOOTER

# ─── Helper: run the real function against a scenario ─────────────────────────
# Positional: comments_json call_log [mock_graphql_rc] [status_field_id] [ready_option_id] [dry_run]
# status_field_id="" simulates the "unresolved Status field/option IDs" failure path.

run_resume_check() {
    local comments_json="$1" call_log="$2"
    local mock_graphql_rc="${3:-0}"
    local status_field_id="${4-FIELD_1}"
    local ready_option_id="${5-OPT_READY}"
    local dry_run="${6:-false}"

    CALL_LOG="$call_log" \
    COMMENTS_JSON="$comments_json" \
    MOCK_GRAPHQL_RC="$mock_graphql_rc" \
    STATUS_FIELD_ID="$status_field_id" \
    READY_FOR_IMPL_OPTION_ID="$ready_option_id" \
    DRY_RUN="$dry_run" \
    TEST_PROJECT_UPDATED_AT="${TEST_PROJECT_UPDATED_AT:-2026-08-10T09:00:00Z}" \
    MOCK_CONFIRM_RC="${MOCK_CONFIRM_RC:-0}" \
        bash "$RUNNER" 2>&1
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
    assert_not_contains "no blocker: nothing posted" "$(cat "$call_log")" "POST_COMMENT"
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
    assert_contains "positive: CLAIM comment posted" "$(cat "$call_log")" "Attempting to resolve the Type: decision blocker"
    assert_contains "positive: CONFIRM comment posted" "$(cat "$call_log")" "Detected: PO comment resolving the Type: decision blocker"
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
    assert_not_contains "negative (prose word): nothing posted" "$(cat "$call_log")" "POST_COMMENT"
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
    assert_not_contains "negative (non-PO): nothing posted" "$(cat "$call_log")" "POST_COMMENT"
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
    assert_not_contains "negative (predates declaration): nothing posted" "$(cat "$call_log")" "POST_COMMENT"
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
    assert_not_contains "negative (no follow-up): nothing posted" "$(cat "$call_log")" "POST_COMMENT"
}

# ─── Test 7: idempotency — blocker already CONFIRMED-resumed -> no re-trigger ─
# Regression for the review finding: a story pulled back into "In
# implementation" after a successful resume must not be re-queued again on
# the next poll just because the old declaration and old /unblock comment
# are still in the thread.

test_idempotent_already_confirmed_does_not_retrigger() {
    if ! $JQ_AVAILABLE; then
        skip_test "idempotency: already CONFIRMED -> no re-trigger"; return
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

    assert_contains "idempotency: function returns 1 on a later poll" "$output" "FUNCTION_RETURNED: 1"
    assert_not_contains "idempotency: nothing posted again" "$(cat "$call_log")" "POST_COMMENT"
}

# ─── Test 8: pagination — declaration and resolving comment on separate pages ─
# Simulates `gh api --paginate` emitting one JSON array per page (as it does
# for a paginated array-typed REST response); the real code slurp-merges
# them with `jq -s add` before parsing. Overrides the mocked `gh` directly
# (rather than using COMMENTS_JSON) since it needs to emit two separate JSON
# documents on stdout.

test_pagination_across_two_pages_still_detected() {
    if ! $JQ_AVAILABLE; then
        skip_test "pagination: declaration + resolve split across pages -> still detected"; return
    fi
    local tmpdir call_log output
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

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

    cat > "$tmpdir/paginated_runner.sh" <<RUNNER_PAGINATED
#!/usr/bin/env bash
set -euo pipefail
REPO='owner/repo'
PROJECT_ID='PROJECT_1'
STATUS_FIELD_ID='FIELD_1'
READY_FOR_IMPL_OPTION_ID='OPT_READY'
DRY_RUN=false

post_comment() {
    echo "POST_COMMENT: #\$1" >> "\$CALL_LOG"
}

gh() {
    if [[ "\$1" == "api" && "\$2" == "--paginate" ]]; then
        printf '%s' '$page1'
        printf '%s' '$page2'
        return 0
    elif [[ "\$1" == "api" && "\$2" == "graphql" ]]; then
        return 0
    fi
    return 1
}

$REAL_CONSTANTS

$REAL_SETTER_FN

$REAL_CHECK_FN

if check_and_resolve_decision_blocker_comment '42' 'ITEM_ID_123' '2026-08-10T09:00:00Z'; then
    echo 'FUNCTION_RETURNED: 0'
else
    echo 'FUNCTION_RETURNED: 1'
fi
RUNNER_PAGINATED

    output=$(CALL_LOG="$call_log" bash "$tmpdir/paginated_runner.sh" 2>&1)

    assert_contains "pagination: function returns 0 (both pages merged)" "$output" "FUNCTION_RETURNED: 0"
    assert_contains "pagination: CONFIRM comment posted" "$(cat "$call_log")" "POST_COMMENT: #42"
}

# ─── Test 9: board transition failure — unresolved Status field/option IDs ────
# CLAIM is still posted (safe — nothing mutated), but no CONFIRM marker, so a
# later poll with the same input can retry.

test_board_transition_failure_missing_ids() {
    if ! $JQ_AVAILABLE; then
        skip_test "board transition failure: missing field/option IDs"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"},
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-11T09:00:00Z",
       "body":"Go with option B.\n/unblock"}
    ]'
    # status_field_id="" simulates get_status_option_id() failing to resolve
    # the Status field on the project — the earliest return-1 path inside
    # set_project_status_ready().
    output=$(run_resume_check "$comments" "$call_log" 0 "" "")

    assert_contains "missing IDs: function returns 1" "$output" "FUNCTION_RETURNED: 1"
    assert_contains "missing IDs: CLAIM comment posted" "$(cat "$call_log")" "Attempting to resolve the Type: decision blocker"
    assert_not_contains "missing IDs: no CONFIRM comment posted" "$(cat "$call_log")" "Detected: PO comment resolving the Type: decision blocker"
}

# ─── Test 10: board transition failure — GraphQL mutation call fails ──────────
# Distinct failure path from Test 9: field/option IDs resolve fine, but the
# `gh api graphql` mutation call itself returns non-zero.

test_board_transition_failure_graphql_call_fails() {
    if ! $JQ_AVAILABLE; then
        skip_test "board transition failure: GraphQL mutation fails"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"},
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-11T09:00:00Z",
       "body":"Go with option B.\n/unblock"}
    ]'
    output=$(run_resume_check "$comments" "$call_log" 1)

    assert_contains "GraphQL failure: function returns 1" "$output" "FUNCTION_RETURNED: 1"
    assert_contains "GraphQL failure: CLAIM comment posted" "$(cat "$call_log")" "Attempting to resolve the Type: decision blocker"
    assert_not_contains "GraphQL failure: no CONFIRM comment posted" "$(cat "$call_log")" "Detected: PO comment resolving the Type: decision blocker"
}

# ─── Test 11: a failed transition is retried and succeeds without duplicate CLAIM ──
# Proves the failure path in Test 10 is recoverable: with the exact same
# comments thread (the CLAIM from the failed attempt is now part of it,
# nothing else changed), a later poll whose GraphQL call succeeds resumes
# the story normally and does not post a second CLAIM comment.

test_failed_transition_is_retried_without_duplicate_claim() {
    if ! $JQ_AVAILABLE; then
        skip_test "failed transition retried -> succeeds without duplicate CLAIM"; return
    fi
    local tmpdir call_log_attempt1 call_log_attempt2 comments_before_retry comments_after_claim output1 output2
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log_attempt1="$tmpdir/calls_1.log"
    call_log_attempt2="$tmpdir/calls_2.log"
    touch "$call_log_attempt1" "$call_log_attempt2"

    comments_before_retry='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"},
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-11T09:00:00Z",
       "body":"Go with option B.\n/unblock"}
    ]'

    # Poll 1: GraphQL mutation fails. CLAIM gets posted, no CONFIRM.
    output1=$(run_resume_check "$comments_before_retry" "$call_log_attempt1" 1)
    assert_contains "retry: first poll fails" "$output1" "FUNCTION_RETURNED: 1"
    assert_contains "retry: first poll posts CLAIM" "$(cat "$call_log_attempt1")" "Attempting to resolve the Type: decision blocker"

    # Poll 2: the thread now includes the CLAIM comment from poll 1 (as it
    # would on GitHub); the mutation succeeds this time.
    comments_after_claim='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor\nBlocks: #42\nUnblocked when: PO decides\nOwner: PO (@LauraMardones)"},
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-11T09:00:00Z",
       "body":"Go with option B.\n/unblock"},
      {"user":{"login":"dispatcher-bot"},"created_at":"2026-08-11T09:05:00Z",
       "body":"## Recovery Comment\nAttempting to resolve the Type: decision blocker (standalone `/unblock` line found); attempting the board transition to \"Ready for implementation\" now.\nProject item version before resume: 2026-08-10T09:00:00Z"}
    ]'
    output2=$(run_resume_check "$comments_after_claim" "$call_log_attempt2" 0)

    assert_contains "retry: second poll succeeds" "$output2" "FUNCTION_RETURNED: 0"
    local post_comment_calls
    post_comment_calls=$(grep -c '^POST_COMMENT: #42$' "$call_log_attempt2" || true)
    if [[ "$post_comment_calls" -eq 1 ]]; then
        echo "PASS: retry: second poll posts exactly one comment (no duplicate CLAIM)"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: retry: second poll posts exactly one comment (no duplicate CLAIM)"
        echo "      Expected exactly 1 POST_COMMENT call, found: $post_comment_calls"
        FAIL=$(( FAIL + 1 ))
    fi
    assert_contains "retry: second poll posts CONFIRM" "$(cat "$call_log_attempt2")" "Detected: PO comment resolving the Type: decision blocker"
}


# ─── Test 12: successful mutation + failed CONFIRM stays consumed after redispatch ──
# The version recorded by CLAIM is the pre-mutation ProjectV2 item version.
# A successful Ready transition followed by redispatch changes that version,
# proving the old /unblock was consumed even when CONFIRM could not be posted.

test_confirm_failure_does_not_requeue_after_redispatch() {
    if ! $JQ_AVAILABLE; then
        skip_test "CONFIRM failure after success does not requeue after redispatch"; return
    fi
    local tmpdir call_log output comments
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    call_log="$tmpdir/calls.log"
    touch "$call_log"

    comments='[
      {"user":{"login":"claude-executor"},"created_at":"2026-08-10T10:00:00Z",
       "body":"## Blocked Declaration\nType: decision\nDeclared by: executor"},
      {"user":{"login":"LauraMardones"},"created_at":"2026-08-11T09:00:00Z",
       "body":"Go with option B.\n/unblock"},
      {"user":{"login":"dispatcher-bot"},"created_at":"2026-08-11T09:05:00Z",
       "body":"## Recovery Comment\nAttempting to resolve the Type: decision blocker.\nProject item version before resume: 2026-08-10T09:00:00Z"}
    ]'

    # Poll 1 is represented by the same declaration/unblock without the CLAIM:
    # the board mutation succeeds, then the CONFIRM write fails.
    local first_log first_output initial_comments
    first_log="$tmpdir/first.log"
    touch "$first_log"
    initial_comments=$(echo "$comments" | jq '.[0:2]')
    MOCK_CONFIRM_RC=1 first_output=$(run_resume_check "$initial_comments" "$first_log" 0)
    assert_contains "CONFIRM failure: mutation succeeded but function reports incomplete marker" "$first_output" "FUNCTION_RETURNED: 1"
    assert_contains "CONFIRM failure: CLAIM was posted first" "$(cat "$first_log")" "Project item version before resume: 2026-08-10T09:00:00Z"

    # Poll 2 follows the successful Ready transition and redispatch. The changed
    # ProjectV2 item version proves the prior CLAIM was consumed.
    TEST_PROJECT_UPDATED_AT='2026-08-11T09:10:00Z' \
        output=$(run_resume_check "$comments" "$call_log" 0)

    assert_contains "CONFIRM failure/redispatch: old unblock is not consumed again" "$output" "FUNCTION_RETURNED: 1"
    assert_contains "CONFIRM failure/redispatch: changed version recognized" "$output" "prior resume claim already changed the project item"
    assert_not_contains "CONFIRM failure/redispatch: no comment posted" "$(cat "$call_log")" "POST_COMMENT"
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
    test_idempotent_already_confirmed_does_not_retrigger
    test_pagination_across_two_pages_still_detected
    test_board_transition_failure_missing_ids
    test_board_transition_failure_graphql_call_fails
    test_failed_transition_is_retried_without_duplicate_claim
    test_confirm_failure_does_not_requeue_after_redispatch
    echo "──────────────────────────────────────────────────────────────────────"
    echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
    [[ $FAIL -eq 0 ]]
}

run_all_tests
