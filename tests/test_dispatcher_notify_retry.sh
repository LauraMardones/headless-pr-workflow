#!/usr/bin/env bash
# tests/test_dispatcher_notify_retry.sh
#
# Tests for scripts/dispatcher-notify-retry.sh (issue #295): retry of the
# Slack notifications that need PO action, with a fake slack-notify.sh that
# fails then succeeds and a fake gh.
#
# Usage: bash tests/test_dispatcher_notify_retry.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PASS=0
FAIL=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }

check() {  # check <label> <condition-result 0|1>
    if [[ "$2" == 0 ]]; then echo "PASS: $1"; PASS=$(( PASS + 1 ))
    else echo "FAIL: $1"; FAIL=$(( FAIL + 1 )); fi
}

# Fresh sandbox: a copy of the script next to a fake slack-notify.sh, a fake gh
# on PATH, and a state directory shared by every run of one test.
setup() {
    T=$(mktemp -d "$GLOBAL_TMP/XXXXXX")
    mkdir -p "$T/scripts" "$T/bin" "$T/gh" "$T/state"
    cp "$REPO_ROOT/scripts/dispatcher-notify-retry.sh" "$T/scripts/"
    cat > "$T/scripts/slack-notify.sh" <<FAKE
#!/usr/bin/env bash
echo "\$1 \$2" >> "$T/slack.log"
[[ -e "$T/slack-fail" ]] && exit 1
exit 0
FAKE
    cat > "$T/bin/gh" <<FAKE
#!/usr/bin/env bash
# fake gh: only "gh api [--paginate] <path> [-X POST -f body=...] [--jq ...]"
shift
path=""; post=false; jq=""
while [[ \$# -gt 0 ]]; do
    case "\$1" in
        --paginate) shift ;;
        -X) post=true; shift 2 ;;
        -f) echo "\$path :: \$2" >> "$T/gh/posts.log"; shift 2 ;;
        --jq) jq="\$2"; shift 2 ;;
        *) path="\$1"; shift ;;
    esac
done
n=\$(echo "\$path" | sed -E 's#^repos/[^/]+/[^/]+/issues/([0-9]+).*#\1#')
if [[ "\$path" == user ]]; then
    echo "dispatcher-bot"
elif [[ "\$path" == */comments* ]]; then
    \$post && exit 0
    cat "$T/gh/comments_\$n.json" 2>/dev/null || echo '[]'
else
    cat "$T/gh/state_\$n" 2>/dev/null || echo open
fi
FAKE
    chmod +x "$T/scripts/"*.sh "$T/bin/gh"
    export PATH="$T/bin:$PATH" DISPATCHER_STATE_DIR="$T/state"
    export SLACK_WEBHOOK_URL=x
}

retry() { bash "$T/scripts/dispatcher-notify-retry.sh" "$@" 2>"$T/stderr"; }
reg_decision() {  # reg_decision <issue> <since>
    retry register --repo o/r --event decision_blocker --issue "$1" --since "$2" --context '{"story_title":"S"}'
}
slack_count() { [[ -e "$T/slack.log" ]] && wc -l < "$T/slack.log" || echo 0; }
STATE() { echo "$T/state/o/r/actionable-events"; }

test_fail_then_succeed_delivers_once() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    touch "$T/slack-fail"
    retry run --repo o/r; rc1=$?
    rm "$T/slack-fail"
    retry run --repo o/r; rc2=$?
    retry run --repo o/r
    check "first failing cycle exits 0 (within bound)" $(( rc1 ))
    check "second cycle succeeds" $(( rc2 ))
    check "attempts: 1 failed + 1 delivered, none on third run" $(( $(slack_count) == 2 ? 0 : 1 ))
    check "recorded as delivered once" $(( $(jq -s '[.[]|select(.delivered)]|length' "$(STATE)") == 1 ? 0 : 1 ))
}

test_delivered_event_not_resent_when_registered_again() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    retry run --repo o/r
    reg_decision 7 2026-10-14T10:00:00Z
    retry run --repo o/r
    check "re-registering a delivered unresolved blocker sends nothing" $(( $(slack_count) == 1 ? 0 : 1 ))
}

