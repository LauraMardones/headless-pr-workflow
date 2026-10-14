#!/usr/bin/env bash
# tests/test_dispatcher_invoke_executor_secrets.sh
#
# Regression test for issue #252, updated for issues #254 and #314:
# dispatcher-invoke.sh once required a separate GitHub Secret per Claude
# executor tier, even though one credential serves every Claude model. Issue
# #254 replaced the CLI-shell-out path with direct API calls. Issue #314
# (ADR-012) then moved the Claude tiers to Claude Code on the PO's
# subscription token, so this test stubs `claude` (on CLAUDE_BIN) for the
# Claude tiers and `curl` for the Codex tier, and asserts the correct secret
# value reaches the correct executor.
#
# This test proves, against the real EXECUTOR_ROUTING/EXECUTOR_PROVIDER/
# EXECUTOR_MODEL tables and the real invoke_executor_command() implementation:
#
#   1. A single CLAUDE_CODE_OAUTH_TOKEN signs in all three Claude tiers
#      (haiku/sonnet/opus) — the cleaned value reaches Claude Code's
#      environment.
#   2. With CLAUDE_CODE_OAUTH_TOKEN unset, all three Claude tiers fail closed
#      with the same clear "Secret 'CLAUDE_CODE_OAUTH_TOKEN' is not set"
#      error, before Claude Code starts — and codex, authenticated by a
#      separate secret, is unaffected.
#   3. Static check: .github/workflows/dispatcher.yml actually wires both
#      CLAUDE_CODE_OAUTH_TOKEN and OPENAI_API_KEY_CODEX into the "Run
#      dispatcher invoke" step's env: — a shell-only test cannot catch a
#      workflow-YAML wiring omission, which is exactly how the original bug
#      (the secret never reaching the script at all) went undetected until a
#      live run.
#   4. Static check: the old EXECUTOR_CLI table and its
#      `--dangerously-skip-permissions` shell-out (issue #254's predecessor
#      defect) are gone.
#
# Usage: bash tests/test_dispatcher_invoke_executor_secrets.sh
# Requires: bash 4+, jq, timeout

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INVOKE_SCRIPT="$REPO_ROOT/scripts/dispatcher-invoke.sh"
WORKFLOW_FILE="$REPO_ROOT/.github/workflows/dispatcher.yml"

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

# ─── Static check 1: EXECUTOR_ROUTING table content ───────────────────────────

