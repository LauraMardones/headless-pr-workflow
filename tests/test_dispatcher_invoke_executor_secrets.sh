#!/usr/bin/env bash
# tests/test_dispatcher_invoke_executor_secrets.sh
#
# Regression test for issue #252: dispatcher-invoke.sh previously required a
# separate GitHub Secret per Claude executor tier (ANTHROPIC_API_KEY_HAIKU,
# _SONNET, _OPUS), even though a single Anthropic API key authenticates any
# Claude model. This test proves, against the real EXECUTOR_ROUTING table and
# the real invoke_executor_command() implementation:
#
#   1. A single ANTHROPIC_API_KEY authenticates all three Claude tiers
#      (haiku/sonnet/opus), and the correct value reaches the CLI under the
#      correct env var name.
#   2. With ANTHROPIC_API_KEY unset, all three Claude tiers fail closed with
#      the same clear "Secret 'ANTHROPIC_API_KEY' is not set" error — and
#      codex, authenticated by a separate secret, is unaffected.
#   3. Static check: .github/workflows/dispatcher.yml actually wires both
#      ANTHROPIC_API_KEY and OPENAI_API_KEY_CODEX into the "Run dispatcher
#      invoke" step's env: — a shell-only test cannot catch a workflow-YAML
#      wiring omission, which is exactly how the original bug (the secret
#      never reaching the script at all) went undetected until a live run.
#
# Usage: bash tests/test_dispatcher_invoke_executor_secrets.sh
# Requires: bash 4+

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
if echo "$ROUTING_BLOCK" | grep -qF '["claude-code-haiku"]="Claude Haiku:ANTHROPIC_API_KEY"'; then
    assert "EXECUTOR_ROUTING: claude-code-haiku maps to shared ANTHROPIC_API_KEY" 0
else
    assert "EXECUTOR_ROUTING: claude-code-haiku maps to shared ANTHROPIC_API_KEY" 1
fi
if echo "$ROUTING_BLOCK" | grep -qF '["claude-code-sonnet"]="Claude Sonnet:ANTHROPIC_API_KEY"'; then
    assert "EXECUTOR_ROUTING: claude-code-sonnet maps to shared ANTHROPIC_API_KEY" 0
else
    assert "EXECUTOR_ROUTING: claude-code-sonnet maps to shared ANTHROPIC_API_KEY" 1
fi
if echo "$ROUTING_BLOCK" | grep -qF '["claude-code-opus"]="Claude Opus:ANTHROPIC_API_KEY"'; then
    assert "EXECUTOR_ROUTING: claude-code-opus maps to shared ANTHROPIC_API_KEY" 0
else
    assert "EXECUTOR_ROUTING: claude-code-opus maps to shared ANTHROPIC_API_KEY" 1
fi
if echo "$ROUTING_BLOCK" | grep -qF '["codex"]="Codex:OPENAI_API_KEY_CODEX"'; then
    assert "EXECUTOR_ROUTING: codex keeps its own separate OPENAI_API_KEY_CODEX secret" 0
else
    assert "EXECUTOR_ROUTING: codex keeps its own separate OPENAI_API_KEY_CODEX secret" 1
fi
if echo "$ROUTING_BLOCK" | grep -qE 'ANTHROPIC_API_KEY_(HAIKU|SONNET|OPUS)'; then
    assert "EXECUTOR_ROUTING: no stale per-tier ANTHROPIC_API_KEY_* names remain" 1
else
    assert "EXECUTOR_ROUTING: no stale per-tier ANTHROPIC_API_KEY_* names remain" 0
fi

# ─── Static check 2: workflow YAML actually wires both secrets ────────────────
# A shell-only test cannot catch this class of bug — the script's own logic
# can be perfectly correct while the workflow simply never hands it the
# secret. This is the check that would have caught issue #252's second,
# more fundamental defect.

echo ""
echo "Checking .github/workflows/dispatcher.yml wiring..."
INVOKE_STEP=$(awk '/name: Run dispatcher invoke/{p=1} p{print} p && /run: \|/{exit}' "$WORKFLOW_FILE")

if echo "$INVOKE_STEP" | grep -qF 'ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}'; then
    assert "dispatcher.yml: 'Run dispatcher invoke' step wires secrets.ANTHROPIC_API_KEY" 0
else
    assert "dispatcher.yml: 'Run dispatcher invoke' step wires secrets.ANTHROPIC_API_KEY" 1
fi
if echo "$INVOKE_STEP" | grep -qF 'OPENAI_API_KEY_CODEX: ${{ secrets.OPENAI_API_KEY_CODEX }}'; then
    assert "dispatcher.yml: 'Run dispatcher invoke' step wires secrets.OPENAI_API_KEY_CODEX" 0
else
    assert "dispatcher.yml: 'Run dispatcher invoke' step wires secrets.OPENAI_API_KEY_CODEX" 1
fi

# ─── Behavioral check: extract the real tables + invoke_executor_command() ────