test_closure_events_retried() {
    setup
    touch "$T/slack-fail"
    retry register --repo o/r --event feature_closure_confirmation --issue 10 --context '{"feature_title":"F"}'
    retry register --repo o/r --event epic_closure_approval --issue 20 --context '{"epic_title":"E"}'
    retry run --repo o/r
    rm "$T/slack-fail"; : > "$T/slack.log"
    retry run --repo o/r
    retry run --repo o/r
    check "both closure events sent once after recovery" $(( $(slack_count) == 2 ? 0 : 1 ))
    check "both recorded delivered" $(( $(jq -s '[.[]|select(.delivered)]|length' "$(STATE)") == 2 ? 0 : 1 ))
    check "state is per event" $(( $(jq -s '[.[].key]|unique|length' "$(STATE)") == 2 ? 0 : 1 ))
}

test_unblock_stops_retry_even_if_undelivered() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    echo '[{"user":{"login":"LauraMardones"},"created_at":"2026-10-14T11:00:00Z","body":"ok\n/unblock\n"}]' > "$T/gh/comments_7.json"
    touch "$T/slack-fail"
    retry run --repo o/r; rc=$?
    check "resolved by /unblock: exit 0" $(( rc ))
    check "resolved by /unblock: no attempt" $(( $(slack_count) == 0 ? 0 : 1 ))
    check "resolved by /unblock: record dropped" $(( $(wc -l < "$(STATE)") == 0 ? 0 : 1 ))
}

test_unblock_by_someone_else_or_before_blocker_does_not_resolve() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    echo '[{"user":{"login":"other"},"created_at":"2026-10-14T11:00:00Z","body":"/unblock"},
          {"user":{"login":"LauraMardones"},"created_at":"2026-10-14T09:00:00Z","body":"/unblock"},
          {"user":{"login":"LauraMardones"},"created_at":"2026-10-14T11:00:00Z","body":"please do not /unblock yet"}]' > "$T/gh/comments_7.json"
    retry run --repo o/r
    check "only a PO standalone /unblock after the blocker resolves" $(( $(slack_count) == 1 ? 0 : 1 ))
}

test_closed_issue_stops_retry() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    retry register --repo o/r --event feature_closure_confirmation --issue 10 --context '{}'
    echo closed > "$T/gh/state_7"; echo closed > "$T/gh/state_10"
    touch "$T/slack-fail"
    retry run --repo o/r
    check "closed issue/Feature: no attempt" $(( $(slack_count) == 0 ? 0 : 1 ))
    check "closed issue/Feature: records dropped" $(( $(wc -l < "$(STATE)") == 0 ? 0 : 1 ))
}

test_new_blocker_after_resolved_one_notifies_once() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    retry run --repo o/r
    echo '[{"user":{"login":"LauraMardones"},"created_at":"2026-10-14T11:00:00Z","body":"/unblock"}]' > "$T/gh/comments_7.json"
    retry run --repo o/r   # resolved: dropped
    reg_decision 7 2026-10-14T12:00:00Z
    retry run --repo o/r
    retry run --repo o/r
    check "first blocker + new blocker = two deliveries" $(( $(slack_count) == 2 ? 0 : 1 ))
}

test_persistent_failure_is_red_and_comments_once() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    touch "$T/slack-fail"
    retry run --repo o/r; r1=$?
    retry run --repo o/r; r2=$?
    check "cycles 1 and 2 stay within the bound (exit 0)" $(( r1 + r2 ))
    check "no comment before the bound" $(( $(cat "$T/gh/posts.log" 2>/dev/null | wc -l) == 0 ? 0 : 1 ))
    retry run --repo o/r; r3=$?
    retry run --repo o/r; r4=$?
    check "cycle 3 ends red" $(( r3 == 1 ? 0 : 1 ))
    check "later failing cycles stay red" $(( r4 == 1 ? 0 : 1 ))
    check "exactly one comment" $(( $(grep -c " :: " "$T/gh/posts.log") == 1 ? 0 : 1 ))
    check "comment names event, issue and first failure" \
        $(grep -q 'decision_blocker.*#7.*first failed attempt: 20' "$T/gh/posts.log"; echo $?)
    check "stdout stays empty" $(( $(retry run --repo o/r | wc -c) == 0 ? 0 : 1 ))
    rm "$T/slack-fail"
    retry run --repo o/r; r5=$?
    check "recovery delivers and exits 0" $(( r5 + ($(slack_count) == 6 ? 0 : 1) ))
}

