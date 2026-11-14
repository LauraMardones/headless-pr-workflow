#!/usr/bin/env bash
# tests/test_dispatcher_invoke_resume.sh
#
# The resume of a decision blocker after the PO's `/unblock` (issue #262,
# Epic #160 Success Criterion #7), as it works since issue #478.
#
# Before #478 the dispatcher moved the story's board card after a resolving
# `/unblock` line and posted a CLAIM and a CONFIRM comment around that write.
# The scheduler and the resume pass decide from facts since #475, so the move
# changed no session. #478 removes it: the dispatcher writes no board status,
# and the `/unblock` line itself is the fact. The invoke script still reads it
# with latest_decision_blocker_json() (used by notify_open_decision_blocker and
# the no-push fix-round check); `hpw dispatcher facts` reads it for the
# scheduler.
#
# Like the earlier version, this file does not copy the function under test:
# extract_function() pulls the current text of latest_decision_blocker_json()
# out of scripts/dispatcher-invoke.sh and evals it in a harness with a mocked
# `gh`, the I/O boundary.
#
# Scenarios:
#   1. No decision blocker -> nothing (no resume fact)
#   2. Decision blocker + later PO comment with a standalone /unblock line ->
#      unblocked
#   3. PO says "unblock"/"unblocked" in prose only -> not unblocked
#   4. A non-PO author posts /unblock -> not unblocked
#   5. The PO's /unblock comes before the blocker -> not unblocked
#   6. Blocker with nothing after it -> not unblocked
#   7. Pagination: blocker and /unblock on two pages -> unblocked
#   8. The invoke script writes no board status for the resume: no status
#      function, no CLAIM/CONFIRM marker, no board-status write at all
#
# Usage: bash tests/test_dispatcher_invoke_resume.sh
# Requires: bash 4+, jq (skips gracefully if jq not available)

set -euo pipefail

PASS=0
FAIL=0
SKIP=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

check() {
    local label="$1" actual="$2" expected="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        echo "      expected: $expected"
        echo "      actual:   $actual"
        FAIL=$(( FAIL + 1 ))
    fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCHER_SCRIPT="$SCRIPT_DIR/scripts/dispatcher-invoke.sh"

# ─── 8. No board status write for the resume (static) ────────────────────────

for retired in set_project_status set_project_status_checked set_project_status_ready board_status_kept \
               check_and_resolve_decision_blocker_comment decision_blocker_claim_body \
               decision_blocker_resume_body DECISION_BLOCKER_CLAIM_MARKER DECISION_BLOCKER_RESUME_MARKER; do
    if grep -q "$retired" "$DISPATCHER_SCRIPT"; then
        check "invoke script has no $retired" "present" "absent"
    else
        check "invoke script has no $retired" "absent" "absent"
    fi
done
if grep -q "board-status" "$DISPATCHER_SCRIPT"; then
    check "invoke script sends no board-status write" "present" "absent"
else
    check "invoke script sends no board-status write" "absent" "absent"
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "SKIP: the /unblock scenarios (jq not available)"
    SKIP=$(( SKIP + 7 ))
    echo ""
    echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
    [[ $FAIL -eq 0 ]]
    exit $?
fi

# ─── The real function, in a harness with a mocked gh ────────────────────────

extract_function() {
    sed -n "/^$1() {/,/^}/p" "$DISPATCHER_SCRIPT"
}

REAL_CONSTANT=$(grep -E '^DECISION_BLOCKER_PO_LOGIN=' "$DISPATCHER_SCRIPT")
REAL_FN=$(extract_function latest_decision_blocker_json)
if [[ -z "$REAL_CONSTANT" || -z "$REAL_FN" ]]; then
    echo "FATAL: could not extract latest_decision_blocker_json() or DECISION_BLOCKER_PO_LOGIN from $DISPATCHER_SCRIPT" >&2
    exit 1
fi

RUNNER="$GLOBAL_TMP/runner.sh"
{
    cat <<'HEADER'
#!/usr/bin/env bash
set -euo pipefail
REPO='owner/repo'
# gh api --paginate prints one JSON array per page; the function merges them.
gh() {
    if [[ "$1" == "api" && "$2" == "--paginate" ]]; then
        printf '%s' "$COMMENTS_PAGES"
        return 0
    fi
    echo "unexpected gh invocation: $*" >&2
    return 1
}
HEADER
    printf '%s\n%s\n' "$REAL_CONSTANT" "$REAL_FN"
    echo 'latest_decision_blocker_json 42 | jq -r ".unblocked | tostring"'
} > "$RUNNER"

unblocked_of() {
    COMMENTS_PAGES="$1" bash "$RUNNER"
}

PO="LauraMardones"
BLOCKER='{"user":{"login":"dispatcher-bot","type":"Bot"},"author_association":"NONE","created_at":"2026-08-10T10:00:00Z","body":"## Blocked Declaration\nType: decision\nBlocks: #42"}'
po_comment() { printf '{"user":{"login":"%s","type":"User"},"author_association":"OWNER","created_at":"%s","body":"%s"}' "$PO" "$1" "$2"; }
other_comment() { printf '{"user":{"login":"someone","type":"User"},"author_association":"NONE","created_at":"%s","body":"%s"}' "$1" "$2"; }

check "1. no decision blocker gives no resume fact" \
    "$(unblocked_of "[$(po_comment 2026-08-10T11:00:00Z '/unblock')]")" ""
check "2. a standalone PO /unblock after the blocker resolves it" \
    "$(unblocked_of "[$BLOCKER,$(po_comment 2026-08-10T11:00:00Z 'Decision: option A.\n/unblock')]")" "true"
check "3. unblock in prose does not resolve it" \
    "$(unblocked_of "[$BLOCKER,$(po_comment 2026-08-10T11:00:00Z 'this is unblocked now, please unblock it')]")" "false"
check "4. a non-PO /unblock does not resolve it" \
    "$(unblocked_of "[$BLOCKER,$(other_comment 2026-08-10T11:00:00Z '/unblock')]")" "false"
check "5. a PO /unblock before the blocker does not resolve it" \
    "$(unblocked_of "[$(po_comment 2026-08-10T09:00:00Z '/unblock'),$BLOCKER]")" "false"
check "6. a blocker with nothing after it stays open" \
    "$(unblocked_of "[$BLOCKER]")" "false"
check "7. blocker and /unblock on two pages are read together" \
    "$(unblocked_of "[$BLOCKER]
[$(po_comment 2026-08-10T11:00:00Z '/unblock')]")" "true"

echo ""
echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
[[ $FAIL -eq 0 ]]
