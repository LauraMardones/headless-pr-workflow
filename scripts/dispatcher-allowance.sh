#!/usr/bin/env bash
# scripts/dispatcher-allowance.sh
#
# Subscription allowance check run by the dispatcher before every model
# session start (ADR-012, issue #317). One Claude Code turn on Haiku, signed in
# with the PO's subscription token, reads how much of the five-hour and weekly
# allowance is left from the `rate_limit_event` line of its stream-json output.
# A session may start only when both windows are at or above the reserve the
# PO has set in GitHub Settings. When the allowance cannot be read, nothing
# starts (fail closed).
#
# Usage:
#   bash scripts/dispatcher-allowance.sh        (no arguments)
#
# Environment:
#   CLAUDE_CODE_OAUTH_TOKEN    required; every space, tab, CR and LF is removed
#   RESERVE_FIVE_HOUR_PERCENT  optional, whole number 0-100, default 10 (empty = unset)
#   RESERVE_WEEKLY_PERCENT     optional, whole number 0-100, default 20 (empty = unset)
#   ALLOWANCE_STATE_DIR        optional, default <repo-root>/.dispatcher-allowance
#   ALLOWANCE_PROBE_TIMEOUT    optional, seconds, default 120
#   CLAUDE_BIN                 optional, default `claude` (test hook)
#
# Exit codes (decision):
#   0  start   — both windows at or above the reserve
#   1  refuse  — a window below the reserve, a usage limit reached, or an active hold
#   2  error   — invalid setting, token missing or rejected, probe failed or
#                timed out, field missing, apiKeySource not "none", or overage
#                not "rejected"
#
# Output:
#   stdout — exactly one JSON object in every exit path (see docs/DISPATCHER-CONFIG.md
#            → "Subscription allowance and reserve" for the contract)
#   stderr — the token's presence and length when a probe runs (never its
#            value), then one decision line: [RESERVE], [RESERVE SKIP],
#            [RESERVE HOLD] or [ALLOWANCE ERROR]
#
# The interface is provider-neutral (start / refuse / error), per the
# docs/PROJECT-STATUS.md invariant that capacity is an abstract scheduling input.
#
# Requires: bash 4+, jq, timeout (coreutils)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

STATE_DIR="${ALLOWANCE_STATE_DIR:-$REPO_ROOT/.dispatcher-allowance}"
HOLD_FILE="$STATE_DIR/hold.json"
PROBE_TIMEOUT="${ALLOWANCE_PROBE_TIMEOUT:-120}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"

PROBE_PROMPT="Reply with the single word OK."
# Exact probe flags, verified on the pinned Claude Code version (see
# docs/DISPATCHER-CONFIG.md). Never add --bare: bare mode ignores the OAuth token.
PROBE_ARGS=(-p "$PROBE_PROMPT" --model haiku --tools "" --strict-mcp-config
            --no-session-persistence --output-format stream-json --verbose)

# Tolerance for comparing (1 - utilization) * 100 with a whole-number reserve:
# 0.9 is not exact in binary, and "exactly at the reserve" must be a start.
EPSILON=0.000001

# ─── Result state (null until read) ──────────────────────────────────────────

RES_FIVE="null"      # reserve values; numbers once validated
RES_WEEK="null"
FIVE_UTIL="null"; FIVE_RESETS="null"
WEEK_UTIL="null"; WEEK_RESETS="null"
API_KEY_SOURCE="null"
OVERAGE_STATUS="null"

# emit <decision> <source> <reason> <exit-code> <stderr-line>
emit() {
    local decision="$1" source="$2" reason="$3" rc="$4" line="$5"
    jq -cn \
        --arg decision "$decision" --arg source "$source" --arg reason "$reason" \
        --argjson rf "$RES_FIVE" --argjson rw "$RES_WEEK" \
        --argjson fu "$FIVE_UTIL" --argjson fr "$FIVE_RESETS" \
        --argjson wu "$WEEK_UTIL" --argjson wr "$WEEK_RESETS" \
        --argjson aks "$API_KEY_SOURCE" --argjson ovs "$OVERAGE_STATUS" \
        --argjson eps "$EPSILON" '
        def win($u; $r; $t):
            { utilization: $u,
              remaining_percent: (if $u == null then null
                                  else ([((1 - $u) * 100 + $eps | floor), 0] | max) end),
              reserve_percent: $r,
              resets_at: (if $t == null then null else ($t | floor | todate) end) };
        { decision: $decision, source: $source, reason: $reason,
          five_hour: win($fu; $rf; $fr),
          weekly:    win($wu; $rw; $wr),
          api_key_source: $aks,
          overage_status: $ovs }'
    echo "$line" >&2
    exit "$rc"
}