test_drain_delivers_without_counting_failures() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    touch "$T/slack-fail"
    for _ in 1 2 3 4; do retry run --repo o/r --drain; rc=$?; done
    check "failing drains never exit red" $(( rc ))
    check "failing drains post no comment" $(( $(cat "$T/gh/posts.log" 2>/dev/null | wc -l) == 0 ? 0 : 1 ))
    check "failing drains leave failed_cycles at 0" $(( $(jq -s '.[0].failed_cycles' "$(STATE)") == 0 ? 0 : 1 ))
    rm "$T/slack-fail"
    retry run --repo o/r --drain
    check "a drain delivers and records the event" $(( $(jq -s '[.[]|select(.delivered)]|length' "$(STATE)") == 1 ? 0 : 1 ))
    retry run --repo o/r
    check "the cycle's own run sends nothing more" $(( $(slack_count) == 5 ? 0 : 1 ))
}

test_invoke_drains_before_every_chained_exec() {
    local f="$REPO_ROOT/scripts/dispatcher-invoke.sh" n bad=0
    n=$(grep -c '^ *exec bash "\$0"' "$f")
    while IFS=: read -r ln _; do
        sed -n "$((ln - 1))p" "$f" | grep -q 'drain_actionable_events' || bad=1
    done < <(grep -n '^ *exec bash "\$0"' "$f")
    check "invoke has chained exec sites" $(( n >= 2 ? 0 : 1 ))
    check "each chained exec is preceded by drain_actionable_events" $bad
}

test_bad_state_warns_and_continues() {
    local kind
    for kind in malformed unreadable; do
        setup
        mkdir -p "$T/state/o/r"
        if [[ $kind == malformed ]]; then
            echo 'not json' > "$(STATE)"
        else
            mkdir "$(STATE)"   # a directory cannot be copied as a file
        fi
        retry run --repo o/r; rc=$?
        check "$kind state: does not abort" $(( rc ))
        check "$kind state: warns" $(grep -q 'Warning' "$T/stderr"; echo $?)
    done
    setup
    mkdir -p "$T/state/o/r"; echo '{"key":"k"}' > "$(STATE)"
    retry run --repo o/r; rc=$?
    check "object missing fields is malformed, not fatal" $(( rc ))
    setup
    retry run --repo o/r; rc=$?
    check "missing state: exit 0, no warning" $(( rc + $(grep -c Warning "$T/stderr") ))
}

test_malformed_line_keeps_pending_record() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    printf '{"key":"trunc\n' >> "$(STATE)"
    retry run --repo o/r; rc=$?
    check "pending record next to a malformed line: exit 0" $(( rc ))
    check "pending record next to a malformed line: still delivered" $(( $(slack_count) == 1 ? 0 : 1 ))
    check "pending record next to a malformed line: warns" $(grep -q 'Warning' "$T/stderr"; echo $?)
    check "pending record next to a malformed line: recorded delivered" $(( $(jq -s '[.[]|select(.delivered)]|length' "$(STATE)") == 1 ? 0 : 1 ))
}

