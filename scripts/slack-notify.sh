#!/usr/bin/env bash
# scripts/slack-notify.sh
#
# Slack notification adapter — posts a Block Kit message to an incoming webhook
# for any of the six dispatcher notification event types.
# Implements: issue #178
#
# Usage:
#   SLACK_WEBHOOK_URL=<url> bash scripts/slack-notify.sh <EVENT_TYPE> <CONTEXT_JSON>
#
# Supported EVENT_TYPE values:
#   decision_blocker          fields: story_title, issue_url, blocker_type, unblocked_when
#   feature_closure_confirmation  fields: feature_title, summary_url
#   epic_closure_approval     fields: epic_title, summary_url
#   red_flow_health           fields: signal, wip_count, blocked_count, board_url
#   dispatcher_error          fields: error_description, last_action
#   ready_for_refinement      fields: issue_title, issue_url
#
# Exit codes:
#   0  — message sent successfully, or unrecognised event type (warning logged, no request made)
#   1  — SLACK_WEBHOOK_URL absent
#   non-zero — curl transport failure
#
# Requirements: bash, curl, jq

set -euo pipefail

# ─── Arguments ────────────────────────────────────────────────────────────────

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <EVENT_TYPE> <CONTEXT_JSON>" >&2
    exit 1
fi

EVENT_TYPE="$1"
CONTEXT_JSON="$2"

# ─── Validate SLACK_WEBHOOK_URL ───────────────────────────────────────────────

if [[ -z "${SLACK_WEBHOOK_URL:-}" ]]; then
    echo "Error: SLACK_WEBHOOK_URL is not set. Set the environment variable before calling this script." >&2
    exit 1
fi

# ─── Build Block Kit payload per event type ──────────────────────────────────

build_payload() {
    local event="$1"
    local ctx="$2"

    case "$event" in

        decision_blocker)
            local story_title issue_url blocker_type unblocked_when
            story_title=$(echo "$ctx" | jq -r '.story_title // "(unknown)"')
            issue_url=$(echo "$ctx" | jq -r '.issue_url // ""')
            blocker_type=$(echo "$ctx" | jq -r '.blocker_type // "(unknown)"')
            unblocked_when=$(echo "$ctx" | jq -r '.unblocked_when // "(unknown)"')
            jq -n \
                --arg story_title "$story_title" \
                --arg issue_url "$issue_url" \
                --arg blocker_type "$blocker_type" \
                --arg unblocked_when "$unblocked_when" \
                '{
                    blocks: [
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ":warning: *Decision Blocker*" }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Story:* " + $story_title + "\n*Blocker type:* " + $blocker_type + "\n*Unblocked when:* " + $unblocked_when) }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Issue:* " + (if $issue_url != "" then "<" + $issue_url + "|View issue>" else "(no URL)" end)) }
                        }
                    ]
                }'
            ;;

        feature_closure_confirmation)
            local feature_title summary_url
            feature_title=$(echo "$ctx" | jq -r '.feature_title // "(unknown)"')
            summary_url=$(echo "$ctx" | jq -r '.summary_url // ""')
            jq -n \
                --arg feature_title "$feature_title" \
                --arg summary_url "$summary_url" \
                '{
                    blocks: [
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ":white_check_mark: *Feature Closure — Confirmation Requested*" }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Feature:* " + $feature_title) }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Summary:* " + (if $summary_url != "" then "<" + $summary_url + "|View summary>" else "(no URL)" end)) }
                        }
                    ]
                }'
            ;;

        epic_closure_approval)
            local epic_title summary_url
            epic_title=$(echo "$ctx" | jq -r '.epic_title // "(unknown)"')
            summary_url=$(echo "$ctx" | jq -r '.summary_url // ""')
            jq -n \
                --arg epic_title "$epic_title" \
                --arg summary_url "$summary_url" \
                '{
                    blocks: [
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ":trophy: *Epic Closure — Approval Requested*" }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Epic:* " + $epic_title) }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Summary:* " + (if $summary_url != "" then "<" + $summary_url + "|View summary>" else "(no URL)" end)) }
                        }
                    ]
                }'
            ;;

        red_flow_health)
            local signal wip_count blocked_count board_url
            signal=$(echo "$ctx" | jq -r '.signal // "(unknown)"')
            wip_count=$(echo "$ctx" | jq -r '.wip_count // "?"')
            blocked_count=$(echo "$ctx" | jq -r '.blocked_count // "?"')
            board_url=$(echo "$ctx" | jq -r '.board_url // ""')
            jq -n \
                --arg signal "$signal" \
                --arg wip_count "$wip_count" \
                --arg blocked_count "$blocked_count" \
                --arg board_url "$board_url" \
                '{
                    blocks: [
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ":red_circle: *Red Flow Health Signal*" }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Signal:* " + $signal + "\n*WIP count:* " + $wip_count + "\n*Blocked count:* " + $blocked_count) }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Board:* " + (if $board_url != "" then "<" + $board_url + "|View board>" else "(no URL)" end)) }
                        }
                    ]
                }'
            ;;

        dispatcher_error)
            local error_description last_action
            error_description=$(echo "$ctx" | jq -r '.error_description // "(unknown)"')
            last_action=$(echo "$ctx" | jq -r '.last_action // "(unknown)"')
            jq -n \
                --arg error_description "$error_description" \
                --arg last_action "$last_action" \
                '{
                    blocks: [
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ":x: *Dispatcher Error*" }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Error:* " + $error_description) }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Last action:* " + $last_action) }
                        }
                    ]
                }'
            ;;

        ready_for_refinement)
            local issue_title issue_url
            issue_title=$(echo "$ctx" | jq -r '.issue_title // "(unknown)"')
            issue_url=$(echo "$ctx" | jq -r '.issue_url // ""')
            jq -n \
                --arg issue_title "$issue_title" \
                --arg issue_url "$issue_url" \
                '{
                    blocks: [
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ":pencil: *Ready for Refinement*" }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Issue:* " + $issue_title) }
                        },
                        {
                            type: "section",
                            text: { type: "mrkdwn", text: ("*Link:* " + (if $issue_url != "" then "<" + $issue_url + "|View issue>" else "(no URL)" end)) }
                        }
                    ]
                }'
            ;;

        *)
            echo "__UNKNOWN_EVENT__"
            ;;
    esac
}

# ─── Validate event type before building payload ──────────────────────────────

KNOWN_EVENTS="decision_blocker feature_closure_confirmation epic_closure_approval red_flow_health dispatcher_error ready_for_refinement"
if ! echo "$KNOWN_EVENTS" | grep -qw "$EVENT_TYPE"; then
    echo "Warning: Unrecognised event type '${EVENT_TYPE}'. No message sent." >&2
    exit 0
fi

# ─── Send ─────────────────────────────────────────────────────────────────────

PAYLOAD=$(build_payload "$EVENT_TYPE" "$CONTEXT_JSON")

curl -sf \
    -H "Content-Type: application/json" \
    -X POST \
    --data "$PAYLOAD" \
    "$SLACK_WEBHOOK_URL"
