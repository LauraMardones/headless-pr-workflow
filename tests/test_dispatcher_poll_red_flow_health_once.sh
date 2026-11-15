#!/usr/bin/env bash
# tests/test_dispatcher_poll_red_flow_health_once.sh
#
# Issue #596: dispatcher-poll.sh sends red_flow_health once when the board turns
# Red, not on every poll while it stays Red. Runs the real poll from a copy of
# scripts/ with a fake flow-review.sh and slack-notify.sh and the real
# `hpw dispatcher state` block on a temporary DISPATCHER_STATE_DIR.
#
# Usage: bash tests/test_dispatcher_poll_red_flow_health_once.sh
# Requires: bash 4+, jq, python 3

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export WIP_LIMIT_IMPLEMENTATION=2 READY_BUFFER_MIN=3 READY_BUFFER_MAX=5

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }
PYTHON=$(command -v python3 || command -v python || true)
[[ -n "$PYTHON" ]] || { echo "SKIP: python not available"; exit 0; }

PASS=0
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1"; FAIL=$(( FAIL + 1 )); }

# A copy of scripts/ whose flow-review.sh and slack-notify.sh are fakes.
SCRIPTS="$TMP/scripts"
cp -r "$REPO_ROOT/scripts" "$SCRIPTS"
cat > "$SCRIPTS/flow-review.sh" << 'FAKE'
#!/usr/bin/env bash
# FLOW_HEALTH and FLOW_WIP_VIOLATION come from the test; "" means no flow_health.
if [[ -n "${FLOW_HEALTH-}" ]]; then
    jq -nc --arg h "$FLOW_HEALTH" --argjson w "${FLOW_WIP_VIOLATION:-false}" \
        '{flow_health:$h, wip_count:3, blocked:[], wip_violation:$w}'
else
    echo '{}'