fail() {  # fail <reason>
    emit "error" "${2:-none}" "$1" 2 "[ALLOWANCE ERROR] $1 — starting nothing"
}

# ─── 1. Reserve settings (AC1, AC2) ──────────────────────────────────────────

read_reserve() {  # read_reserve <var-name> <default>
    local name="$1" default="$2" value
    value="${!name:-}"
    if [[ -z "$value" ]]; then
        echo "$default"
        return 0
    fi
    if [[ ! "$value" =~ ^[0-9]+$ ]] || (( 10#$value > 100 )); then
        return 1
    fi
    echo $(( 10#$value ))
}

if ! RES_FIVE=$(read_reserve RESERVE_FIVE_HOUR_PERCENT 10); then
    RES_FIVE="null"
    fail "RESERVE_FIVE_HOUR_PERCENT must be a whole number from 0 to 100, got '${RESERVE_FIVE_HOUR_PERCENT}'"
fi
if ! RES_WEEK=$(read_reserve RESERVE_WEEKLY_PERCENT 20); then
    RES_WEEK="null"
    fail "RESERVE_WEEKLY_PERCENT must be a whole number from 0 to 100, got '${RESERVE_WEEKLY_PERCENT}'"
fi

if [[ ! "$PROBE_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    fail "ALLOWANCE_PROBE_TIMEOUT must be a positive whole number of seconds, got '${PROBE_TIMEOUT}'"
fi

# ─── 2. Token (AC7, AC11, AC21) ──────────────────────────────────────────────

CLEAN_TOKEN=$(printf '%s' "${CLAUDE_CODE_OAUTH_TOKEN:-}" | tr -d ' \t\r\n')
if [[ -z "$CLEAN_TOKEN" ]]; then
    fail "CLAUDE_CODE_OAUTH_TOKEN is missing or empty after whitespace removal"
fi

# ─── 3. Hold until reset (AC12, AC13) ────────────────────────────────────────
# A missing, unreadable or malformed hold file is treated as no hold: the
# script probes. A bad hold file can therefore only cause a probe, never a start.

NOW=$(date -u +%s)
if [[ -r "$HOLD_FILE" ]]; then
    HOLD=$(jq -cse --argjson rf "$RES_FIVE" --argjson rw "$RES_WEEK" --argjson now "$NOW" '
        if length == 1 then .[0] else empty end
        | select(type == "object"
               and (.until | type) == "number"
               and .reserve_five_hour_percent == $rf
               and .reserve_weekly_percent == $rw
               and $now < .until)' "$HOLD_FILE" 2>/dev/null || true)
    if [[ -n "$HOLD" ]]; then
        FIVE_UTIL=$(jq -c '.five_hour.utilization | if type == "number" then . else null end' <<< "$HOLD")
        FIVE_RESETS=$(jq -c '.five_hour.resets_at | if type == "number" then . else null end' <<< "$HOLD")
        WEEK_UTIL=$(jq -c '.weekly.utilization | if type == "number" then . else null end' <<< "$HOLD")
        WEEK_RESETS=$(jq -c '.weekly.resets_at | if type == "number" then . else null end' <<< "$HOLD")
        UNTIL_TXT=$(jq -r '.until | floor | strftime("%Y-%m-%dT%H:%MZ")' <<< "$HOLD")
        emit "refuse" "hold" "below reserve until $(jq -r '.until | floor | todate' <<< "$HOLD"); not probing" 1 \
            "[RESERVE HOLD] below reserve until $UNTIL_TXT; not probing"
    fi
fi

# ─── 4. Probe (AC4, AC5) ─────────────────────────────────────────────────────

echo "[ALLOWANCE] token present, length ${#CLEAN_TOKEN} after whitespace removal; probing" >&2

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
PROBE_RC=0
# Run from an empty directory so no project instructions are loaded into the
# probe, with every metered credential removed from its environment.
( cd "$WORK" && env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
      CLAUDE_CODE_OAUTH_TOKEN="$CLEAN_TOKEN" \
      timeout "$PROBE_TIMEOUT" "$CLAUDE_BIN" "${PROBE_ARGS[@]}" \
      > "$WORK/stream.jsonl" 2> "$WORK/stderr.txt" < /dev/null ) || PROBE_RC=$?

if [[ $PROBE_RC -eq 124 ]]; then
    fail "probe timed out after ${PROBE_TIMEOUT}s" probe
fi

# Parse line by line, ignoring non-JSON lines and line order.
EVENTS=$(jq -cR 'fromjson? // empty | select(type == "object")' "$WORK/stream.jsonl" 2>/dev/null || true)
INIT=$(jq -c 'select(.type == "system" and .subtype == "init")' <<< "$EVENTS" | tail -1)
RESULT=$(jq -c 'select(.type == "result")' <<< "$EVENTS" | tail -1)
RLE=$(jq -c 'select(.type == "rate_limit_event")' <<< "$EVENTS" | tail -1)

# 401: the token was rejected (AC8). Look at the result message and stderr only.
RESULT_TEXT=$(jq -r '[.result, .error] | map(strings) | join(" ")' <<< "${RESULT:-null}" 2>/dev/null || true)
if jq -e '.api_error_status == 401' <<< "${RESULT:-null}" >/dev/null 2>&1 \
    || grep -qE 'API Error: 401|401 Unauthorized|OAuth access token is invalid|authentication_error' \
        <<< "$RESULT_TEXT"$'\n'"$(cat "$WORK/stderr.txt")"; then
    fail "the subscription token was rejected (401). Run 'claude setup-token' again and save the CLAUDE_CODE_OAUTH_TOKEN secret with nothing before or after the token" probe
fi

if [[ -z "$INIT" && $PROBE_RC -ne 0 ]]; then
    fail "probe exited with code $PROBE_RC before starting a session" probe
fi

API_KEY_SOURCE=$(jq -c '.apiKeySource // null' <<< "${INIT:-null}")
if [[ "$API_KEY_SOURCE" != '"none"' ]]; then
    fail "probe did not run on the subscription (init apiKeySource is $API_KEY_SOURCE, expected \"none\")" probe
fi

# A probe stopped by a usage limit (AC9).
RATE_LIMITED=false
if [[ -n "$RESULT" ]] && jq -e '
        (.is_error == true or .subtype != "success")
        and (([.error, .subtype, .error_type, .error_category]
               | any(. == "rate_limit" or . == "rate_limit_error"))
             or .api_error_status == 429)' <<< "$RESULT" >/dev/null 2>&1; then
    RATE_LIMITED=true
fi

if [[ $PROBE_RC -ne 0 && $RATE_LIMITED == false ]]; then
    fail "probe exited with code $PROBE_RC" probe
fi

if [[ -z "$RLE" ]]; then
    if $RATE_LIMITED; then
        emit "refuse" "probe" "usage limit reached; the probe was rejected and no allowance reading is available" 1 \
            "[RESERVE SKIP] usage limit reached; no allowance reading available"
    fi
    fail "no rate_limit_event in the probe output" probe
fi

# ─── 5. Read both windows (AC6, AC7, AC10) ───────────────────────────────────

OVERAGE_STATUS=$(jq -c '.rate_limit_info.overageStatus // null' <<< "$RLE")
read_window() {  # read_window <key> → sets W_UTIL W_RESETS, or fails
    local key="$1" w
    w=$(jq -c --arg k "$key" '.rate_limit_info.unifiedWindows[$k] // null' <<< "$RLE" 2>/dev/null || echo null)
    if [[ "$w" == "null" ]]; then
        if [[ $(jq -c '.rate_limit_info.unifiedWindows // null' <<< "$RLE") == "null" ]]; then
            fail "rate_limit_event has no unifiedWindows" probe
        fi
        fail "rate_limit_event has no unifiedWindows.$key" probe
    fi
    W_UTIL=$(jq -c '.utilization // null' <<< "$w")
    if ! jq -e 'type == "number"' <<< "$W_UTIL" >/dev/null 2>&1; then
        fail "unifiedWindows.$key.utilization is missing or not a number" probe
    fi
    W_RESETS=$(jq -c '.resetsAt | if type == "number" then . else null end' <<< "$w")
}
read_window five_hour; FIVE_UTIL="$W_UTIL"; FIVE_RESETS="$W_RESETS"
read_window seven_day; WEEK_UTIL="$W_UTIL"; WEEK_RESETS="$W_RESETS"

if [[ "$OVERAGE_STATUS" != "null" && "$OVERAGE_STATUS" != '"rejected"' ]]; then
    fail "usage credits look turned on (overageStatus $OVERAGE_STATUS, expected \"rejected\"), so a session could be billed past the limit" probe
fi

# ─── 6. Decide ───────────────────────────────────────────────────────────────

DECISION=$(jq -cn --argjson fu "$FIVE_UTIL" --argjson wu "$WEEK_UTIL" \
        --argjson rf "$RES_FIVE" --argjson rw "$RES_WEEK" \
        --argjson fr "$FIVE_RESETS" --argjson wr "$WEEK_RESETS" --argjson eps "$EPSILON" '
    def rem($u): (1 - $u) * 100;
    def shown($u): ([rem($u) + $eps | floor, 0] | max);
    def t($s): if $s == null then "unknown" else ($s | floor | strftime("%Y-%m-%dT%H:%MZ")) end;
    [ {name: "five-hour", u: $fu, r: $rf, at: $fr},
      {name: "weekly",    u: $wu, r: $rw, at: $wr} ] as $ws
    | [ $ws[] | select(rem(.u) + $eps < .r) ] as $below
    | { start: ($below | length == 0),
        until: ([$below[].at | select(. != null)] | max),
        summary: ($ws | map("\(.name) \(shown(.u))% left (reserve \(.r)%)") | join(", ")),
        below: ($below | map("\(.name) \(shown(.u))% left, below reserve \(.r)%; resets \(t(.at))") | join("; ")) }')

SUMMARY=$(jq -r '.summary' <<< "$DECISION")
if [[ $(jq -r '.start' <<< "$DECISION") == "true" ]]; then
    emit "start" "probe" "" 0 "[RESERVE] $SUMMARY — start"
fi

BELOW=$(jq -r '.below' <<< "$DECISION")
UNTIL=$(jq -c '.until' <<< "$DECISION")
if [[ "$UNTIL" != "null" ]]; then
    # Best effort: a failed write only means the next poll probes again.
    if mkdir -p "$STATE_DIR" 2>/dev/null \
        && jq -n --argjson until "$UNTIL" --argjson rf "$RES_FIVE" --argjson rw "$RES_WEEK" \
              --argjson fu "$FIVE_UTIL" --argjson fr "$FIVE_RESETS" \
              --argjson wu "$WEEK_UTIL" --argjson wr "$WEEK_RESETS" \
              '{until: $until, reserve_five_hour_percent: $rf, reserve_weekly_percent: $rw,
                five_hour: {utilization: $fu, resets_at: $fr},
                weekly: {utilization: $wu, resets_at: $wr}}' > "$HOLD_FILE.tmp" 2>/dev/null \
        && mv "$HOLD_FILE.tmp" "$HOLD_FILE" 2>/dev/null; then
        :
    else
        rm -f "$HOLD_FILE.tmp" 2>/dev/null || true
        echo "[ALLOWANCE] Warning: could not write $HOLD_FILE; the next poll will probe again" >&2
    fi
fi

emit "refuse" "probe" "$BELOW" 1 "[RESERVE SKIP] $BELOW"
