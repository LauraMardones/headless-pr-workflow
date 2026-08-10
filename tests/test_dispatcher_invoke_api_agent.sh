#!/usr/bin/env bash
# tests/test_dispatcher_invoke_api_agent.sh
#
# Regression test for issue #254: dispatcher-invoke.sh's invoke_executor_command()
# used to shell out to a locally-installed CLI binary ("$cli" --dangerously-
# skip-permissions -p "/$slash_command $target_arg") that is never installed on
# the GitHub Actions runner, so every dispatcher-driven /implement invocation
# failed immediately with `env: 'claude': No such file or directory`. This test
# proves, against the real EXECUTOR_PROVIDER/EXECUTOR_MODEL tables and the real
# invoke_executor_command()/run_anthropic_agent()/run_openai_agent()/_curl_json()
# implementations:
#
#   1. The defect no longer reproduces: a dispatcher invocation completes
#      successfully with PATH containing no `claude` or `codex` executable at
#      all — every call reaches the target provider through curl only.
#   2. A multi-turn tool-use conversation (the model requests a bash-tool call,
#      then returns a final answer) round-trips correctly end-to-end for both
#      providers, using the real .claude/commands/implement.md file as the task
#      prompt with $ARGUMENTS correctly substituted.
#   3. Each executor tier resolves to its own distinct API model ID
#      (EXECUTOR_MODEL), for both the Anthropic and OpenAI request shapes.
#   4. Failure-mode / fail-closed coverage, matching the old CLI-missing case's
#      contract (non-zero exit, no work silently swallowed):
#        - a malformed API response (missing the expected content field)
#        - a non-2xx HTTP status from the provider
#        - the agent loop exceeding AGENT_MAX_TURNS without completing
#      all return non-zero from invoke_executor_command() with a clear stderr
#      message, for both providers where applicable.
#   5. --dry-run makes no HTTP call at all and does not require any secret's
#      value beyond what pre-flight already validates.
#
# Usage: bash tests/test_dispatcher_invoke_api_agent.sh
# Requires: bash 4+, jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INVOKE_SCRIPT="$REPO_ROOT/scripts/dispatcher-invoke.sh"

PASS=0
FAIL=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

assert() {
    local label="$1" cond="$2"
    if [[ "$cond" == "0" ]]; then
        echo "PASS: $label"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: $label"
        FAIL=$(( FAIL + 1 ))
    fi
}

# ─── Extract the real tables + agent functions ────────────────────────────────

TABLES_AND_FUNC="$GLOBAL_TMP/extracted.sh"
{
    awk '/^declare -A EXECUTOR_ROUTING=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^declare -A EXECUTOR_PROVIDER=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^declare -A EXECUTOR_MODEL=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^_curl_json\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^_run_agent_bash_tool\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^run_anthropic_agent\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^run_openai_agent\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^invoke_executor_command\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
} > "$TABLES_AND_FUNC"

for fn in _curl_json _run_agent_bash_tool run_anthropic_agent run_openai_agent invoke_executor_command; do
    if ! grep -q "^${fn}() {" "$TABLES_AND_FUNC"; then
        echo "FAIL: could not extract ${fn}() from $INVOKE_SCRIPT (renamed or removed?)"
        exit 1
    fi
done

# ─── Fake curl: no network, mode-driven canned responses ─────────────────────
# Reads -o <file> / --data @<file> / the URL from argv, logs the request body
# and URL, and writes a mode-specific response. FAKE_CURL_MODE selects the
# scenario; a per-invocation counter file lets a mode return different bodies
# on turn 1 vs turn 2 (to exercise a real multi-turn tool-use round trip).

FAKE_BIN="$GLOBAL_TMP/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/curl" << 'CURLEOF'
#!/usr/bin/env bash
set -euo pipefail
outfile="" reqfile="" url=""
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
    case "${args[$i]}" in
        -o) i=$((i+1)); outfile="${args[$i]}" ;;
        --data) i=$((i+1)); reqfile="${args[$i]#@}" ;;
        http*://*) url="${args[$i]}" ;;
    esac
    i=$((i+1))
done

STATE_DIR="${FAKE_CURL_STATE_DIR:?}"
counter_file="$STATE_DIR/counter"
[[ -f "$counter_file" ]] || echo 0 > "$counter_file"
count=$(( $(<"$counter_file") + 1 ))
echo "$count" > "$counter_file"
cp "$reqfile" "$STATE_DIR/req_${count}.json"
echo "$url" >> "$STATE_DIR/urls.log"