ROUTING_BLOCK=$(awk '/^declare -A EXECUTOR_ROUTING=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT")

echo "Checking EXECUTOR_ROUTING table..."
for row in '["claude-code-haiku"]="Claude Haiku:CLAUDE_CODE_OAUTH_TOKEN"' \
           '["claude-code-sonnet"]="Claude Sonnet:CLAUDE_CODE_OAUTH_TOKEN"' \
           '["claude-code-opus"]="Claude Opus:CLAUDE_CODE_OAUTH_TOKEN"'; do
    if echo "$ROUTING_BLOCK" | grep -qF "$row"; then
        assert "EXECUTOR_ROUTING: $row" 0
    else
        assert "EXECUTOR_ROUTING: $row" 1
    fi
done
if echo "$ROUTING_BLOCK" | grep -qF '["codex"]="Codex:OPENAI_API_KEY_CODEX"'; then
    assert "EXECUTOR_ROUTING: codex keeps its own separate OPENAI_API_KEY_CODEX secret" 0
else
    assert "EXECUTOR_ROUTING: codex keeps its own separate OPENAI_API_KEY_CODEX secret" 1
fi

# ─── Static check 1b: review-path tables (issues #263 and #293) ──────────────
# Lookup-table test, not a conditional-branch test, per the "data-driven, not
# hardcoded" convention already used for EXECUTOR_ROUTING above. The Claude
# tiers are reviewed through the Codex review loop (ADR-006), so they have no
# paired /review session; only codex has one.

REVIEW_ROUTING_BLOCK=$(awk '/^declare -A REVIEW_EXECUTOR_ROUTING=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT")
LOOP_ROUTING_BLOCK=$(awk '/^declare -A CODEX_REVIEW_LOOP_ROUTING=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT")

echo ""
echo "Checking the review-path tables (paired session and Codex review loop)..."
if [[ -z "$REVIEW_ROUTING_BLOCK" ]]; then
    echo "FAIL: could not extract REVIEW_EXECUTOR_ROUTING from $INVOKE_SCRIPT (table renamed or removed?)"
    exit 1
fi
if [[ -z "$LOOP_ROUTING_BLOCK" ]]; then
    echo "FAIL: could not extract CODEX_REVIEW_LOOP_ROUTING from $INVOKE_SCRIPT (table renamed or removed?)"
    exit 1
fi
if echo "$REVIEW_ROUTING_BLOCK" | grep -qF '["codex"]="claude-code-opus"'; then
    assert "REVIEW_EXECUTOR_ROUTING: codex implemented -> claude-code-opus reviews" 0
else
    assert "REVIEW_EXECUTOR_ROUTING: codex implemented -> claude-code-opus reviews" 1
fi
for impl_label in claude-code-haiku claude-code-sonnet claude-code-opus; do
    if echo "$REVIEW_ROUTING_BLOCK" | grep -qF "[\"$impl_label\"]="; then
        assert "REVIEW_EXECUTOR_ROUTING: $impl_label has no paired /review session" 1
    else
        assert "REVIEW_EXECUTOR_ROUTING: $impl_label has no paired /review session" 0
    fi
    if echo "$LOOP_ROUTING_BLOCK" | grep -qF "[\"$impl_label\"]="; then
        assert "CODEX_REVIEW_LOOP_ROUTING: $impl_label uses the Codex review loop" 0
    else
        assert "CODEX_REVIEW_LOOP_ROUTING: $impl_label uses the Codex review loop" 1
    fi
done
if echo "$LOOP_ROUTING_BLOCK" | grep -qF '["codex"]='; then
    assert "CODEX_REVIEW_LOOP_ROUTING: codex is not reviewed through its own review loop" 1
else
    assert "CODEX_REVIEW_LOOP_ROUTING: codex is not reviewed through its own review loop" 0
fi
if echo "$REVIEW_ROUTING_BLOCK" | grep -qF '["codex"]="codex"'; then
    assert "REVIEW_EXECUTOR_ROUTING: codex is not paired to review itself" 1
else
    assert "REVIEW_EXECUTOR_ROUTING: codex is not paired to review itself" 0
fi

# ─── Static check 2: workflow YAML actually wires both secrets ────────────────
# A shell-only test cannot catch this class of bug — the script's own logic
# can be perfectly correct while the workflow simply never hands it the
# secret. This is the check that would have caught issue #252's second,
# more fundamental defect.

echo ""
echo "Checking .github/workflows/dispatcher.yml wiring..."
INVOKE_STEP=$(awk '/name: Run dispatcher invoke/{p=1} p{print} p && /run: \|/{exit}' "$WORKFLOW_FILE")

if echo "$INVOKE_STEP" | grep -qF 'CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'; then
    assert "dispatcher.yml: 'Run dispatcher invoke' step wires secrets.CLAUDE_CODE_OAUTH_TOKEN" 0
else
    assert "dispatcher.yml: 'Run dispatcher invoke' step wires secrets.CLAUDE_CODE_OAUTH_TOKEN" 1
fi
if echo "$INVOKE_STEP" | grep -qF 'OPENAI_API_KEY_CODEX: ${{ secrets.OPENAI_API_KEY_CODEX }}'; then
    assert "dispatcher.yml: 'Run dispatcher invoke' step wires secrets.OPENAI_API_KEY_CODEX" 0
else
    assert "dispatcher.yml: 'Run dispatcher invoke' step wires secrets.OPENAI_API_KEY_CODEX" 1
fi

# ─── Static check 3: no CLI shell-out path remains (issue #254) ──────────────

echo ""
echo "Checking the old CLI shell-out path stays removed..."
if grep -qF 'EXECUTOR_CLI' "$INVOKE_SCRIPT"; then
    assert "scripts/dispatcher-invoke.sh: EXECUTOR_CLI table removed" 1
else
    assert "scripts/dispatcher-invoke.sh: EXECUTOR_CLI table removed" 0
fi
if grep -qF -- '--dangerously-skip-permissions' "$INVOKE_SCRIPT"; then
    assert "scripts/dispatcher-invoke.sh: no --dangerously-skip-permissions shell-out" 1
else
    assert "scripts/dispatcher-invoke.sh: no --dangerously-skip-permissions shell-out" 0
fi
if grep -qF 'declare -A EXECUTOR_PROVIDER=' "$INVOKE_SCRIPT" && grep -qF 'declare -A EXECUTOR_MODEL=' "$INVOKE_SCRIPT"; then
    assert "scripts/dispatcher-invoke.sh: EXECUTOR_PROVIDER and EXECUTOR_MODEL tables present" 0
else
    assert "scripts/dispatcher-invoke.sh: EXECUTOR_PROVIDER and EXECUTOR_MODEL tables present" 1
fi

# ─── Behavioral check: extract the real tables + session functions ───────────

TABLES_AND_FUNC="$GLOBAL_TMP/extracted.sh"
{
    echo "source \"$REPO_ROOT/scripts/lib/claude-session.sh\""
    awk '/^declare -A EXECUTOR_ROUTING=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^declare -A EXECUTOR_PROVIDER=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^declare -A EXECUTOR_MODEL=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^CLAUDE_SESSION_ARGS=\(/{p=1} p{print} p && /\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^_curl_json\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^_run_agent_bash_tool\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^run_claude_code_session\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^_claude_code_session\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^run_openai_agent\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    awk '/^invoke_executor_command\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
    # The subscription allowance check (issue #317) is covered by
    # tests/test_dispatcher_allowance*.py; this test isolates the start path.
    echo 'check_subscription_allowance() { :; }'
    echo 'check_usage_limit() { :; }'
    echo 'record_session_start() { :; }'
} > "$TABLES_AND_FUNC"

for fn in invoke_executor_command run_claude_code_session _claude_code_session run_openai_agent; do
    if ! grep -q "^${fn}() {" "$TABLES_AND_FUNC"; then
        echo "FAIL: could not extract ${fn}() from $INVOKE_SCRIPT (function renamed or removed?)"
        exit 1
    fi
done

# Fake executors: never touch the network. Each logs the credential it
# received and reports a single-turn "task complete", so invoke_executor_command()
# returns success after exactly one call.
FAKE_BIN="$GLOBAL_TMP/bin"
mkdir -p "$FAKE_BIN"
CALL_LOG="$GLOBAL_TMP/calls.log"
: > "$CALL_LOG"

cat > "$FAKE_BIN/curl" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
outfile="" url="" auth_header=""
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
    case "${args[$i]}" in
        -o) i=$((i+1)); outfile="${args[$i]}" ;;
        -H)
            i=$((i+1))
            case "${args[$i]}" in
                Authorization:*) auth_header="${args[$i]}" ;;
            esac
            ;;
        http*://*) url="${args[$i]}" ;;
    esac
    i=$((i+1))
