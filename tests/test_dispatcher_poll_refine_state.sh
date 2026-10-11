#!/usr/bin/env bash
# tests/test_dispatcher_poll_refine_state.sh
#
# Regression test for issue #257: ready_for_refinement Slack notifications
# were re-sent on every scheduled poll because de-duplication state lived in
# the runner's TMPDIR, which every fresh GitHub-hosted runner starts without.
#
# Each "run" below executes the real scripts/dispatcher-poll.sh (and the real
# slack-notify.sh) against a mocked `curl`, with:
#   - a brand-new TMPDIR (a fresh ephemeral runner), and
#   - DISPATCHER_STATE_DIR restored from the previous run's saved snapshot
#     (what the workflow's actions/cache restore/save steps do).
# Against the pre-fix TMPDIR-only state, scenario 1 sends two notifications
# and fails; with persisted, reconciled state it sends exactly one.
#
# Dry-run contract asserted here: --dry-run reads persisted state but never
# writes it and never calls Slack.
#
# Usage: bash tests/test_dispatcher_poll_refine_state.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
POLL="$REPO_ROOT/scripts/dispatcher-poll.sh"

PASS=0
FAIL=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }

WEBHOOK="https://hooks.slack.test/services/T000/B000/XXXX"

# ─── Mock curl ────────────────────────────────────────────────────────────────
#
#   Slack webhook POST       -> logs "<issue-number>" for ready_for_refinement
#                               payloads to MOCK_SLACK_LOG; exits 22 (curl -f
#                               HTTP failure) when MOCK_SLACK_FAIL is "1".
#   projectsV2(first:10)     -> one linked project.
#   items(first:100 ...)     -> one page; every number in MOCK_REFINE (space
#                               separated) is "Ready for refinement".
#   anything else            -> {"data":{}} (flow-review.sh tolerates this and
#                               never reports Red, so no red_flow_health call).

FAKE_BIN="$GLOBAL_TMP/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/curl" << 'CURLMOCK'
#!/usr/bin/env bash
ARGS="$*"
if [[ "$ARGS" == *"hooks.slack.test"* ]]; then
    if [[ "$ARGS" == *"Ready for Refinement"* ]]; then
        num=$(printf '%s' "$ARGS" | grep -oE 'issues/[0-9]+' | head -1)
        echo "${num#issues/}" >> "$MOCK_SLACK_LOG"
    fi
    [[ "${MOCK_SLACK_FAIL:-0}" == "1" ]] && exit 22
    echo "ok"
    exit 0
fi
if [[ "$ARGS" == *"projectsV2(first:10)"* ]]; then
    echo '{"data":{"repository":{"projectsV2":{"nodes":[{"number":1,"title":"Board"}]}}}}'
elif [[ "$ARGS" == *"items(first:100"* ]]; then
    nodes='[]'
    for n in ${MOCK_REFINE:-}; do
        nodes=$(jq -c --argjson n "$n" '. + [{"content":{"number":$n,"title":("Story " + ($n|tostring))},
            "fieldValues":{"nodes":[{"name":"Ready for refinement","field":{"name":"Status"}}]}}]' <<<"$nodes")
    done
    jq -nc --argjson nodes "$nodes" \
        '{data:{repository:{projectsV2:{nodes:[{items:{pageInfo:{hasNextPage:false,endCursor:null},nodes:$nodes}}]}}}}'
else
    echo '{"data":{}}'
fi
exit 0
CURLMOCK
chmod +x "$FAKE_BIN/curl"

# ─── Harness ──────────────────────────────────────────────────────────────────

pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1"; FAIL=$(( FAIL + 1 )); }

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        pass "$label"
    else
        fail "$label (expected '$expected', got '$actual')"
    fi
}

# Scenario-level state; reset by new_scenario.
SCEN=""
CACHE=""
SLACK_LOG=""
RUN_N=0
LAST_STDOUT=""
LAST_STDERR=""
LAST_RC=0

new_scenario() {
    SCEN="$GLOBAL_TMP/$1"
    CACHE="$SCEN/cache"          # simulated actions/cache snapshot
    SLACK_LOG="$SCEN/slack.log"
    RUN_N=0
    mkdir -p "$SCEN"
    : > "$SLACK_LOG"
}

