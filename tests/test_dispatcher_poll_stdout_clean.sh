#!/usr/bin/env bash
# tests/test_dispatcher_poll_stdout_clean.sh
#
# Regression test for issue #220: dispatcher-poll.sh stdout corruption.
#
# slack-notify.sh prints the webhook's answer ("ok") on stdout. Every Slack
# call in dispatcher-poll.sh must send that stdout to /dev/null, so the poll's
# stdout stays the JSON object the workflow reads. Since issue #474 the only
# Slack call left in the poll is red_flow_health: the ready_for_refinement
# notification is retired.
#
# Usage: bash tests/test_dispatcher_poll_stdout_clean.sh
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

pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1"; FAIL=$(( FAIL + 1 )); }

# ─── Test 1: the redirect keeps slack-notify's "ok" out of captured stdout ─────

test_slack_notify_stdout_suppressed() {
    local tmpdir out
    tmpdir=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    printf '#!/usr/bin/env bash\necho "ok"\nexit 0\n' > "$tmpdir/slack-notify.sh"
    chmod +x "$tmpdir/slack-notify.sh"
    out=$(bash "$tmpdir/slack-notify.sh" red_flow_health '{}' >/dev/null; echo '{"next_issue":null}')
    if jq -e 'type == "object"' <<< "$out" >/dev/null 2>&1; then
        pass "the redirected call leaves stdout valid JSON"
    else
        fail "stdout is not JSON: $out"
    fi
}

# ─── Test 2: every Slack call in the poll redirects its stdout ────────────────

test_every_poll_slack_call_redirects_stdout() {
    local calls bad
    calls=$(grep -n 'slack-notify.sh' "$POLL" | grep -v '^[0-9]*:[[:space:]]*#' || true)
    if [[ -z "$calls" ]]; then
        fail "no slack-notify.sh call found in dispatcher-poll.sh"
        return
    fi
    bad=$(grep -v '>/dev/null' <<< "$calls" || true)
    if [[ -z "$bad" ]]; then
        pass "every slack-notify.sh call in dispatcher-poll.sh has >/dev/null"
    else
        fail "slack-notify.sh call without >/dev/null: $bad"
    fi
    if grep -q 'ready_for_refinement' "$POLL"; then
        fail "dispatcher-poll.sh still sends the retired ready_for_refinement notification"
    else
        pass "dispatcher-poll.sh sends no ready_for_refinement notification (issue #474)"
    fi
}

echo "Running dispatcher-poll stdout regression tests..."
echo ""

test_slack_notify_stdout_suppressed
test_every_poll_slack_call_redirects_stdout

echo ""
echo "Results: $PASS pass, $FAIL fail"

[[ "$FAIL" -eq 0 ]]