done
echo "curl url=$url auth=$auth_header" >> "$MOCK_CALL_LOG"
printf '%s' '{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"done"}}]}' > "$outfile"
echo -n "200"
EOF
chmod +x "$FAKE_BIN/curl"

cat > "$FAKE_BIN/claude" << 'EOF'
#!/usr/bin/env bash
cat > /dev/null
model=""
while [[ $# -gt 0 ]]; do
    [[ "$1" == "--model" ]] && model="$2"
    shift
done
echo "claude model=$model token=${CLAUDE_CODE_OAUTH_TOKEN:-<unset>}" >> "$MOCK_CALL_LOG"
echo '{"type":"system","subtype":"init","apiKeySource":"none"}'
echo '{"type":"result","subtype":"success","is_error":false,"num_turns":1,"duration_ms":1000,"result":"done","usage":{"input_tokens":1,"output_tokens":1}}'
EOF
chmod +x "$FAKE_BIN/claude"

# $1=story's own EXECUTOR_LABEL  $2=CLAUDE_CODE_OAUTH_TOKEN value or "" to unset
# $3=OPENAI_API_KEY_CODEX value or "" to unset  $4=command (default implement)
# $5=override executor label passed as invoke_executor_command's 3rd arg (optional)
run_invoke() {
    local label="$1" claude_token="$2" codex_key="$3" command="${4:-implement}" override="${5:-}"
    bash -c '
        set -euo pipefail
        source "'"$TABLES_AND_FUNC"'"

        EXECUTOR_LABEL="'"$label"'"
        DRY_RUN=false
        AGENT_MAX_TURNS=5
        AGENT_MAX_TOKENS=1024
        AGENT_TOOL_TIMEOUT=30
        AGENT_API_TIMEOUT=30
        AGENT_MAX_WALLCLOCK_SECONDS=999999
        DISPATCH_JOB_START_TS=$(date +%s)
        OPENAI_API_URL="https://api.openai.com/v1/chat/completions"
        REPO_ROOT="'"$REPO_ROOT"'"
        COMMANDS_DIR="'"$REPO_ROOT"'/.claude/commands"
        CLAUDE_BIN="'"$FAKE_BIN"'/claude"

        if [[ -n "'"$claude_token"'" ]]; then export CLAUDE_CODE_OAUTH_TOKEN="'"$claude_token"'"; else unset CLAUDE_CODE_OAUTH_TOKEN 2>/dev/null || true; fi
        if [[ -n "'"$codex_key"'" ]]; then export OPENAI_API_KEY_CODEX="'"$codex_key"'"; else unset OPENAI_API_KEY_CODEX 2>/dev/null || true; fi
        export PATH="'"$FAKE_BIN"':$PATH"
        export MOCK_CALL_LOG="'"$CALL_LOG"'"

        if [[ -n "'"$override"'" ]]; then
            invoke_executor_command "'"$command"'" "999" "'"$override"'"
        else
            invoke_executor_command "'"$command"'" "999"
        fi
    ' 2>&1
}

echo ""
echo "Checking a single CLAUDE_CODE_OAUTH_TOKEN signs in all three Claude tiers..."
: > "$CALL_LOG"
for label in claude-code-haiku claude-code-sonnet claude-code-opus; do
    out=$(run_invoke "$label" "shared-subscription-token" "codex-key") && rc=0 || rc=$?
    if [[ $rc -eq 0 ]]; then
        assert "$label: invoke_executor_command succeeds with only CLAUDE_CODE_OAUTH_TOKEN set" 0
    else
        echo "      output: $out"
        assert "$label: invoke_executor_command succeeds with only CLAUDE_CODE_OAUTH_TOKEN set" 1
    fi
done
if grep -c 'token=shared-subscription-token' "$CALL_LOG" | grep -qx 3; then
    assert "Claude Code received the shared subscription token all 3 times" 0
else
    assert "Claude Code received the shared subscription token all 3 times" 1
fi
for model in haiku sonnet opus; do
    if grep -qF "claude model=$model " "$CALL_LOG"; then
        assert "Claude Code started with the $model alias" 0
    else
        assert "Claude Code started with the $model alias" 1
    fi
done

echo ""
echo "Checking codex is authenticated by its own separate secret, unaffected..."
: > "$CALL_LOG"
out=$(run_invoke "codex" "shared-subscription-token" "codex-key") && rc=0 || rc=$?
assert "codex: invoke_executor_command succeeds via OPENAI_API_KEY_CODEX" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
if grep -qF 'Authorization: Bearer codex-key' "$CALL_LOG"; then
    assert "codex: the OpenAI API received the OPENAI_API_KEY_CODEX secret's value as a Bearer token" 0
else
    assert "codex: the OpenAI API received the OPENAI_API_KEY_CODEX secret's value as a Bearer token" 1
fi

echo ""
echo "Checking all three Claude tiers fail closed the same way when CLAUDE_CODE_OAUTH_TOKEN is unset..."
for label in claude-code-haiku claude-code-sonnet claude-code-opus; do
    : > "$CALL_LOG"
    out=$(run_invoke "$label" "" "codex-key") && rc=0 || rc=$?
    if [[ $rc -ne 0 ]] && echo "$out" | grep -qF "Secret 'CLAUDE_CODE_OAUTH_TOKEN' is not set in the environment."; then
        assert "$label: fails closed with the CLAUDE_CODE_OAUTH_TOKEN-not-set error" 0
    else
        echo "      exit=$rc output: $out"
        assert "$label: fails closed with the CLAUDE_CODE_OAUTH_TOKEN-not-set error" 1
    fi
    if [[ -s "$CALL_LOG" ]]; then
        assert "$label: Claude Code not started when the secret is missing" 1
    else
        assert "$label: Claude Code not started when the secret is missing" 0
    fi
done

echo ""
echo "Checking codex still succeeds when CLAUDE_CODE_OAUTH_TOKEN is unset (secrets are decoupled)..."
out=$(run_invoke "codex" "" "codex-key") && rc=0 || rc=$?
assert "codex: unaffected by CLAUDE_CODE_OAUTH_TOKEN being unset" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"

# ─── Behavioral check: review-pairing override reaches the right credential ──
# Issue #263: invoke_executor_command()'s optional 3rd argument lets a caller
# (Step E2 in the real script) invoke /review under a *different* executor
# label than the story's own $EXECUTOR_LABEL. Proves the override actually
# changes which secret/provider is used — not just that the label is accepted.

echo ""
echo "Checking /review under the cross-provider pairing override reaches the paired provider's credential, not the implementing story's..."

: > "$CALL_LOG"
out=$(run_invoke "claude-code-sonnet" "subscription-token-unused-for-review" "codex-review-key" review codex) && rc=0 || rc=$?
assert "story implemented by claude-code-sonnet, /review overridden to codex: succeeds" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
if grep -qF 'Authorization: Bearer codex-review-key' "$CALL_LOG" && ! grep -q '^claude ' "$CALL_LOG"; then
    assert "claude-code-sonnet implemented / codex reviews: OpenAI API received OPENAI_API_KEY_CODEX; Claude Code not started" 0
else
    assert "claude-code-sonnet implemented / codex reviews: OpenAI API received OPENAI_API_KEY_CODEX; Claude Code not started" 1
fi

: > "$CALL_LOG"
out=$(run_invoke "codex" "opus-review-token" "codex-key-unused-for-review" review claude-code-opus) && rc=0 || rc=$?
assert "story implemented by codex, /review overridden to claude-code-opus: succeeds" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
if grep -qF 'claude model=opus token=opus-review-token' "$CALL_LOG"; then
    assert "codex implemented / opus reviews: Claude Code (opus) received CLAUDE_CODE_OAUTH_TOKEN" 0
else
    assert "codex implemented / opus reviews: Claude Code (opus) received CLAUDE_CODE_OAUTH_TOKEN" 1
fi
if grep -qF 'codex-key-unused-for-review' "$CALL_LOG"; then
    assert "codex implemented / opus reviews: the implementing tier's Codex key was NOT used for review" 1
else
    assert "codex implemented / opus reviews: the implementing tier's Codex key was NOT used for review" 0
fi

echo ""
echo "Checking invoke_executor_command's default (no override) behavior is unaffected — /implement, /merge, /cleanup call sites all still resolve via the story's own EXECUTOR_LABEL..."
: > "$CALL_LOG"
out=$(run_invoke "claude-code-opus" "default-behavior-token" "codex-key") && rc=0 || rc=$?
assert "no override: invoke_executor_command still succeeds via the story's own EXECUTOR_LABEL" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
if grep -qF 'claude model=opus token=default-behavior-token' "$CALL_LOG"; then
    assert "no override: the story's own tier and the subscription token are used, exactly as before issue #263" 0
else
    assert "no override: the story's own tier and the subscription token are used, exactly as before issue #263" 1
fi

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