# run_poll <repo> "<refine numbers>" [slack_fail] [extra args...]
# Simulates one workflow run on a fresh runner: restore snapshot -> poll -> save.
run_poll() {
    local repo="$1" refine="$2" slack_fail="${3:-0}"
    shift 3 2>/dev/null || shift $#
    RUN_N=$(( RUN_N + 1 ))
    local runner="$SCEN/runner$RUN_N"
    mkdir -p "$runner/tmp" "$runner/state"
    [[ -d "$CACHE" ]] && cp -a "$CACHE/." "$runner/state/"

    LAST_RC=0
    PATH="$FAKE_BIN:$PATH" \
    TMPDIR="$runner/tmp" \
    DISPATCHER_STATE_DIR="$runner/state" \
    GH_TOKEN="fake-token" \
    SLACK_WEBHOOK_URL="$WEBHOOK" \
    MOCK_SLACK_LOG="$SLACK_LOG" \
    MOCK_SLACK_FAIL="$slack_fail" \
    MOCK_REFINE="$refine" \
        bash "$POLL" --repo "$repo" "$@" >"$runner/stdout" 2>"$runner/stderr" || LAST_RC=$?
    LAST_STDOUT=$(cat "$runner/stdout")
    LAST_STDERR=$(cat "$runner/stderr")

    rm -rf "$CACHE"
    mkdir -p "$CACHE"
    cp -a "$runner/state/." "$CACHE/"
}