test_unwritable_state_is_observable() {
    setup
    : > "$T/blocker"   # a file where the state directory should be
    export DISPATCHER_STATE_DIR="$T/blocker"
    retry register --repo o/r --event decision_blocker --issue 7 --since 2026-10-14T10:00:00Z --context '{"story_title":"S"}'; rc=$?
    check "unwritable state: register delivers synchronously, exit 0" $(( rc ))
    check "unwritable state: one send" $(( $(slack_count) == 1 ? 0 : 1 ))
    touch "$T/slack-fail"
    retry register --repo o/r --event decision_blocker --issue 8 --since 2026-10-14T10:00:00Z --context '{"story_title":"S"}'; rc=$?
    check "unwritable state and failed send: register fails" $(( rc == 1 ? 0 : 1 ))
    check "unwritable state and failed send: error logged" $(grep -q 'Error: cannot record or deliver' "$T/stderr"; echo $?)
    retry run --repo o/r; rc=$?
    check "unwritable state: run exits non-zero" $(( rc == 1 ? 0 : 1 ))
    check "unwritable state: run logs an error" $(grep -q 'could not be saved' "$T/stderr"; echo $?)
}

test_directory_valued_state_path_is_replaced() {
    setup
    mkdir -p "$(STATE)"   # the state path is a directory
    reg_decision 7 2026-10-14T10:00:00Z; rc=$?
    check "directory state path: register exits 0" $(( rc ))
    check "directory state path: state is now a regular file" $([[ -f "$(STATE)" ]]; echo $?)
    check "directory state path: the record is kept" $(( $(jq -s 'length' "$(STATE)") == 1 ? 0 : 1 ))
    retry run --repo o/r; rc=$?
    check "directory state path: next run delivers once" $(( rc + ($(slack_count) == 1 ? 0 : 1) ))
}

# Issue #352: a fake cp on PATH fails for the state file while $T/unreadable
# exists (chmod 000 does not block root or Windows Git Bash).
make_state_unreadable() {
    local real; real=$(command -v cp)
    cat > "$T/bin/cp" <<FAKE
#!/usr/bin/env bash
if [[ -e "$T/unreadable" && "\$1" == */actionable-events ]]; then exit 1; fi
exec "$real" "\$@"
FAKE
    chmod +x "$T/bin/cp"
    : > "$T/unreadable"
}

test_unreadable_state_file_is_kept() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    reg_decision 8 2026-10-14T10:00:00Z
    cp "$(STATE)" "$T/before"
    make_state_unreadable

    retry run --repo o/r; rc=$?
    check "unreadable state, run: exits 1" $(( rc == 1 ? 0 : 1 ))
    check "unreadable state, run: Error line names the file" $(grep -q "\[NOTIFY\] Error:.*actionable-events" "$T/stderr"; echo $?)
    check "unreadable state, run: sends nothing" $(( $(slack_count) == 0 ? 0 : 1 ))
    check "unreadable state, run: file unchanged" $(cmp -s "$T/before" "$(STATE)"; echo $?)

    retry run --repo o/r --drain; rc=$?
    check "unreadable state, drain: exits 0" $(( rc ))
    check "unreadable state, drain: Warning line" $(grep -q "\[NOTIFY\] Warning:" "$T/stderr"; echo $?)
    check "unreadable state, drain: sends nothing" $(( $(slack_count) == 0 ? 0 : 1 ))
    check "unreadable state, drain: file unchanged" $(cmp -s "$T/before" "$(STATE)"; echo $?)

    retry register --repo o/r --event epic_closure_approval --issue 20 --context '{"epic_title":"E"}'; rc=$?
    check "unreadable state, register: delivers directly, exit 0" $(( rc + ($(slack_count) == 1 ? 0 : 1) ))
    check "unreadable state, register: file unchanged" $(cmp -s "$T/before" "$(STATE)"; echo $?)

    touch "$T/slack-fail"
    retry register --repo o/r --event epic_closure_approval --issue 21 --context '{"epic_title":"E"}'; rc=$?
    check "unreadable state, register: failed delivery exits 1" $(( rc == 1 ? 0 : 1 ))
    check "unreadable state, register: file still unchanged" $(cmp -s "$T/before" "$(STATE)"; echo $?)
    rm "$T/slack-fail"

    rm "$T/unreadable"; : > "$T/slack.log"
    retry run --repo o/r; rc=$?
    check "readable again: run exits 0" $(( rc ))
    check "readable again: both held events delivered" $(( $(slack_count) == 2 ? 0 : 1 ))
}

