#!/usr/bin/env bash
# tests/test_slack_notify_decision_blocker.sh
#
# Payload tests for the decision_blocker event in scripts/slack-notify.sh
# (issue #296). No network: curl is replaced by a stub that records the body.
#
# Usage: bash tests/test_slack_notify_decision_blocker.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NOTIFY_SCRIPT="$REPO_ROOT/scripts/slack-notify.sh"

PASS=0
FAIL=0
GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

check() {
    local label="$1"; shift
    if "$@"; then
        echo "PASS: $label"; PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"; FAIL=$(( FAIL + 1 ))
    fi
}

mkdir -p "$GLOBAL_TMP/bin"
cat > "$GLOBAL_TMP/bin/curl" << 'STUB'
#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
    [[ "$1" == "--data" ]] && printf '%s' "$2" > "$CURL_BODY_FILE"
    shift
done
exit 0
STUB
chmod +x "$GLOBAL_TMP/bin/curl"
export CURL_BODY_FILE="$GLOBAL_TMP/body.json"

# send <context-json> → payload on stdout; fails when the script exits non-zero
send() {
    rm -f "$CURL_BODY_FILE"
    PATH="$GLOBAL_TMP/bin:$PATH" SLACK_WEBHOOK_URL="https://hooks.example.com/x" \
        bash "$NOTIFY_SCRIPT" decision_blocker "$1" >/dev/null 2>&1 || return 1
    cat "$CURL_BODY_FILE"
}

texts() { jq -r '.blocks[].text.text' <<< "$1"; }
within_limits() {
    jq -e '(.blocks | length) <= 50 and ([.blocks[].text.text | length] | max) <= 3000' <<< "$1" >/dev/null
}

URL="https://github.com/o/r/issues/7"
FULL=$(jq -n --arg u "$URL" '{story_title:"My story", issue_url:$u, blocker_type:"decision",
    unblocked_when:"PO decides", question:"Ship A or B?",
    options:"A) ship A — fast but risky\nB) ship B — slow but safe", recommendation:"B because safe"}')

p=$(send "$FULL") || { echo "FAIL: full context send failed"; FAIL=$(( FAIL + 1 )); p=""; }
t=$(texts "$p")
check "full: renders story, question, options, recommendation, unblocked-when" \
    bash -c 'grep -qF "My story" <<< "$1" && grep -qF "Ship A or B?" <<< "$1" && grep -qF "A) ship A — fast but risky" <<< "$1" && grep -qF "B) ship B — slow but safe" <<< "$1" && grep -qF "B because safe" <<< "$1" && grep -qF "PO decides" <<< "$1"' _ "$t"
check "full: links the issue and names /unblock" \
    bash -c 'grep -qF "<$2|View issue>" <<< "$1" && grep -qF "/unblock" <<< "$1"' _ "$t" "$URL"
check "full: nothing reported missing" bash -c '! grep -qF "missing" <<< "$1"' _ "$t"

LONG=$(head -c 5000 /dev/zero | tr '\0' 'x')
BIG=$(jq -n --arg u "$URL" --arg l "$LONG" '{story_title:$l, issue_url:$u, blocker_type:"decision",
    unblocked_when:$l, question:$l, recommendation:$l,
    options:([range(0;20) | ["A","B","C","D"][. % 4] + ") name" + (.|tostring) + " — " + $l] | join("\n"))}')
p=$(send "$BIG") || { echo "FAIL: oversize context send failed"; FAIL=$(( FAIL + 1 )); p=""; }
t=$(texts "$p")
check "oversize: every section <= 3000 chars and <= 50 blocks" within_limits "$p"
check "oversize: issue link and /unblock instruction survive" \
    bash -c 'grep -qF "<$2|View issue>" <<< "$1" && grep -qF "/unblock" <<< "$1"' _ "$t" "$URL"
check "oversize: every option keeps its letter and name, cuts are marked" \
    bash -c 'for i in $(seq 0 19); do grep -qF "name$i — " <<< "$1" || exit 1; done; grep -qF "…" <<< "$1"' _ "$t"

# Many short options: no option is cut when everything fits.
check "short options are not cut" bash -c '! grep -qF "…" <<< "$1"' _ "$(texts "$(send "$FULL")")"

p=$(send '{"story_title":"Legacy","issue_url":"'"$URL"'","blocker_type":"decision","unblocked_when":"x"}') \
    || { echo "FAIL: missing keys crashed"; FAIL=$(( FAIL + 1 )); p=""; }
if [[ -n "$p" ]]; then
    t=$(texts "$p")
    check "missing keys: valid payload naming what is missing" \
        bash -c 'grep -qF "Declaration is missing: Question, Options, Recommendation" <<< "$1" && grep -qF "Legacy" <<< "$1"' _ "$t"
    check "missing keys: within limits" within_limits "$p"
fi

p=$(send '{"story_title":"S","question":"Q?","options":"","recommendation":"A: ok"}') || p=""
check "partial: names only the missing field" \
    bash -c 'grep -qF "Declaration is missing: Options" <<< "$1"' _ "$(texts "$p")"

p=$(send '{}') || p=""
check "empty context does not crash" test -n "$p"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