case "$FAKE_CURL_MODE" in
  two_turn)
    if [[ "$url" == *anthropic* ]]; then
        if [[ $count -eq 1 ]]; then
            printf '%s' '{"stop_reason":"tool_use","content":[{"type":"tool_use","id":"t1","name":"bash","input":{"command":"echo probe"}}],"usage":{"input_tokens":10,"output_tokens":5}}' > "$outfile"
        else
            printf '%s' '{"stop_reason":"end_turn","content":[{"type":"text","text":"anthropic-agent-done"}],"usage":{"input_tokens":5,"output_tokens":3}}' > "$outfile"
        fi
    else
        if [[ $count -eq 1 ]]; then
            printf '%s' '{"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","tool_calls":[{"id":"c1","function":{"name":"bash","arguments":"{\"command\":\"echo probe\"}"}}]}}]}' > "$outfile"
        else
            printf '%s' '{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"openai-agent-done"}}]}' > "$outfile"
        fi
    fi
    echo -n "200"
    ;;
  malformed)
    printf '%s' '{"unexpected":"shape"}' > "$outfile"
    echo -n "200"
    ;;
  http_error)
    printf '%s' '{"type":"error","error":{"message":"unauthorized"}}' > "$outfile"
    echo -n "403"
    ;;
  never_ends)
    if [[ "$url" == *anthropic* ]]; then
        printf '%s' '{"stop_reason":"tool_use","content":[{"type":"tool_use","id":"loop","name":"bash","input":{"command":"true"}}],"usage":{"input_tokens":1,"output_tokens":1}}' > "$outfile"
    else
        printf '%s' '{"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","tool_calls":[{"id":"loop","function":{"name":"bash","arguments":"{\"command\":\"true\"}"}}]}}]}' > "$outfile"
    fi
    echo -n "200"
    ;;
  *)
    echo "unknown FAKE_CURL_MODE: $FAKE_CURL_MODE" >&2
    exit 99
    ;;
esac
CURLEOF
chmod +x "$FAKE_BIN/curl"

run_invoke() {
    # $1=executor label  $2=FAKE_CURL_MODE  $3=AGENT_MAX_TURNS  $4=state dir (fresh)
    local label="$1" mode="$2" max_turns="$3" state_dir="$4"
    bash -c '
        set -euo pipefail
        source "'"$TABLES_AND_FUNC"'"

        EXECUTOR_LABEL="'"$label"'"
        ROUTING_VALUE="${EXECUTOR_ROUTING[$EXECUTOR_LABEL]}"
        EXECUTOR_SECRET="${ROUTING_VALUE##*:}"
        DRY_RUN=false
        AGENT_MAX_TURNS='"$max_turns"'
        AGENT_MAX_TOKENS=1024
        AGENT_TOOL_TIMEOUT=30
        AGENT_API_TIMEOUT=30
        ANTHROPIC_API_URL="https://api.anthropic.com/v1/messages"
        OPENAI_API_URL="https://api.openai.com/v1/chat/completions"
        COMMANDS_DIR="'"$REPO_ROOT"'/.claude/commands"

        export ANTHROPIC_API_KEY="fake-anthropic-key"
        export OPENAI_API_KEY_CODEX="fake-codex-key"
        # No claude/codex CLI binary anywhere on PATH — only the fake curl and
        # the base system. Reproduces the exact runner shape from issue #254
        # (curl available, no assistant CLI installed).
        export PATH="'"$FAKE_BIN"':/usr/bin:/bin"
        export FAKE_CURL_STATE_DIR="'"$state_dir"'"
        export FAKE_CURL_MODE="'"$mode"'"

        invoke_executor_command "implement" "254"
    ' 2>&1
}

# ─── 1. Defect no longer reproduces: full success with no CLI on PATH ────────

echo "Checking a full multi-turn cycle succeeds for every executor tier with no CLI on PATH..."
for label in claude-code-haiku claude-code-sonnet claude-code-opus codex; do
    state_dir="$GLOBAL_TMP/state-$label"; mkdir -p "$state_dir"
    out=$(run_invoke "$label" two_turn 10 "$state_dir") && rc=0 || rc=$?
    if [[ $rc -eq 0 ]]; then
        assert "$label: invoke_executor_command succeeds end-to-end (no CLI installed)" 0
    else
        echo "      output: $out"
        assert "$label: invoke_executor_command succeeds end-to-end (no CLI installed)" 1
    fi
    if echo "$out" | grep -q -- "-done"; then
        assert "$label: final agent message reached stdout" 0
    else
        assert "$label: final agent message reached stdout" 1
    fi
    # Confirm the real .claude/commands/implement.md was used with $ARGUMENTS
    # substituted to the target issue number.
    if grep -qF "Implement issue #254" "$state_dir/req_1.json"; then
        assert "$label: task prompt is implement.md with \$ARGUMENTS substituted" 0
    else
        assert "$label: task prompt is implement.md with \$ARGUMENTS substituted" 1
    fi
    # Confirm the tool-use round trip actually happened (2 API calls: request + follow-up).
    call_count=$(< "$state_dir/counter")
    if [[ "$call_count" -eq 2 ]]; then
        assert "$label: exactly one tool-use round trip occurred (2 API calls)" 0
    else
        assert "$label: exactly one tool-use round trip occurred (2 API calls), got $call_count" 1
    fi
done

# ─── 2. Each tier resolves to its own distinct model ID ──────────────────────