test_dry_run_sends_nothing() {
    setup
    reg_decision 7 2026-10-14T10:00:00Z
    cp "$(STATE)" "$T/before"
    retry run --repo o/r --dry-run; rc=$?
    retry register --repo o/r --event epic_closure_approval --issue 20 --context '{}' --dry-run
    check "dry-run: exit 0" $(( rc ))
    check "dry-run: nothing sent" $(( $(slack_count) == 0 ? 0 : 1 ))
    check "dry-run: state untouched" $(cmp -s "$T/before" "$(STATE)"; echo $?)
    check "dry-run: no comment" $([[ ! -e "$T/gh/posts.log" ]]; echo $?)
    check "dry-run: logs the would-be send" $(grep -q 'DRY RUN' "$T/stderr"; echo $?)
}

# evidence_unwritable (issue #372): <since> is "<timestamp>:<item>:<ref>"; the
# context says how the posted evidence is recognised.
EV_CTX='{"story_title":"S","story_number":"7","issue_url":"u","item":"merge-confirmation","ref":"pr5","target_number":"5","target_url":"t","minutes":20,"has_pending_text":true,"resolve_kind":"payload","resolve_text":"Merge gate: ok","gave_up_marker":"<!-- hpw:dispatcher action=evidence-gave-up item=merge-confirmation ref=pr5 -->"}'
reg_evidence() {  # reg_evidence <issue> <item> [<context>]
    retry register --repo o/r --event evidence_unwritable --issue "$1" \
        --since "2026-10-31T10:00:00Z:$2:pr5" --context "${3:-$EV_CTX}"
}

test_evidence_unwritable_on_a_closed_story_stays_unresolved_and_is_sent() {
    setup
    reg_evidence 7 merge-confirmation
    echo closed > "$T/gh/state_7"
    retry run --repo o/r; rc=$?
    check "evidence_unwritable closed story: exit 0" $(( rc ))
    check "evidence_unwritable closed story: sent" $(( $(slack_count) == 1 ? 0 : 1 ))
    retry run --repo o/r
    check "evidence_unwritable closed story: sent once, record kept" $(( $(slack_count) == 1 && $(wc -l < "$(STATE)") == 1 ? 0 : 1 ))
}

test_evidence_unwritable_resolved_when_the_evidence_is_posted() {
    setup
    reg_evidence 7 merge-confirmation
    echo '[{"user":{"login":"dispatcher-bot"},"created_at":"2026-10-31T10:30:00Z","body":"Merge gate: ok\nmore"}]' > "$T/gh/comments_5.json"
    touch "$T/slack-fail"
    retry run --repo o/r; rc=$?
    check "evidence posted: exit 0" $(( rc ))
    check "evidence posted: nothing sent" $(( $(slack_count) == 0 ? 0 : 1 ))
    check "evidence posted: record dropped" $(( $(wc -l < "$(STATE)") == 0 ? 0 : 1 ))
}

test_evidence_unwritable_not_resolved_by_another_account_or_the_pending_comment() {
    setup
    reg_evidence 7 merge-confirmation
    echo '[{"user":{"login":"someone"},"created_at":"2026-10-31T10:30:00Z","body":"Merge gate: ok"},
           {"user":{"login":"dispatcher-bot"},"created_at":"2026-10-31T10:31:00Z","body":"Merge gate: ok <!-- hpw:dispatcher action=evidence-pending item=merge-confirmation -->"}]' > "$T/gh/comments_5.json"
    retry run --repo o/r
    check "foreign or pending copy does not resolve: sent" $(( $(slack_count) == 1 ? 0 : 1 ))
}

test_evidence_unwritable_resolved_by_the_give_up_blocker() {
    setup
    reg_evidence 7 merge-confirmation
    echo '[{"user":{"login":"dispatcher-bot"},"created_at":"2026-10-31T10:30:00Z","body":"## Blocked Declaration\n<!-- hpw:dispatcher action=evidence-gave-up item=merge-confirmation ref=pr5 -->"}]' > "$T/gh/comments_7.json"
    retry run --repo o/r
    check "give-up blocker: nothing sent" $(( $(slack_count) == 0 ? 0 : 1 ))
    check "give-up blocker: record dropped" $(( $(wc -l < "$(STATE)") == 0 ? 0 : 1 ))
}