slack_count() {  # slack_count [issue-number]
    if [[ $# -eq 0 ]]; then
        wc -l < "$SLACK_LOG" | tr -d ' '
    else
        grep -cxF "$1" "$SLACK_LOG" || true
    fi
}

assert_clean_json() {
    local label="$1"
    if jq -e 'type == "object" and (keys == ["ready_for_implementation","ready_for_refinement"])' \
            <<<"$LAST_STDOUT" >/dev/null 2>&1; then
        pass "$label: stdout is the documented JSON object only"
    else
        fail "$label: stdout is not the documented JSON object: $LAST_STDOUT"
    fi
}

# ─── Scenarios ────────────────────────────────────────────────────────────────

test_cross_run_single_notification() {
    new_scenario cross_run
    run_poll acme/example "301"
    assert_eq "cross-run: run 1 exits 0" 0 "$LAST_RC"
    assert_clean_json "cross-run run 1"
    run_poll acme/example "301"
    assert_eq "cross-run: run 2 exits 0" 0 "$LAST_RC"
    assert_clean_json "cross-run run 2"
    run_poll acme/example "301"
    assert_eq "cross-run: 3 fresh-runner polls of an unchanged issue send exactly one notification" 1 "$(slack_count 301)"
    if grep -qF "Notified: ready_for_refinement #301" "$SCEN/runner1/stderr" \
        && ! grep -qF "Notified:" "$SCEN/runner2/stderr"; then
        pass "cross-run: 'Notified' line only on the run that delivered"
    else
        fail "cross-run: unexpected Notified lines"
    fi
}

test_multiple_issues_tracked_independently() {
    new_scenario multi
    run_poll acme/example "301 302"
    run_poll acme/example "301 302 303"
    run_poll acme/example "301 302 303"
    assert_eq "multi: #301 notified once" 1 "$(slack_count 301)"
    assert_eq "multi: #302 notified once" 1 "$(slack_count 302)"
    assert_eq "multi: #303 (arrives in run 2) notified once" 1 "$(slack_count 303)"
    assert_eq "multi: total notifications" 3 "$(slack_count)"
}

test_exit_and_reentry() {
    new_scenario reentry
    run_poll acme/example "301 302"
    run_poll acme/example "302"              # #301 leaves Ready for refinement
    local state="$CACHE/acme/example/notified-refinement"
    if ! grep -qxF 301 "$state" && grep -qxF 302 "$state"; then
        pass "reentry: exited issue removed from state, remaining issue kept"
    else
        fail "reentry: state after exit is '$(tr '\n' ' ' < "$state")'"
    fi
    run_poll acme/example "301 302"          # #301 re-enters
    run_poll acme/example "301 302"          # still in the same new stay
    assert_eq "reentry: #301 notified once per continuous stay (2 stays)" 2 "$(slack_count 301)"
    assert_eq "reentry: #302 notified once (never left)" 1 "$(slack_count 302)"

    run_poll acme/example ""                 # nothing ready: state cleared
    if [[ -f "$state" && ! -s "$state" ]]; then
        pass "reentry: state saved as empty when no issue is ready"
    else
        fail "reentry: expected an empty saved state file"
    fi
}

test_slack_failure_is_retried() {
    new_scenario retry
    run_poll acme/example "301" 1            # delivery fails
    assert_eq "retry: failed delivery still exits 0" 0 "$LAST_RC"
    assert_clean_json "retry failed run"
    if grep -qF "slack-notify.sh failed for #301; will retry next cycle" <<<"$LAST_STDERR" \
        && ! grep -qF "Notified:" <<<"$LAST_STDERR"; then
        pass "retry: failure logs retry warning and no Notified line"
    else
        fail "retry: unexpected stderr: $LAST_STDERR"
    fi
    if ! grep -qxF 301 "$CACHE/acme/example/notified-refinement"; then
        pass "retry: failed delivery not recorded in state"
    else
        fail "retry: failed delivery was recorded as notified"
    fi
    run_poll acme/example "301" 0            # retried and delivered
    run_poll acme/example "301" 0            # silent afterwards
    assert_eq "retry: attempts = 1 failed + 1 successful, then silent" 2 "$(slack_count 301)"
    if grep -qF "Notified: ready_for_refinement #301" "$SCEN/runner2/stderr"; then
        pass "retry: next poll delivers"
    else
        fail "retry: next poll did not deliver"
    fi
}

test_missing_state_is_silent_and_safe() {
    new_scenario missing
    run_poll acme/example "301"
    assert_eq "missing state: exits 0" 0 "$LAST_RC"
    assert_clean_json "missing state"
    assert_eq "missing state: notifies" 1 "$(slack_count 301)"
    if ! grep -qi "warning" <<<"$LAST_STDERR"; then
        pass "missing state: no warning for a plain cache miss"
    else
        fail "missing state: unexpected warning: $LAST_STDERR"
    fi
}

test_malformed_state_fails_safe() {
    new_scenario malformed
    mkdir -p "$CACHE/acme/example"
    printf '301\n{not json}\n' > "$CACHE/acme/example/notified-refinement"
    run_poll acme/example "301"
    assert_eq "malformed state: exits 0" 0 "$LAST_RC"
    assert_clean_json "malformed state"
    if grep -qF "Warning: malformed refinement notification state" <<<"$LAST_STDERR"; then
        pass "malformed state: clear warning on stderr"
    else
        fail "malformed state: no warning: $LAST_STDERR"
    fi
    assert_eq "malformed state: falls back to empty state and notifies" 1 "$(slack_count 301)"
    assert_eq "malformed state: saved state repaired" "301" "$(cat "$CACHE/acme/example/notified-refinement")"
}

test_unwritable_state_fails_safe() {
    new_scenario unwritable
    # A regular file where the per-owner directory should be: mkdir -p fails.
    mkdir -p "$CACHE"
    : > "$CACHE/acme"
    run_poll acme/example "301"
    assert_eq "unwritable state: exits 0" 0 "$LAST_RC"
    assert_clean_json "unwritable state"
    if grep -qF "Warning: cannot write refinement notification state" <<<"$LAST_STDERR"; then
        pass "unwritable state: clear warning on stderr"
    else
        fail "unwritable state: no warning: $LAST_STDERR"
    fi
}

test_repository_isolation() {
    new_scenario isolation
    run_poll acme/example "301"
    run_poll acme/other "301"                # same number, other repo, shared cache
    run_poll acme-x/y "301"
    run_poll acme/x-y "301"                  # "acme-x/y" vs "acme/x-y" must not collide
    run_poll acme/example "301"
    assert_eq "isolation: #301 notified once per repository (4 repos)" 4 "$(slack_count 301)"
    if [[ -f "$CACHE/acme/example/notified-refinement" && -f "$CACHE/acme/other/notified-refinement" \
          && -f "$CACHE/acme-x/y/notified-refinement" && -f "$CACHE/acme/x-y/notified-refinement" ]]; then
        pass "isolation: state stored under distinct owner/repo paths"
    else
        fail "isolation: per-repository state files missing"
    fi
}

test_dry_run_reads_but_never_writes() {
    new_scenario dryrun
    run_poll acme/example "301" 0 --dry-run
    assert_eq "dry-run: exits 0" 0 "$LAST_RC"
    assert_clean_json "dry-run"
    assert_eq "dry-run: no Slack call" 0 "$(slack_count)"
    if grep -qF "[DRY RUN] Would notify: ready_for_refinement #301" <<<"$LAST_STDERR"; then
        pass "dry-run: logs the notification it would send"
    else
        fail "dry-run: missing Would notify line: $LAST_STDERR"
    fi
    if [[ ! -e "$CACHE/acme/example/notified-refinement" ]]; then
        pass "dry-run: no state written"
    else
        fail "dry-run: state file was written"
    fi

    run_poll acme/example "301"              # real run records #301
    local before
    before=$(cat "$CACHE/acme/example/notified-refinement")
    run_poll acme/example "301 302" 0 --dry-run
    if grep -qF "Would notify: ready_for_refinement #302" <<<"$LAST_STDERR" \
        && ! grep -qF "Would notify: ready_for_refinement #301" <<<"$LAST_STDERR"; then
        pass "dry-run: reads persisted state (skips already-notified #301)"
    else
        fail "dry-run: did not honour persisted state: $LAST_STDERR"
    fi
    assert_eq "dry-run: persisted state unchanged" "$before" "$(cat "$CACHE/acme/example/notified-refinement")"
    assert_eq "dry-run: only the real run called Slack" 1 "$(slack_count)"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

echo "Running dispatcher-poll refinement-state regression tests..."
echo ""

test_cross_run_single_notification
test_multiple_issues_tracked_independently
test_exit_and_reentry
test_slack_failure_is_retried
test_missing_state_is_silent_and_safe
test_malformed_state_fails_safe
test_unwritable_state_fails_safe
test_repository_isolation
test_dry_run_reads_but_never_writes

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