echo ""
echo "Checking each executor tier sends its own distinct model ID..."
declare -A EXPECT_MODEL=(
    ["claude-code-haiku"]="claude-haiku-4-5-20251001"
    ["claude-code-sonnet"]="claude-sonnet-5"
    ["claude-code-opus"]="claude-opus-5"
    ["codex"]="gpt-5-codex"
)
for label in claude-code-haiku claude-code-sonnet claude-code-opus codex; do
    state_dir="$GLOBAL_TMP/state-$label"  # reuse from step 1
    got_model=$(jq -r '.model' "$state_dir/req_1.json")
    if [[ "$got_model" == "${EXPECT_MODEL[$label]}" ]]; then
        assert "$label: request used model ${EXPECT_MODEL[$label]}" 0
    else
        assert "$label: request used model ${EXPECT_MODEL[$label]}, got $got_model" 1
    fi
done

# ─── 3. Fail-closed: malformed response ───────────────────────────────────────

echo ""
echo "Checking a malformed API response fails closed for both providers..."
for label in claude-code-sonnet codex; do
    state_dir="$GLOBAL_TMP/state-malformed-$label"; mkdir -p "$state_dir"
    out=$(run_invoke "$label" malformed 10 "$state_dir") && rc=0 || rc=$?
    if [[ $rc -ne 0 ]] && echo "$out" | grep -qi "malformed"; then
        assert "$label: malformed response fails closed with a clear error" 0
    else
        echo "      exit=$rc output: $out"
        assert "$label: malformed response fails closed with a clear error" 1
    fi
done

# ─── 4. Fail-closed: non-2xx HTTP status ──────────────────────────────────────

echo ""
echo "Checking a non-2xx HTTP status fails closed for both providers..."
for label in claude-code-sonnet codex; do
    state_dir="$GLOBAL_TMP/state-httperr-$label"; mkdir -p "$state_dir"
    out=$(run_invoke "$label" http_error 10 "$state_dir") && rc=0 || rc=$?
    if [[ $rc -ne 0 ]] && echo "$out" | grep -qF "HTTP 403"; then
        assert "$label: HTTP 403 fails closed with the status code surfaced" 0
    else
        echo "      exit=$rc output: $out"
        assert "$label: HTTP 403 fails closed with the status code surfaced" 1
    fi
done

# ─── 5. Fail-closed: exceeds AGENT_MAX_TURNS ──────────────────────────────────
# Same fail-closed contract the old CLI-missing case had: the caller
# (invoke_executor_command's caller in the execution loop) gets a non-zero
# return and posts a mid-cycle Blocked Declaration — no work is silently lost.

echo ""
echo "Checking the agent loop fails closed after exceeding AGENT_MAX_TURNS..."
for label in claude-code-sonnet codex; do
    state_dir="$GLOBAL_TMP/state-maxturns-$label"; mkdir -p "$state_dir"
    out=$(run_invoke "$label" never_ends 3 "$state_dir") && rc=0 || rc=$?
    if [[ $rc -ne 0 ]] && echo "$out" | grep -qi "exceeded AGENT_MAX_TURNS"; then
        assert "$label: exceeding AGENT_MAX_TURNS fails closed with a clear error" 0
    else
        echo "      exit=$rc output: $out"
        assert "$label: exceeding AGENT_MAX_TURNS fails closed with a clear error" 1
    fi
    call_count=$(< "$state_dir/counter")
    if [[ "$call_count" -eq 3 ]]; then
        assert "$label: made exactly AGENT_MAX_TURNS API calls before giving up" 0
    else
        assert "$label: made exactly AGENT_MAX_TURNS API calls before giving up, got $call_count" 1
    fi
done

# ─── 6. --dry-run makes no HTTP call ──────────────────────────────────────────

echo ""
echo "Checking --dry-run makes no HTTP call..."
state_dir="$GLOBAL_TMP/state-dryrun"; mkdir -p "$state_dir"
out=$(bash -c '
    set -euo pipefail
    source "'"$TABLES_AND_FUNC"'"
    EXECUTOR_LABEL="claude-code-sonnet"
    ROUTING_VALUE="${EXECUTOR_ROUTING[$EXECUTOR_LABEL]}"
    EXECUTOR_SECRET="${ROUTING_VALUE##*:}"
    DRY_RUN=true
    AGENT_MAX_TURNS=10
    COMMANDS_DIR="'"$REPO_ROOT"'/.claude/commands"
    export ANTHROPIC_API_KEY="fake-anthropic-key"
    export PATH="'"$FAKE_BIN"':/usr/bin:/bin"
    export FAKE_CURL_STATE_DIR="'"$state_dir"'"
    export FAKE_CURL_MODE="two_turn"
    invoke_executor_command "implement" "254"
' 2>&1) && rc=0 || rc=$?
assert "dry-run: invoke_executor_command exits 0" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
if [[ -f "$state_dir/counter" ]]; then
    assert "dry-run: no HTTP call was made" 1
else
    assert "dry-run: no HTTP call was made" 0
fi
if echo "$out" | grep -qF "no CLI, no install step"; then
    assert "dry-run: log line documents no-CLI invocation" 0
else
    assert "dry-run: log line documents no-CLI invocation" 1
fi

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