TABLES_AND_FUNC="$GLOBAL_TMP/extracted.sh"
{
    awk '/^declare -A EXECUTOR_ROUTING=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^declare -A EXECUTOR_CLI=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^declare -A EXECUTOR_API_KEY_ENV=\($/{p=1} p{print} p && /^\)$/{exit}' "$INVOKE_SCRIPT"
    awk '/^invoke_executor_command\(\) \{$/{p=1} p{print} p && /^}$/{exit}' "$INVOKE_SCRIPT"
} > "$TABLES_AND_FUNC"

if ! grep -q "^invoke_executor_command() {" "$TABLES_AND_FUNC"; then
    echo "FAIL: could not extract invoke_executor_command() from $INVOKE_SCRIPT (function renamed or removed?)"
    exit 1
fi

FAKE_BIN="$GLOBAL_TMP/bin"
mkdir -p "$FAKE_BIN"
CALL_LOG="$GLOBAL_TMP/cli-calls.log"
: > "$CALL_LOG"

cat > "$FAKE_BIN/claude" << 'EOF'
#!/usr/bin/env bash
echo "claude called: ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-<unset>}" >> "$MOCK_CALL_LOG"
exit 0
EOF
cat > "$FAKE_BIN/codex" << 'EOF'
#!/usr/bin/env bash
echo "codex called: OPENAI_API_KEY=${OPENAI_API_KEY:-<unset>}" >> "$MOCK_CALL_LOG"
exit 0
EOF
chmod +x "$FAKE_BIN/claude" "$FAKE_BIN/codex"

run_invoke() {
    # $1=executor label  $2=ANTHROPIC_API_KEY value or "" to unset  $3=OPENAI_API_KEY_CODEX value or "" to unset
    local label="$1" anthropic_key="$2" codex_key="$3"
    bash -c '
        set -euo pipefail
        source "'"$TABLES_AND_FUNC"'"

        EXECUTOR_LABEL="'"$label"'"
        ROUTING_VALUE="${EXECUTOR_ROUTING[$EXECUTOR_LABEL]}"
        EXECUTOR_SECRET="${ROUTING_VALUE##*:}"
        DRY_RUN=false

        if [[ -n "'"$anthropic_key"'" ]]; then export ANTHROPIC_API_KEY="'"$anthropic_key"'"; else unset ANTHROPIC_API_KEY 2>/dev/null || true; fi
        if [[ -n "'"$codex_key"'" ]]; then export OPENAI_API_KEY_CODEX="'"$codex_key"'"; else unset OPENAI_API_KEY_CODEX 2>/dev/null || true; fi
        export PATH="'"$FAKE_BIN"':$PATH"
        export MOCK_CALL_LOG="'"$CALL_LOG"'"

        invoke_executor_command "implement" "999"
    ' 2>&1
}

echo ""
echo "Checking a single ANTHROPIC_API_KEY authenticates all three Claude tiers..."
: > "$CALL_LOG"
for label in claude-code-haiku claude-code-sonnet claude-code-opus; do
    out=$(run_invoke "$label" "shared-anthropic-key" "codex-key")
    rc=$?
    if [[ $rc -eq 0 ]]; then
        assert "$label: invoke_executor_command succeeds with only a shared ANTHROPIC_API_KEY set" 0
    else
        echo "      output: $out"
        assert "$label: invoke_executor_command succeeds with only a shared ANTHROPIC_API_KEY set" 1
    fi
done
if grep -c 'claude called: ANTHROPIC_API_KEY=shared-anthropic-key' "$CALL_LOG" | grep -qx 3; then
    assert "the CLI received the correct shared key value all 3 times" 0
else
    assert "the CLI received the correct shared key value all 3 times" 1
fi

echo ""
echo "Checking codex is authenticated by its own separate secret, unaffected..."
: > "$CALL_LOG"
out=$(run_invoke "codex" "shared-anthropic-key" "codex-key")
rc=$?
assert "codex: invoke_executor_command succeeds via OPENAI_API_KEY_CODEX" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
if grep -qF 'codex called: OPENAI_API_KEY=codex-key' "$CALL_LOG"; then
    assert "codex: the CLI received the OPENAI_API_KEY_CODEX secret's value under OPENAI_API_KEY" 0
else
    assert "codex: the CLI received the OPENAI_API_KEY_CODEX secret's value under OPENAI_API_KEY" 1
fi

echo ""
echo "Checking all three Claude tiers fail closed the same way when ANTHROPIC_API_KEY is unset..."
for label in claude-code-haiku claude-code-sonnet claude-code-opus; do
    out=$(run_invoke "$label" "" "codex-key") && rc=0 || rc=$?
    if [[ $rc -ne 0 ]] && echo "$out" | grep -qF "Secret 'ANTHROPIC_API_KEY' is not set in the environment."; then
        assert "$label: fails closed with the ANTHROPIC_API_KEY-not-set error" 0
    else
        echo "      exit=$rc output: $out"
        assert "$label: fails closed with the ANTHROPIC_API_KEY-not-set error" 1
    fi
done

echo ""
echo "Checking codex still succeeds when ANTHROPIC_API_KEY is unset (secrets are decoupled)..."
out=$(run_invoke "codex" "" "codex-key") && rc=0 || rc=$?
assert "codex: unaffected by ANTHROPIC_API_KEY being unset" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
