#!/usr/bin/env bash
# scripts/slack-notify.sh
#
# Slack notification adapter — posts a Block Kit message to an incoming webhook
# for any of the dispatcher notification event types.
# Implements: issue #178, issue #204
#
# Usage:
#   SLACK_WEBHOOK_URL=<url> bash scripts/slack-notify.sh <EVENT_TYPE> <CONTEXT_JSON>
#
# Supported EVENT_TYPE values:
#   decision_blocker          fields: story_title, issue_url, blocker_type, unblocked_when,
#                                     question, options, recommendation (the last three optional;
#                                     missing ones are named in the message)
#   po_blocker                fields: story_title, issue_url, blocker_type, unblocked_when,
#                                     state, cause (cause optional; a blocker only the PO resolves,
#                                     resumed by a standalone "/unblock" line, issue #359)
#   feature_closure_confirmation  fields: feature_title, summary_url
#   epic_closure_approval     fields: epic_title, summary_url
#   red_flow_health           fields: signal, wip_count, blocked_count, board_url
#   dispatcher_error          fields: error_description, last_action
#   ready_for_refinement      fields: issue_title, issue_url
#   usage_limit_reached       fields: sessions_today, limit, settings_url
#   dispatch_trigger_stopped  fields: age, last_created_at, check_command, docs_url (issue #339;
#                                     age is "none" when no dispatched run exists)
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
            # Block Kit allows 3000 characters per section text and 50 blocks;
            # the payload has a fixed number of blocks and every text is capped
            # below 3000. Options are cut consequence-first (letter and name
            # stay); question and recommendation are cut last, with the
            # larger caps. The issue link and resume line are never cut.
            jq -n --argjson c "$ctx" '
                def cut($n): if length > $n then .[0:($n - 1)] + "…" else . end;
                def str: if type == "string" then . else "" end;
                def clean: str | sub("^\\s+"; "") | sub("\\s+$"; "");
                def cut_option($n):
                    if length <= $n then .
                    else (index(" — ")) as $i
                        | if $i == null or $i + 4 >= $n then cut($n)
                          else .[0:($i + 3)] + (.[($i + 3):] | cut($n - $i - 3)) end
                    end;
                def cut_options($total):
                    split("\n") | map(select(test("\\S"))) as $ls
                    | ($ls | length) as $k
                    | if $k == 0 then ""
                      else (($total - $k + 1) / $k | floor) as $per
                        | $ls | map(cut_option($per)) | join("\n") end;
                ($c.story_title | clean) as $title
                | ($c.issue_url | clean) as $url
                | ($c.blocker_type | clean) as $type
                | ($c.unblocked_when | clean) as $uw
                | ($c.question | clean) as $q
                | ($c.options | clean) as $o
                | ($c.recommendation | clean) as $r
                | ([if $q == "" then "Question" else empty end,
                    if $o == "" then "Options" else empty end,
                    if $r == "" then "Recommendation" else empty end]) as $missing
                | {blocks: ([
                    {type: "section", text: {type: "mrkdwn", text: ":warning: *Decision Blocker*"}},
                    {type: "section", text: {type: "mrkdwn", text: (
                        "*Story:* " + (if $title == "" then "(unknown)" else $title | cut(500) end)
                        + "\n*Blocker type:* " + (if $type == "" then "(unknown)" else $type | cut(100) end))}}
                  ]
                  + (if $q != "" then [{type: "section", text: {type: "mrkdwn", text: ("*Question:* " + ($q | cut(1000)))}}] else [] end)
                  + (if $o != "" then [{type: "section", text: {type: "mrkdwn", text: ("*Options:*\n" + ($o | cut_options(2500)))}}] else [] end)
                  + (if $r != "" then [{type: "section", text: {type: "mrkdwn", text: ("*Recommendation:* " + ($r | cut(1000)))}}] else [] end)
                  + (if ($missing | length) > 0 then [{type: "section", text: {type: "mrkdwn", text: ("_Declaration is missing: " + ($missing | join(", ")) + "_")}}] else [] end)
                  + [
                    {type: "section", text: {type: "mrkdwn", text: ("*Unblocked when:* " + (if $uw == "" then "(unknown)" else $uw | cut(1000) end))}},
                    {type: "section", text: {type: "mrkdwn", text: ("*Issue:* " + (if $url != "" then "<" + $url + "|View issue>" else "(no URL)" end))}},
                    {type: "section", text: {type: "mrkdwn", text: "To answer: reply on the issue with your decision and a line that is exactly `/unblock`."}}
                  ])}'
            ;;

        po_blocker)
            # A blocker the dispatcher posted and only the PO can resolve
            # (issue #359). Every text is capped below Block Kit's 3000
            # characters; the issue link and the resume line are never cut.
            jq -n --argjson c "$ctx" '
                def cut($n): if length > $n then .[0:($n - 1)] + "…" else . end;
                def str: if type == "string" then . else "" end;
                def clean: str | sub("^\\s+"; "") | sub("\\s+$"; "");
                def or_unknown($n): if . == "" then "(unknown)" else cut($n) end;
                ($c.story_title | clean) as $title
                | ($c.issue_url | clean) as $url
                | ($c.blocker_type | clean) as $type
                | ($c.unblocked_when | clean) as $uw
                | ($c.state | clean) as $state
                | ($c.cause | clean) as $cause
                | {blocks: ([
                    {type: "section", text: {type: "mrkdwn", text: ":octagonal_sign: *Story stopped — waiting for the PO*"}},
                    {type: "section", text: {type: "mrkdwn", text: (
                        "*Story:* " + ($title | or_unknown(500))
                        + "\n*Blocker type:* " + ($type | or_unknown(100)))}},
                    {type: "section", text: {type: "mrkdwn", text: ("*Unblocked when:* " + ($uw | or_unknown(1000)))}},
                    {type: "section", text: {type: "mrkdwn", text: ("*State of in-progress work:* " + ($state | or_unknown(1000)))}}
                  ]
                  + (if $cause != "" then [{type: "section", text: {type: "mrkdwn", text: ("*Cause:* " + ($cause | cut(1000)))}}] else [] end)
                  + [
                    {type: "section", text: {type: "mrkdwn", text: ("*Issue:* " + (if $url != "" then "<" + $url + "|View issue>" else "(no URL)" end))}},
                    {type: "section", text: {type: "mrkdwn", text: "To resume: do what *Unblocked when* says, then reply on the issue with a line that is exactly `/unblock`."}}
                  ])}'
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


        usage_limit_reached)
            local sessions_today limit settings_url
            sessions_today=$(echo "$ctx" | jq -r '.sessions_today // "(unknown)"')
            limit=$(echo "$ctx" | jq -r '.limit // "(unknown)"')
            settings_url=$(echo "$ctx" | jq -r '.settings_url // ""')
            jq -n --arg sessions_today "$sessions_today" --arg limit "$limit" --arg settings_url "$settings_url" '{ blocks: [ { type: "section", text: { type: "mrkdwn", text: ":no_entry: *Daily session limit reached -- Dispatcher Paused*" } }, { type: "section", text: { type: "mrkdwn", text: ("The dispatcher has reached your daily session limit and starts no new work until 00:00 UTC.\n*Sessions today:* " + $sessions_today + " of " + $limit) } }, { type: "section", text: { type: "mrkdwn", text: (if $settings_url != "" then "<" + $settings_url + "|Change the limit or toggle DISPATCHER_ENABLED in GitHub Settings>" else "Change the limit or toggle DISPATCHER_ENABLED in GitHub Settings" end) } } ] }'
            ;;

        dispatch_trigger_stopped)
            jq -n --argjson c "$ctx" '
                def cut($n): if length > $n then .[0:($n - 1)] + "…" else . end;
                def str: if type == "string" then . else "" end;
                def clean: str | sub("^\\s+"; "") | sub("\\s+$"; "");
                def or_unknown: if . == "" then "(unknown)" else cut(500) end;
                ($c.age | clean) as $age
                | ($c.last_created_at | clean) as $at
                | ($c.check_command | clean) as $cmd
                | ($c.docs_url | clean) as $url
                | {blocks: [
                    {type: "section", text: {type: "mrkdwn", text: ":rotating_light: *Dispatcher trigger stopped*"}},
                    {type: "section", text: {type: "mrkdwn", text: (
                        "*Age of the newest dispatched run:* " + ($age | or_unknown)
                        + "\n*Created:* " + ($at | or_unknown))}},
                    {type: "section", text: {type: "mrkdwn", text: ("*Manual check:* `" + ($cmd | or_unknown) + "`")}},
                    {type: "section", text: {type: "mrkdwn", text: ("*Fix:* " + (if $url != "" then "<" + $url + "|Setup and token rotation>" else "see the trigger section of docs/DISPATCHER-CONFIG.md" end))}}
                  ]}'
            ;;

        *)
            echo "__UNKNOWN_EVENT__"
            ;;
    esac
}

# ─── Validate event type before building payload ──────────────────────────────

KNOWN_EVENTS="decision_blocker po_blocker feature_closure_confirmation epic_closure_approval red_flow_health dispatcher_error ready_for_refinement usage_limit_reached dispatch_trigger_stopped"
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
