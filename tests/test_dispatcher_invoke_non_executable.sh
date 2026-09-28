#!/usr/bin/env bash
# tests/test_dispatcher_invoke_non_executable.sh
#
# Regression tests for issue #256: a non-executable item (Epic, Feature,
# missing or unsupported type) in "Ready for implementation" must be skipped
# with a warning instead of crashing the dispatcher.
#
# Runs the real scripts/dispatcher-invoke.sh end to end (no function copies)
# with a fake `gh` and a fake `curl` on PATH. The fake `gh` serves a stateful
# project board: board mutations are recorded and reflected in later board
# fetches, so a live (non-dry-run) story cycle can reach Done and the loop's
# "next ready story" search runs against the updated board.
#
# Scenarios covered:
#   1. Incident reproduction, live: type:epic first, valid Story second ->
#      one warning for the Epic, executor invoked for the Story only, Epic
#      never reselected, exit 0, no all_budget_blocked
#   2. Same as 1 with type:feature, under --dry-run -> same skip decision,
#      no executor call, no GitHub mutation
#   3. All ready items non-executable (Epic + Feature) -> exit 0, no
#      executor call, no all_budget_blocked, one warning each
#   4. Missing type metadata -> warning says the type is missing
#   5. Unsupported type label -> warning says the type is unsupported
#   6. Native GitHub issue type used only as a fallback (no type: label)
#   7. Executable Story with no executor: label -> still fatal (exit 1),
#      with a readable "Add one of:" hint
#   8. Executable Story with an unrecognised executor: label -> still fatal
#   9. Earlier budget skip + trailing non-executable item -> all_budget_blocked
#      reflects only the budget-blocked executable story
#  10. format_executor_hint() output shape
#
# Usage: bash tests/test_dispatcher_invoke_non_executable.sh
# Requires: bash 4+, jq

set -euo pipefail

PASS=0
FAIL=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCHER_SCRIPT="$SCRIPT_DIR/scripts/dispatcher-invoke.sh"

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

# ─── Test harness ─────────────────────────────────────────────────────────────

pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1"; [[ -n "${2:-}" ]] && echo "$2" | sed 's/^/        /'; FAIL=$(( FAIL + 1 )); }

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if grep -qF -- "$needle" <<< "$haystack"; then pass "$label"; else fail "$label" "Expected: $needle"$'\n'"$haystack"; fi
}

assert_not_contains() {
    local label="$1" haystack="$2" needle="$3"
    if ! grep -qF -- "$needle" <<< "$haystack"; then pass "$label"; else fail "$label" "Did NOT expect: $needle"$'\n'"$haystack"; fi
}

assert_eq() {
    local label="$1" actual="$2" expected="$3"
    if [[ "$actual" == "$expected" ]]; then pass "$label"; else fail "$label" "Expected: $expected"$'\n'"Actual:   $actual"; fi
}

count_matches() { grep -cF -- "$2" <<< "$1" || true; }

# ─── Fakes ────────────────────────────────────────────────────────────────────

FAKE_BIN="$GLOBAL_TMP/bin"
mkdir -p "$FAKE_BIN"

# Fake gh. State lives in $MOCK_DIR:
#   board.json   — items: [{id, number, title, body, status}], options: {name: id}
#   state.json   — {itemId: statusName} overrides written by board mutations
#   issue_N.json — REST issue payload for #N
#   pulls.json   — REST open-PR list
#   gh.log       — one line per call
cat > "$FAKE_BIN/gh" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "gh $*" >> "$MOCK_DIR/gh.log"
[[ "${1:-}" == "api" ]] || { echo "fake gh: unsupported: $*" >&2; exit 1; }
shift
endpoint="$1"; shift
query="" item_id="" option_id="" method="GET"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f) case "$2" in
                query=*)    query="${2#query=}" ;;
                itemId=*)   item_id="${2#itemId=}" ;;
                optionId=*) option_id="${2#optionId=}" ;;
            esac
            shift 2 ;;
        -X) method="$2"; shift 2 ;;
        *)  shift ;;
    esac
