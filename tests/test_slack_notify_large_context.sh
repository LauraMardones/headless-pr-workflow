#!/usr/bin/env bash
# tests/test_slack_notify_large_context.sh
#
# Issue #426: decision_blocker, po_blocker, evidence_unwritable and
# dispatch_trigger_stopped give the context to jq on standard input, so a
# context above the 32 KB Windows command-line limit still works. No network:
# curl is a stub that records the body.
#
# Usage: bash tests/test_slack_notify_large_context.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTIFY_SCRIPT="$(cd "$SCRIPT_DIR/.." && pwd)/scripts/slack-notify.sh"

PASS=0
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

check() {
    local label="$1"; shift
    if "$@"; then echo "PASS: $label"; PASS=$(( PASS + 1 ))
    else echo "FAIL: $label"; FAIL=$(( FAIL + 1 )); fi
}

mkdir -p "$TMP/bin"
cat > "$TMP/bin/curl" << 'STUB'
#!/usr/bin/env bash
echo called >> "$CURL_CALLS_FILE"
while [[ $# -gt 0 ]]; do
    [[ "$1" == "--data" ]] && printf '%s' "$2" > "$CURL_BODY_FILE"
    shift
done
exit 0
STUB
chmod +x "$TMP/bin/curl"
export CURL_BODY_FILE="$TMP/body.json" CURL_CALLS_FILE="$TMP/calls"

# run_notify <event> <context-file> → exit code of slack-notify.sh
run_notify() {
    rm -f "$CURL_BODY_FILE" "$CURL_CALLS_FILE"
    PATH="$TMP/bin:$PATH" SLACK_WEBHOOK_URL="https://hooks.example.com/x" \
        bash "$NOTIFY_SCRIPT" "$1" "$(cat "$2")" >/dev/null 2>&1
}

valid_payload() {
    jq -e '(.blocks | length) >= 1 and ([.blocks[].text.text | length] | max) <= 3000 and ([.blocks[].text.text] | join(" ") | contains("github.com") or contains("example.com"))' "$CURL_BODY_FILE" >/dev/null
}

# The 40 KB padding key is ignored by the script but makes the context large.
# jq builds it, so no command-line argument grows past the Windows limit.
LONG=$(head -c 5000 /dev/zero | tr '\0' 'x')
jq -n --argjson n 40000 --arg l "$LONG" '{
    story_title:("Story " + $l), issue_url:"https://github.com/o/r/issues/7",
    blocker_type:"decision", unblocked_when:$l, question:$l, options:$l,
    recommendation:$l, cause:$l, state:$l, story_number:"7", item:$l,
    target_number:"8", target_url:"https://github.com/o/r/pull/8",
    minutes:"90", has_pending_text:$l, age:"3h", last_created_at:"now",
    check_command:$l, docs_url:"https://example.com", pad:([range($n) | "p"] | join(""))}' > "$TMP/big.json"
check "context is larger than 32 KB" test "$(wc -c < "$TMP/big.json")" -gt 32768

for ev in decision_blocker po_blocker evidence_unwritable dispatch_trigger_stopped; do
    check "$ev: >32 KB context exits 0" run_notify "$ev" "$TMP/big.json"
    check "$ev: payload is valid and within the section limit" valid_payload
done

echo '{not json' > "$TMP/bad.json"
rejects_invalid() {
    if run_notify "$1" "$TMP/bad.json"; then return 1; fi
    [[ ! -e "$CURL_CALLS_FILE" ]]
}
for ev in decision_blocker po_blocker evidence_unwritable dispatch_trigger_stopped; do
    check "$ev: invalid JSON exits non-zero and sends nothing" rejects_invalid "$ev"
done

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