fi
FAKE
cat > "$SCRIPTS/slack-notify.sh" << 'FAKE'
#!/usr/bin/env bash
echo "$1" >> "$SLACK_LOG"
echo ok
[[ "${SLACK_FAIL:-0}" != 1 ]]
FAKE
chmod +x "$SCRIPTS"/*.sh

BIN="$TMP/bin"
mkdir -p "$BIN"
cat > "$BIN/curl" << 'FAKE'
#!/usr/bin/env bash
echo '[]'
FAKE
cat > "$BIN/hpw" << FAKE
#!/usr/bin/env bash
case "\${1:-} \${2:-}" in
    "dispatcher state"|"dispatcher in-flight")
        shift; PYTHONPATH="$REPO_ROOT/src" exec "$PYTHON" -m headless_pr_workflow.dispatcher "\$@" ;;
    "dispatcher scheduler")
        echo '{"ok":true,"block":"scheduler","operation":"next","result":"idle","action":null,"skipped":[]}'
        exit 1 ;;
esac
exit 1
FAKE
chmod +x "$BIN/curl" "$BIN/hpw"

STATE="$TMP/state"
STATE_FILE="$STATE/acme/example/red-flow-notified"
SLACK_LOG="$TMP/slack.log"

reset() { rm -rf "$STATE"; : > "$SLACK_LOG"; }
sends() { grep -c '^red_flow_health$' "$SLACK_LOG" || true; }

# poll <flow_health> [extra poll args...] — sets POLL_RC, POLL_OUT, POLL_ERR.
poll() {
    local health="$1"; shift
    POLL_RC=0
    POLL_OUT=$(
        PATH="$BIN:$PATH" GH_TOKEN=fake HPW_BIN="$BIN/hpw" DISPATCHER_STATE_DIR="$STATE" \
        SLACK_LOG="$SLACK_LOG" FLOW_HEALTH="$health" \
        bash "$SCRIPTS/dispatcher-poll.sh" --repo acme/example "$@" 2>"$TMP/err.log"
    ) || POLL_RC=$?
    POLL_ERR=$(cat "$TMP/err.log")
}

check_json() { jq -e 'type == "object"' <<< "$POLL_OUT" >/dev/null 2>&1; }

expect() { # expect <description> <condition result: 0 = ok>
    if [[ "$2" == 0 ]]; then pass "$1"; else fail "$1 (sends=$(sends) rc=$POLL_RC err=$POLL_ERR)"; fi
}

# AC1: Red x3 sends one message; a signal change inside the Red period sends none.
reset
poll Red; poll Red; FLOW_WIP_VIOLATION=true poll Red
expect "Red three times sends exactly one message, also when the signal changes" "$(( $(sends) == 1 ? 0 : 1 ))"

# AC2: Red, not Red, Red sends two messages.
reset
poll Red; poll Green; poll Red; poll Red
expect "Red, not Red, Red sends two messages" "$(( $(sends) == 2 ? 0 : 1 ))"

# An empty flow_health leaves the state unchanged: still one message.
reset
poll Red; poll ""; poll Red
expect "an empty flow_health is not 'no longer Red'" "$(( $(sends) == 1 ? 0 : 1 ))"

# AC3: a failed delivery is not recorded; the next poll tries again; poll still exits 0 with JSON.
reset
SLACK_FAIL=1 poll Red
rc_fail=$POLL_RC
check_json && json_ok=0 || json_ok=1
expect "a failed delivery leaves the poll exit code and JSON as before" "$(( rc_fail == 0 && json_ok == 0 ? 0 : 1 ))"
expect "a failed delivery is not recorded as sent" "$([[ ! -e "$STATE_FILE" ]] && echo 0 || echo 1)"
poll Red
expect "the next poll after a failed delivery sends again" "$(( $(sends) == 2 ? 0 : 1 ))"
poll Red
expect "and then records it: no third message" "$(( $(sends) == 2 ? 0 : 1 ))"

# AC4: missing, malformed, unreadable state and a failed write never stop the poll.
reset
mkdir -p "$(dirname "$STATE_FILE")"; echo garbage > "$STATE_FILE"
poll Red
expect "malformed state still sends once, exit 0, JSON on stdout" "$(( $(sends) == 1 && POLL_RC == 0 ? 0 : 1 ))"
check_json && json_ok=0 || json_ok=1
expect "malformed state keeps stdout JSON" "$json_ok"
poll Red
expect "the malformed file was replaced: no second message" "$(( $(sends) == 1 ? 0 : 1 ))"

reset
mkdir -p "$STATE_FILE"   # a directory: unreadable, and the write moves it aside
poll Red
check_json && json_ok=0 || json_ok=1
expect "unreadable state still sends once, exit 0, JSON on stdout" "$(( $(sends) == 1 && POLL_RC == 0 && json_ok == 0 ? 0 : 1 ))"

reset
mkdir -p "$STATE/acme"; : > "$STATE/acme/example"   # parent is a file: the write fails
poll Red
check_json && json_ok=0 || json_ok=1
expect "a failed state write sends once, exit 0, JSON on stdout" "$(( $(sends) == 1 && POLL_RC == 0 && json_ok == 0 ? 0 : 1 ))"
expect "a failed state write logs a warning" "$([[ "$POLL_ERR" == *"[POLL] Warning: cannot write red-flow-notified"* ]] && echo 0 || echo 1)"
poll Red
expect "after a failed state write the next poll sends again" "$(( $(sends) == 2 ? 0 : 1 ))"

# AC5: --dry-run sends nothing and writes no state; it logs only when due.
reset
poll Red --dry-run
expect "--dry-run sends nothing" "$(( $(sends) == 0 ? 0 : 1 ))"
expect "--dry-run writes no state file" "$([[ ! -e "$STATE_FILE" ]] && echo 0 || echo 1)"
expect "--dry-run logs the notification when due" "$([[ "$POLL_ERR" == *"[DRY RUN] Would notify: red_flow_health (Empty ready queue with active WIP)"* ]] && echo 0 || echo 1)"
poll Red
poll Red --dry-run
expect "--dry-run logs nothing once the message was sent" "$([[ "$POLL_ERR" != *"Would notify: red_flow_health"* ]] && echo 0 || echo 1)"
poll Green --dry-run
expect "--dry-run does not clear the state" "$([[ "$(cat "$STATE_FILE")" == sent ]] && echo 0 || echo 1)"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