done

if [[ "$endpoint" == "graphql" ]]; then
    if [[ "$query" == *mutation* ]]; then
        echo "MUTATION item=$item_id option=$option_id" >> "$MOCK_DIR/gh.log"
        name=$(jq -r --arg o "$option_id" '.options | to_entries[] | select(.value == $o) | .key' "$MOCK_DIR/board.json")
        jq --arg i "$item_id" --arg n "$name" '. + {($i): $n}' "$MOCK_DIR/state.json" > "$MOCK_DIR/state.tmp"
        mv "$MOCK_DIR/state.tmp" "$MOCK_DIR/state.json"
        echo '{}'
        exit 0
    fi
    jq --slurpfile st "$MOCK_DIR/state.json" '
        . as $b |
        {data:{repository:{projectsV2:{nodes:[{
            id: "PROJ",
            fields: {nodes: [{id: "STATUS_FIELD", name: "Status",
                              options: [$b.options | to_entries[] | {id: .value, name: .key}]}]},
            items: {
                pageInfo: {hasNextPage: false, endCursor: null},
                nodes: [$b.items[] | {
                    id: .id,
                    updatedAt: "2026-09-27T00:00:00Z",
                    content: {number: .number, title: .title, body: (.body // ""),
                              updatedAt: "2026-09-27T00:00:00Z"},
                    fieldValues: {nodes: [{name: ($st[0][.id] // .status),
                                           field: {name: "Status"}}]}
                }]
            }
        }]}}}}' "$MOCK_DIR/board.json"
    exit 0
fi

path="${endpoint%%\?*}"
case "$path" in
    repos/*/issues/*/comments)
        if [[ "$method" == "POST" ]]; then
            echo "POST $path" >> "$MOCK_DIR/gh.log"
            echo '{}'
        else
            echo '[]'
        fi ;;
    repos/*/issues/*)
        n="${path##*/}"
        [[ -f "$MOCK_DIR/issue_$n.json" ]] || { echo "fake gh: no issue $n" >&2; exit 1; }
        cat "$MOCK_DIR/issue_$n.json" ;;
    repos/*/pulls)
        cat "$MOCK_DIR/pulls.json" ;;
    *)
        echo '[]' ;;
esac
EOF
chmod +x "$FAKE_BIN/gh"

# Fake curl: never touches the network. Logs the URL and returns a
# single-turn "task complete" response for either provider.
cat > "$FAKE_BIN/curl" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
outfile="" url=""
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
    case "${args[$i]}" in
        -o) i=$((i+1)); outfile="${args[$i]}" ;;
        http*://*) url="${args[$i]}" ;;
    esac
    i=$((i+1))
done
echo "url=$url" >> "$MOCK_DIR/curl.log"
if [[ "$url" == *anthropic* ]]; then
    printf '%s' '{"stop_reason":"end_turn","content":[{"type":"text","text":"done"}],"usage":{"input_tokens":1,"output_tokens":1}}' > "$outfile"
else
    printf '%s' '{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"done"}}]}' > "$outfile"
fi
echo -n "200"
EOF
chmod +x "$FAKE_BIN/curl"

# ─── Fixture helpers ──────────────────────────────────────────────────────────

# new_env — fresh mock dir; echoes its path
new_env() {
    local d
    d=$(mktemp -d "$GLOBAL_TMP/env.XXXXXX")
    echo '{}' > "$d/state.json"
    echo '[]' > "$d/pulls.json"
    : > "$d/gh.log"
    : > "$d/curl.log"
    : > "$d/github_output"
    mkdir -p "$d/budget"
    jq -n '{items: [], options: {
        "Ready for implementation": "OPT_READY", "In implementation": "OPT_IMPL",
        "In review": "OPT_REVIEW", "Ready to merge": "OPT_MERGE", "Done": "OPT_DONE"}}' \
        > "$d/board.json"
    echo "$d"
}

# add_item <dir> <number> <status> <native-type-or-empty> <label>...
add_item() {
    local d="$1" n="$2" status="$3" native="$4"; shift 4
    local labels_json
    labels_json=$(printf '%s\n' "$@" | jq -R 'select(length > 0) | {name: .}' | jq -s '.')
    jq --argjson n "$n" --arg s "$status" \
        '.items += [{id: ("ITEM_\($n)"), number: $n, title: "Item \($n)", body: "", status: $s}]' \
        "$d/board.json" > "$d/board.tmp" && mv "$d/board.tmp" "$d/board.json"
    jq -n --argjson n "$n" --argjson labels "$labels_json" --arg native "$native" \
        '{number: $n, title: "Item \($n)", body: "", labels: $labels}
         + (if $native == "" then {} else {type: {name: $native}} end)' \
        > "$d/issue_$n.json"
}

# link_pr <dir> <pr-number> <issue-number>
link_pr() {
    jq --argjson p "$2" --argjson n "$3" \
        '. += [{number: $p, body: "Closes #\($n)", updated_at: "2026-09-27T00:00:00Z", head: {ref: "x"}}]' \
        "$1/pulls.json" > "$1/pulls.tmp" && mv "$1/pulls.tmp" "$1/pulls.json"
}

# run_dispatcher <dir> <args...> — sets RUN_RC, RUN_OUT (stdout), RUN_ERR (stderr)
run_dispatcher() {
    local d="$1"; shift
    RUN_RC=0
    env -i HOME="$HOME" PATH="$FAKE_BIN:/usr/bin:/bin" \
        MOCK_DIR="$d" GH_TOKEN="fake-token" GITHUB_OUTPUT="$d/github_output" \
        ANTHROPIC_API_KEY="fake-anthropic" OPENAI_API_KEY_CODEX="fake-openai" \
        BUDGET_COUNTER_DIR="$d/budget" \
        BUDGET_DAILY_HAIKU="${BUDGET_DAILY:-10000000}" BUDGET_DAILY_SONNET="${BUDGET_DAILY:-10000000}" \
        BUDGET_DAILY_OPUS="${BUDGET_DAILY:-10000000}" BUDGET_DAILY_CODEX="${BUDGET_DAILY:-10000000}" \
        AGENT_MAX_TURNS=3 AGENT_MAX_WALLCLOCK_SECONDS=600 \
        timeout 120 bash "$DISPATCHER_SCRIPT" --repo "o/r" "$@" \
        > "$d/stdout" 2> "$d/stderr" || RUN_RC=$?
    RUN_OUT=$(cat "$d/stdout")
    RUN_ERR=$(cat "$d/stderr")
}

READY="Ready for implementation"

# ─── 1. Incident: Epic first, Story second (live) ─────────────────────────────

echo ""
echo "1. Epic first, valid Story second (live run)"
E=$(new_env)
add_item "$E" 160 "$READY" "" type:epic
add_item "$E" 161 "$READY" "" type:story executor:claude-code-sonnet
link_pr "$E" 900 161
run_dispatcher "$E" --issue 160

assert_eq "exit 0" "$RUN_RC" "0"
assert_eq "exactly one warning for #160" "$(count_matches "$RUN_ERR" "Warning: #160 is not an executable")" "1"
assert_contains "warning names the detected type" "$RUN_ERR" \
    "Warning: #160 is not an executable Story, Task, or Bug (detected type: type:epic); skipping it for this run and continuing selection."
assert_not_contains "no executor-label error for the Epic" "$RUN_ERR" "No executor: label found on #160"
assert_contains "Story #161 went through /implement" "$RUN_OUT" "E1: /implement #161"
assert_contains "Story #161 reached Done" "$RUN_OUT" "Story #161 completed (Done)."
assert_not_contains "no /implement for the Epic" "$RUN_OUT" "E1: /implement #160"
assert_contains "executor API was called" "$(cat "$E/curl.log")" "url=https://api.anthropic.com"
assert_not_contains "Epic board item never mutated" "$(cat "$E/gh.log")" "MUTATION item=ITEM_160"
assert_contains "loop exited cleanly after the Story" "$RUN_OUT" "No more \"Ready for implementation\" stories."
assert_not_contains "all_budget_blocked not set" "$(cat "$E/github_output")" "all_budget_blocked"

# ─── 2. Feature first, Story second (--dry-run) ───────────────────────────────

echo ""
echo "2. Feature first, valid Story second (--dry-run)"
E=$(new_env)
add_item "$E" 231 "$READY" "" type:feature
add_item "$E" 232 "$READY" "" type:task executor:claude-code-sonnet
link_pr "$E" 901 232
run_dispatcher "$E" --issue 231 --dry-run

assert_eq "exit 0" "$RUN_RC" "0"
assert_contains "warning names type:feature" "$RUN_ERR" "Warning: #231 is not an executable Story, Task, or Bug (detected type: type:feature)"
assert_eq "exactly one warning for #231" "$(count_matches "$RUN_ERR" "Warning: #231")" "1"
assert_contains "dry-run reports the Story's executor invocation" "$RUN_OUT" "[DRY RUN] Would invoke anthropic (claude-sonnet-5) directly via API for /implement 232"
assert_not_contains "dry-run: no invocation for the Feature" "$RUN_OUT" "/implement 231"
assert_eq "dry-run: no executor API call" "$(cat "$E/curl.log")" ""
assert_not_contains "dry-run: no board mutation" "$(cat "$E/gh.log")" "MUTATION"
assert_not_contains "dry-run: no comment posted" "$(cat "$E/gh.log")" "POST "
assert_not_contains "dry-run: loop does not reselect #231" "$RUN_OUT" "--issue 231"

# ─── 3. All ready items non-executable ────────────────────────────────────────

echo ""
echo "3. Every ready item is non-executable"
E=$(new_env)
add_item "$E" 10 "$READY" "" type:epic
add_item "$E" 11 "$READY" "" type:feature
run_dispatcher "$E" --issue 10

assert_eq "exit 0" "$RUN_RC" "0"
assert_eq "one warning for #10" "$(count_matches "$RUN_ERR" "Warning: #10 ")" "1"
assert_eq "one warning for #11" "$(count_matches "$RUN_ERR" "Warning: #11 ")" "1"
assert_contains "clean exit message" "$RUN_OUT" "No more executable \"Ready for implementation\" stories after non-executable skip."
assert_eq "no executor API call" "$(cat "$E/curl.log")" ""
assert_not_contains "no board mutation" "$(cat "$E/gh.log")" "MUTATION"
assert_not_contains "all_budget_blocked not set" "$(cat "$E/github_output")" "all_budget_blocked"

# ─── 4. Missing type ──────────────────────────────────────────────────────────

echo ""
echo "4. Missing type metadata"
E=$(new_env)
add_item "$E" 20 "$READY" "" executor:claude-code-sonnet
run_dispatcher "$E" --issue 20

assert_eq "exit 0" "$RUN_RC" "0"
assert_contains "warning states the type is missing" "$RUN_ERR" \
    "Warning: #20 is not an executable Story, Task, or Bug (type missing: no type: label or native issue type)"
assert_eq "no executor API call (executor: label does not imply executable)" "$(cat "$E/curl.log")" ""

# ─── 5. Unsupported type label ────────────────────────────────────────────────

echo ""
echo "5. Unsupported type label"
E=$(new_env)
add_item "$E" 30 "$READY" "" type:spike executor:claude-code-sonnet
add_item "$E" 31 "$READY" "" type:story type:epic executor:claude-code-sonnet
run_dispatcher "$E" --issue 30

assert_eq "exit 0" "$RUN_RC" "0"
assert_contains "warning states the type is unsupported" "$RUN_ERR" "Warning: #30 is not an executable Story, Task, or Bug (unsupported type: type:spike)"
assert_contains "conflicting type labels are unsupported" "$RUN_ERR" "Warning: #31 is not an executable Story, Task, or Bug (unsupported type: conflicting type labels type:story type:epic)"
assert_eq "no executor API call" "$(cat "$E/curl.log")" ""

# ─── 6. Native issue type fallback ────────────────────────────────────────────

echo ""
echo "6. Native GitHub issue type as fallback"
E=$(new_env)
add_item "$E" 40 "$READY" "Feature" executor:claude-code-sonnet
add_item "$E" 41 "$READY" "Bug" executor:claude-code-sonnet
link_pr "$E" 902 41
run_dispatcher "$E" --issue 40 --dry-run

assert_eq "exit 0" "$RUN_RC" "0"
assert_contains "native Feature is skipped" "$RUN_ERR" "Warning: #40 is not an executable Story, Task, or Bug (unsupported type: native issue type 'Feature')"
assert_contains "native Bug is executable" "$RUN_OUT" "Issue type: native issue type 'Bug' (executable)"
assert_contains "native Bug reaches executor (dry-run)" "$RUN_OUT" "for /implement 41"

# ─── 7. Executable Story without executor: label stays fatal ──────────────────

echo ""
echo "7. Executable Story with no executor: label"
E=$(new_env)
add_item "$E" 50 "$READY" "" type:story
run_dispatcher "$E" --issue 50

assert_eq "exit 1" "$RUN_RC" "1"
assert_contains "configuration error reported" "$RUN_ERR" "Error: No executor: label found on #50."
assert_contains "readable hint" "$RUN_ERR" \
    "       Add one of: executor:claude-code-haiku, executor:claude-code-opus, executor:claude-code-sonnet, executor:codex"
assert_not_contains "no skip warning for an executable Story" "$RUN_ERR" "is not an executable"

# ─── 8. Executable Story with unrecognised executor: label stays fatal ────────

echo ""
echo "8. Executable Story with an unrecognised executor: label"
E=$(new_env)
add_item "$E" 60 "$READY" "" type:bug executor:mystery
run_dispatcher "$E" --issue 60

assert_eq "exit 1" "$RUN_RC" "1"
assert_contains "unrecognised executor error" "$RUN_ERR" "Error: Unrecognised executor label 'executor:mystery' on #60."

# ─── 9. Budget skip followed by a non-executable item ─────────────────────────

echo ""
echo "9. Budget-blocked Story, then only a non-executable item remains"
E=$(new_env)
add_item "$E" 70 "$READY" "" type:story executor:claude-code-sonnet
add_item "$E" 71 "$READY" "" type:epic
# Pre-seed today's sonnet usage above the cap so the budget check reports it reached.
echo "$(date -u +%Y-%m-%d):5" > "$E/budget/budget-sonnet"
BUDGET_DAILY=1 run_dispatcher "$E" --issue 70

assert_eq "exit 0" "$RUN_RC" "0"
assert_contains "Story was budget-skipped" "$RUN_OUT" "[BUDGET SKIP] #70"
assert_contains "Epic skipped as non-executable" "$RUN_ERR" "Warning: #71 is not an executable"
assert_contains "all_budget_blocked reflects the budget-blocked Story" "$(cat "$E/github_output")" "all_budget_blocked=true"
assert_eq "no executor API call" "$(cat "$E/curl.log")" ""

# ─── 10. format_executor_hint() ───────────────────────────────────────────────

echo ""
echo "10. format_executor_hint()"
HINT=$(bash -c "
    $(sed -n '/^declare -A EXECUTOR_ROUTING=(/,/^)/p' "$DISPATCHER_SCRIPT")
    $(sed -n '/^format_executor_hint() {/,/^}/p' "$DISPATCHER_SCRIPT")
    format_executor_hint
")
assert_eq "hint is comma-separated executor:<name> list" "$HINT" \
    "executor:claude-code-haiku, executor:claude-code-opus, executor:claude-code-sonnet, executor:codex"

# ─── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
