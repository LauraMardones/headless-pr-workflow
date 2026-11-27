#!/usr/bin/env bash
# tests/test_dispatcher_poll_no_feature_note.sh
#
# Issue #616: dispatcher-poll.sh hands the scheduler's output to
# `hpw dispatcher no-feature-note`, writes no Slack event for it, and keeps the
# scheduler's result when the rule fails. The workflow runs the poll only behind
# the DISPATCHER_ENABLED guard. Runs the real poll from a copy of scripts/ with
# fake flow-review.sh, slack-notify.sh and hpw.
#
# Usage: bash tests/test_dispatcher_poll_no_feature_note.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export WIP_LIMIT_IMPLEMENTATION=2 READY_BUFFER_MIN=3 READY_BUFFER_MAX=5

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }

PASS=0
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1"; FAIL=$(( FAIL + 1 )); }
expect() { if [[ "$2" == 0 ]]; then pass "$1"; else fail "$1 (rc=$POLL_RC err=$POLL_ERR)"; fi; }

SCRIPTS="$TMP/scripts"
cp -r "$REPO_ROOT/scripts" "$SCRIPTS"
cat > "$SCRIPTS/flow-review.sh" << 'FAKE'
#!/usr/bin/env bash
echo '{}'
FAKE
cat > "$SCRIPTS/slack-notify.sh" << 'FAKE'
#!/usr/bin/env bash
echo "$1" >> "$SLACK_LOG"
echo ok
FAKE
chmod +x "$SCRIPTS"/*.sh

BIN="$TMP/bin"
mkdir -p "$BIN"
printf '#!/usr/bin/env bash\necho "[]"\n' > "$BIN/curl"
# The fake hpw: the scheduler skips #7 with no-feature and starts #9; the note rule logs its arguments and stdin.
PYTHON=$(command -v python3 || command -v python || true)
[[ -n "$PYTHON" ]] || { echo "SKIP: python not available"; exit 0; }
cat > "$BIN/hpw" << FAKE
#!/usr/bin/env bash
case "\${1:-} \${2:-}" in
    "dispatcher state"|"dispatcher in-flight")
        shift; PYTHONPATH="$REPO_ROOT/src" exec "$PYTHON" -m headless_pr_workflow.dispatcher "\$@" ;;
esac
FAKE
cat >> "$BIN/hpw" << 'FAKE'
case "${1:-} ${2:-}" in
    "dispatcher scheduler")
        echo '{"ok":true,"block":"scheduler","operation":"next","result":"implement","action":{"kind":"implement","issue":9,"step":"s","reason":"r"},"skipped":[{"issue":7,"reason":"no-feature"}]}'
        exit 0 ;;
    "dispatcher no-feature-note")
        echo "$*" >> "$NOTE_LOG"
        cat >> "$NOTE_LOG"
        echo "[NO-FEATURE NOTE] #7: written"
        exit "${NOTE_RC:-0}" ;;
esac
exit 1
FAKE
chmod +x "$BIN/curl" "$BIN/hpw"

NOTE_LOG="$TMP/note.log"
SLACK_LOG="$TMP/slack.log"

poll() { # poll [extra poll args...] — sets POLL_RC, POLL_OUT, POLL_ERR
    : > "$NOTE_LOG"; : > "$SLACK_LOG"
    POLL_RC=0
    POLL_OUT=$(
        PATH="$BIN:$PATH" GH_TOKEN=fake HPW_BIN="$BIN/hpw" DISPATCHER_STATE_DIR="$TMP/state" \
        NOTE_LOG="$NOTE_LOG" SLACK_LOG="$SLACK_LOG" NOTE_RC="${NOTE_RC:-0}" \
        bash "$SCRIPTS/dispatcher-poll.sh" --repo acme/example "$@" 2>"$TMP/err.log"
    ) || POLL_RC=$?
    POLL_ERR=$(cat "$TMP/err.log")
}

# The rule is called once with the repo and the scheduler's output (AC 1, 2).
poll
expect "the poll exits 0" "$(( POLL_RC == 0 ? 0 : 1 ))"
expect "the rule is called once with the repo" \
    "$([[ "$(grep -c '^dispatcher no-feature-note --repo acme/example' "$NOTE_LOG")" == 1 ]] && echo 0 || echo 1)"
expect "the rule gets the scheduler's skipped list on stdin" \
    "$(grep -q '"reason":"no-feature"' "$NOTE_LOG" && echo 0 || echo 1)"
expect "the rule's output stays off stdout (the poll's JSON is clean)" \
    "$(jq -e '.next_issue == 9' <<< "$POLL_OUT" >/dev/null 2>&1 && echo 0 || echo 1)"

# AC 5: no Slack event for the note.
expect "the note writes no Slack event" "$([[ ! -s "$SLACK_LOG" ]] && echo 0 || echo 1)"

# --dry-run reaches the rule.
poll --dry-run
expect "--dry-run is passed to the rule" "$(grep -q -- '--dry-run' "$NOTE_LOG" && echo 0 || echo 1)"

# AC 6: a non-zero rule exit changes neither the scheduler's result nor the poll's exit code.
for rc in 1 3; do
    NOTE_RC=$rc poll
    expect "a rule exit $rc keeps the poll exit 0 and the scheduler's choice" \
        "$([[ $POLL_RC == 0 ]] && jq -e '.next_issue == 9 and .scheduler_result == "implement"' <<< "$POLL_OUT" >/dev/null 2>&1 && echo 0 || echo 1)"
    expect "a rule exit $rc is logged for a later run" \
        "$([[ "$POLL_ERR" == *"no-feature note exited $rc"* ]] && echo 0 || echo 1)"
done

# AC 4: the workflow runs the poll only behind the DISPATCHER_ENABLED guard.
poll_if=$(awk '/- name: Run dispatcher poll/{f=1} f&&/if:/{print; exit}' "$REPO_ROOT/.github/workflows/dispatcher.yml")
expect "the workflow's poll step runs only when the guard is enabled" \
    "$([[ "$poll_if" == *"steps.guard.outputs.enabled == 'true'"* ]] && echo 0 || echo 1)"

echo "Passed: $PASS Failed: $FAIL"
[[ $FAIL -eq 0 ]]
