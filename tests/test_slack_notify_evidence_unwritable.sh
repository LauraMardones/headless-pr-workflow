#!/usr/bin/env bash
# tests/test_slack_notify_evidence_unwritable.sh
#
# Payload tests for the evidence_unwritable event in scripts/slack-notify.sh
# (issue #372). No network: curl is replaced by a stub that records the body.
#
# Usage: bash tests/test_slack_notify_evidence_unwritable.sh
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
        bash "$NOTIFY_SCRIPT" evidence_unwritable "$1" >/dev/null 2>&1 || return 1
    cat "$CURL_BODY_FILE"
}

texts() { jq -r '.blocks[].text.text' <<< "$1"; }
within_limits() {
    jq -e '(.blocks | length) <= 50 and ([.blocks[].text.text | length] | max) <= 3000' <<< "$1" >/dev/null
}

URL="https://github.com/o/r/issues/7"
TURL="https://github.com/o/r/issues/5"
FULL=$(jq -n --arg u "$URL" --arg t "$TURL" '{story_title:"My story", story_number:"7", issue_url:$u,
    item:"merge-confirmation", ref:"pr5", target_number:"5", target_url:$t, minutes:21, has_pending_text:true}')

p=$(send "$FULL"); t=$(texts "$p")
check "names the story number, title and link" \
    bash -c 'grep -qF "#7 My story" <<< "$1" && grep -qF "<$2|View story>" <<< "$1"' _ "$t" "$URL"
check "names the missing item and where it belongs" \
    bash -c 'grep -qF "merge-confirmation" <<< "$1" && grep -qF "<$2|#5>" <<< "$1"' _ "$t" "$TURL"
check "names the time since it has been missing" bash -c 'grep -qF "21 min" <<< "$1"' _ "$t"
check "offers fixing the write cause and posting the pending text by hand" \
    bash -c 'grep -qF "fix the cause" <<< "$1" && grep -qF "post the text by hand" <<< "$1"' _ "$t"
check "within Block Kit limits" within_limits "$p"

p=$(send "$(jq '.has_pending_text=false' <<< "$FULL")"); t=$(texts "$p")
check "without a pending text: no advice to post it by hand" bash -c '! grep -qF "by hand" <<< "$1"' _ "$t"

p=$(send '{}') || p=""
check "empty context does not crash" test -n "$p"

BIG=$(jq -n --arg u "$URL" --arg big "$(printf 'x%.0s' $(seq 1 5000))" \
    '{story_title:$big, story_number:"7", issue_url:$u, item:$big, target_number:"5", minutes:1}')
p=$(send "$BIG")
check "oversize: within limits and the link survives" \
    bash -c 'grep -qF "<$2|View story>" <<< "$1"' _ "$(texts "$p")" "$URL"
check "oversize: every section <= 3000 chars" within_limits "$p"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
