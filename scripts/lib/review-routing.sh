#!/usr/bin/env bash
# scripts/lib/review-routing.sh
#
# The review-path tables (issues #263, #293, #315; ADR-006, ADR-012), shared by
# scripts/dispatcher-invoke.sh and scripts/dispatcher-poll.sh so that the rule
# "which executor's PR is reviewed by a dispatcher session" has one source.
# How a story is reviewed is looked up by its /implement executor label suffix.
# An executor has a row in exactly one of the two tables:
#   REVIEW_EXECUTOR_ROUTING   — a /review session started by the dispatcher on
#                               the story's PR, under the executor named by the
#                               value (resume entry for a story "In review").
#   CODEX_REVIEW_LOOP_ROUTING — no dispatcher session: the story is handed to
#                               the Codex review loop (ADR-006), and the value
#                               names the reviewer for the log.
# OSS invariant: lookup tables, not conditional branches on an executor name.
#
# Source this file; it only declares the tables and one helper.
# Requires: bash 4+

declare -A REVIEW_EXECUTOR_ROUTING=(
    ["codex"]="claude-code-opus"
)

declare -A CODEX_REVIEW_LOOP_ROUTING=(
    ["claude-code-haiku"]="Codex Cloud"
    ["claude-code-sonnet"]="Codex Cloud"
    ["claude-code-opus"]="Codex Cloud"
)

# review_by_dispatcher_session <executor-suffix>... — 0 when exactly one
# executor label is given and it has a REVIEW_EXECUTOR_ROUTING row.
review_by_dispatcher_session() {
    [[ $# -eq 1 && -n "${REVIEW_EXECUTOR_ROUTING[$1]:-}" ]]
}