test_evidence_unwritable_give_up_blocker_of_another_item_does_not_resolve() {
    setup
    reg_evidence 7 merge-confirmation
    echo '[{"user":{"login":"dispatcher-bot"},"created_at":"2026-10-31T10:30:00Z","body":"<!-- hpw:dispatcher action=evidence-gave-up item=merge-owner ref=pr5 -->"}]' > "$T/gh/comments_7.json"
    retry run --repo o/r
    check "other item's blocker: sent" $(( $(slack_count) == 1 ? 0 : 1 ))
}

test_evidence_unwritable_two_items_on_one_story_are_two_events() {
    setup
    reg_evidence 7 merge-confirmation
    reg_evidence 7 cleanup-summary
    reg_evidence 7 cleanup-summary
    check "two items, one repeat: two records" $(( $(wc -l < "$(STATE)") == 2 ? 0 : 1 ))
}

test_evidence_unwritable_failed_delivery_is_retried_and_red_after_three_cycles() {
    setup
    reg_evidence 7 merge-confirmation
    touch "$T/slack-fail"
    retry run --repo o/r; rc1=$?
    retry run --repo o/r; rc2=$?
    retry run --repo o/r; rc3=$?
    check "evidence_unwritable: cycles 1 and 2 stay green" $(( rc1 + rc2 ))
    check "evidence_unwritable: cycle 3 is red" $(( rc3 == 1 ? 0 : 1 ))
    rm "$T/slack-fail"
    retry run --repo o/r
    check "evidence_unwritable: delivered after recovery" $(( $(jq -s '[.[]|select(.delivered)]|length' "$(STATE)") == 1 ? 0 : 1 ))
}

test_evidence_unwritable_unreadable_facts_count_as_unresolved() {
    setup
    reg_evidence 7 merge-confirmation '{"story_title":"S"}'
    retry run --repo o/r
    check "context without resolve facts: still sent" $(( $(slack_count) == 1 ? 0 : 1 ))
}

test_evidence_unwritable_dry_run_registers_nothing() {
    setup
    retry register --repo o/r --event evidence_unwritable --issue 7 --since "2026-10-31T10:00:00Z:x:y" --context "$EV_CTX" --dry-run
    check "evidence_unwritable dry-run: no state" $([[ ! -e "$(STATE)" ]]; echo $?)
}

test_fail_then_succeed_delivers_once
test_delivered_event_not_resent_when_registered_again
test_closure_events_retried
test_unblock_stops_retry_even_if_undelivered
test_unblock_by_someone_else_or_before_blocker_does_not_resolve
test_closed_issue_stops_retry
test_new_blocker_after_resolved_one_notifies_once
test_persistent_failure_is_red_and_comments_once
test_drain_delivers_without_counting_failures
test_invoke_drains_before_every_chained_exec
test_bad_state_warns_and_continues
test_malformed_line_keeps_pending_record
test_unwritable_state_is_observable
test_directory_valued_state_path_is_replaced
test_unreadable_state_file_is_kept
test_dry_run_sends_nothing
test_evidence_unwritable_on_a_closed_story_stays_unresolved_and_is_sent
test_evidence_unwritable_resolved_when_the_evidence_is_posted
test_evidence_unwritable_not_resolved_by_another_account_or_the_pending_comment
test_evidence_unwritable_resolved_by_the_give_up_blocker
test_evidence_unwritable_give_up_blocker_of_another_item_does_not_resolve
test_evidence_unwritable_two_items_on_one_story_are_two_events
test_evidence_unwritable_failed_delivery_is_retried_and_red_after_three_cycles
test_evidence_unwritable_unreadable_facts_count_as_unresolved
test_evidence_unwritable_dry_run_registers_nothing

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
