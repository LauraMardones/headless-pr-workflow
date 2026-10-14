#!/usr/bin/env bash
# scripts/lib/decision-blocker.sh
#
# Parses a "## Blocked Declaration" comment body of Type: decision into the
# context JSON of the decision_blocker Slack event (issue #296).
#
# decision_blocker_context <body> <story_title> <issue_url>
#   Prints {story_title, issue_url, blocker_type, unblocked_when, question,
#   options, recommendation}. A field runs from its label at the start of a
#   line to the next known declaration label, so Options keeps its lines and a
#   label name inside option text does not end the field. A field the
#   declaration lacks is an empty string. The body is only ever passed to jq as
#   data (--arg), never interpolated into a shell string or jq program.

decision_blocker_context() {
    local body="$1" title="$2" url="$3"
    jq -nc --arg body "$body" --arg st "$title" --arg iu "$url" '
        ["Type","Checked","Question","Options","Recommendation","Declared by",
         "Blocks","Unblocked when","Owner","State of in-progress work",
         "Resume instruction"] as $labels
        | ($body | gsub("\r"; "") | split("\n")) as $lines
        | reduce $lines[] as $line ({cur: null, f: {}};
            ([$labels[] | . as $x | select($line | startswith($x + ":"))] | first) as $l
            | if $l != null then
                .cur = $l | .f[$l] = ($line[($l | length) + 1:] | sub("^[ \t]+"; ""))
              elif .cur != null then
                .f[.cur] += "\n" + $line
              else . end)
        | .f as $f
        | def field($k): ($f[$k] // "") | sub("^\\s+"; "") | sub("\\s+$"; "");
          {story_title: $st, issue_url: $iu, blocker_type: "decision",
           unblocked_when: (field("Unblocked when")
               | if . == "" then "condition to be determined by PO" else . end),
           question: field("Question"),
           options: field("Options"),
           recommendation: field("Recommendation")}'
}
