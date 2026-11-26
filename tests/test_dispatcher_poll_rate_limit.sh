#!/usr/bin/env bash
# tests/test_dispatcher_poll_rate_limit.sh
#
# Issue #660: when the poll's read of the open pull requests fails because the
# GitHub rate limit is used up, it prints one `[RATE LIMIT]` line with the reset
# time, exits 1 as before and starts nothing. A failure that is not a rate limit
# (HTTP 502) prints no such line. A used-up limit whose /rate_limit read fails
# says `resets unknown`.
#
# Usage: bash tests/test_dispatcher_poll_rate_limit.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export WIP_LIMIT_IMPLEMENTATION=2 READY_BUFFER_MIN=3 READY_BUFFER_MAX=5

PASS=0
FAIL=0
GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT
command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }

FAKE_BIN=$(mktemp -d "$GLOBAL_TMP/bin.XXXXXX")
CALL_LOG="$GLOBAL_TMP/curl-calls.log"

# MODE=limit      open PRs: 403 (curl -f fails); /rate_limit: remaining 0, reset 1798200000
# MODE=limit-down open PRs: 403; /rate_limit fails
# MODE=bad-gateway open PRs: 502; /rate_limit: remaining 5000
cat > "$FAKE_BIN/curl" << 'CURLMOCK'
#!/usr/bin/env bash
url="${@: -1}"
echo "$url" >> "$MOCK_CALL_LOG"
status_probe=false
[[ " $* " == *" -w "* ]] && status_probe=true
case "$url" in
    */rate_limit)
        [[ "$MODE" == "limit-down" ]] && exit 22
        if [[ "$MODE" == "limit" ]]; then echo '{"resources":{"core":{"limit":5000,"remaining":0,"reset":1798200000}}}'
        else echo '{"resources":{"core":{"limit":5000,"remaining":5000,"reset":1798200000}}}'; fi ;;
    *"/pulls?state=open"*)
        if $status_probe; then [[ "$MODE" == "bad-gateway" ]] && printf 502 || printf 403; exit 0; fi
        exit 22 ;;
    *"/issues?state=open"*) echo '[]' ;;
    *) echo '[]' ;;
esac
exit 0
CURLMOCK
chmod +x "$FAKE_BIN/curl"

cat > "$FAKE_BIN/hpw" << 'HPWMOCK'
#!/usr/bin/env bash
echo "hpw called: $*" >> "$MOCK_CALL_LOG"
exit 1
HPWMOCK
chmod +x "$FAKE_BIN/hpw"

run_poll() {  # <mode> — sets OUT, ERR, RC
    : > "$CALL_LOG"
    RC=0
    OUT=$(PATH="$FAKE_BIN:$PATH" MODE="$1" MOCK_CALL_LOG="$CALL_LOG" GH_TOKEN="fake-token" \
        HPW_BIN="$FAKE_BIN/hpw" bash "$REPO_ROOT/scripts/dispatcher-poll.sh" --repo "acme/example" 2>"$GLOBAL_TMP/err.log") || RC=$?
    ERR=$(cat "$GLOBAL_TMP/err.log")
}

check() {  # <name> <condition-exit-code>
    if [[ "$2" == "0" ]]; then echo "PASS: $1"; PASS=$(( PASS + 1 )); else echo "FAIL: $1"; echo "$ERR"; FAIL=$(( FAIL + 1 )); fi
}

run_poll limit
check "rate limit: one [RATE LIMIT] line with the reset time" \
    "$([[ "$(grep -c '^\[RATE LIMIT\]' <<< "$ERR")" == 1 ]] && grep -qF '[RATE LIMIT] read open pull requests: GitHub API rate limit used up; resets 2026-12-25T12:00:00Z' <<< "$ERR"; echo $?)"
check "rate limit: exit 1, nothing chosen or started" \
    "$([[ "$RC" == 1 && -z "$(jq -r '.next_issue // empty' <<< "$OUT" 2>/dev/null || true)" ]] && ! grep '^hpw called' "$CALL_LOG" | grep -qv 'marker whoami'; echo $?)"
check "rate limit: the existing unreadable lines stay" \
    "$(grep -qF 'Error: could not read the work in flight.' <<< "$ERR" && grep -qF '[IN-FLIGHT] unreadable:' <<< "$ERR"; echo $?)"

run_poll limit-down
check "rate limit with /rate_limit down: resets unknown" \
    "$(grep -qF 'GitHub API rate limit used up; resets unknown' <<< "$ERR"; echo $?)"
check "rate limit with /rate_limit down: exit 1" "$([[ "$RC" == 1 ]]; echo $?)"

run_poll bad-gateway
check "HTTP 502: no [RATE LIMIT] line" "$(grep -qF '[RATE LIMIT]' <<< "$ERR" && echo 1 || echo 0)"
check "HTTP 502: exit 1" "$([[ "$RC" == 1 ]]; echo $?)"

echo "Results: $PASS pass, $FAIL fail"
[[ "$FAIL" -eq 0 ]]
