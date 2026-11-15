"""Registry of the dispatcher's next-step decision rules (issue #555, ADR-014 §7).

``docs/DISPATCHER-DECISIONS.md`` is the written inventory; this module records the
same rules as data. For each rule it names the single owning file, a marker for
each implementation (a function, variable or constant name that appears
literally in that file) and the known duplicate locations: every implementation
outside the owner. The duplicates are the allowlist that the duplicate-rule guard
(#570) shrinks as the consolidation stories (#556, #557, Feature #526) remove
copies.

This module only records today's state. No dispatcher code imports it, so it
changes no decision. ``tests/test_dispatcher_rules_registry.py`` keeps it and the
inventory in step.
"""

from __future__ import annotations

from dataclasses import dataclass

ENTRY_POINTS = ("poll", "invoke", "resume", "codex-worker")

_PY = "src/headless_pr_workflow/dispatcher/"
INVOKE = "scripts/dispatcher-invoke.sh"
POLL = "scripts/dispatcher-poll.sh"
MERGE = "scripts/dispatcher-merge.sh"
IN_FLIGHT_SH = "scripts/lib/in-flight.sh"
CONFLICT_ROUND_SH = "scripts/lib/conflict-round.sh"
REVIEW_ROUTING_SH = "scripts/lib/review-routing.sh"
SCHEDULER_SH = "scripts/lib/scheduler.sh"
LIMITS_SH = "scripts/lib/limits.sh"
CLAUDE_SESSION_SH = "scripts/lib/claude-session.sh"
MERGE_GATE = "scripts/merge-gate-summary"
NOTIFY_RETRY = "scripts/dispatcher-notify-retry.sh"
BOOKKEEPING = "src/headless_pr_workflow/codex_review_bookkeeping.py"
REVIEW_POLICY = "src/headless_pr_workflow/review_policy.py"
IN_FLIGHT = _PY + "in_flight.py"
SCHEDULER = _PY + "scheduler.py"
ADMISSION = _PY + "admission.py"
FACTS = _PY + "facts.py"
STATUS = _PY + "status.py"
GATEWAY = _PY + "gateway.py"
MARKER = _PY + "marker.py"
WRITE = _PY + "write.py"
SESSION_BLOCKER = _PY + "session_blocker.py"
CODEX_WORKER = _PY + "codex_worker.py"
CODEX_RESERVE = _PY + "codex_reserve.py"


@dataclass(frozen=True)
class Implementation:
    path: str
    marker: str


@dataclass(frozen=True)
class Rule:
    name: str
    decides: str
    owner: str
    implementations: tuple[Implementation, ...]
    duplicates: tuple[Implementation, ...] = ()
    entry_points: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    divergence: str = ""


def _rule(name: str, decides: str, owner: str, impls: list[tuple[str, str]], entry_points: str,
          tests: str, divergence: str = "") -> Rule:
    implementations = tuple(Implementation(path, marker) for path, marker in impls)
    return Rule(
        name=name,
        decides=decides,
        owner=owner,
        implementations=implementations,
        duplicates=tuple(impl for impl in implementations if impl.path != owner),
        entry_points=tuple(entry_points.split()),
        tests=tuple("tests/" + test for test in tests.split()),
        divergence=divergence,
    )


RULES: tuple[Rule, ...] = (
    _rule("linked_pr_lookup", "Which open PR is a story's linked PR.", IN_FLIGHT_SH,
          [(IN_FLIGHT_SH, "INFLIGHT_JQ_LINKED"), (INVOKE, "find_linked_pr_json"), (INVOKE, "find_linked_pr"),
           (STATUS, "_linked"), (CODEX_WORKER, "_CLOSES")],
          "poll invoke resume codex-worker",
          "test_dispatcher_poll_pagination.sh test_dispatcher_status.py test_codex_worker.py "
          "test_dispatcher_review_loop.py",
          "`find_linked_pr_json` reads only the first 100 open PRs, `INFLIGHT_JQ_LINKED` every page; `_CLOSES` "
          "also matches close/closed/fix/fixed/resolve/resolved; `_linked` keeps every linked PR, the jq "
          "takes the first; `find_linked_pr` has no caller."),
    _rule("merged_pr_finish", "A story with no open linked PR and a merged one goes to the finish step.", IN_FLIGHT,
          [(IN_FLIGHT, "plan_in_flight"), (IN_FLIGHT_SH, "inflight_compute"), (INVOKE, "find_merged_linked_pr")],
          "poll resume",
          "test_dispatcher_in_flight.py test_dispatcher_in_flight_facts.py test_dispatcher_cleanup_script.py"),
    _rule("merge_gates_pass", "Whether the merge gates pass on the PR's current head.", MERGE_GATE,
          [(MERGE_GATE, "GateResult"), (GATEWAY, "merge_gates_pass"), (IN_FLIGHT_SH, "inflight_gates_pass")],
          "poll resume",
          "test_dispatcher_in_flight_facts.py test_dispatcher_status.py test_merge_gate_summary.py "
          "test_dispatcher_merge_script.py",
          "`inflight_gates_pass` accepts any `mergeable=` value, `GateResult` needs `mergeable` yes: a "
          "conflicting PR is in the in-flight merge step but shows In review on the board."),
    _rule("in_flight_step", "Which resume step a story is in, and the in-implementation and in-flight lists.",
          IN_FLIGHT,
          [(IN_FLIGHT, "plan_in_flight"), (IN_FLIGHT_SH, "inflight_compute"), (CODEX_WORKER, "work_in_flight"),
           (STATUS, "derive_status")],
          "poll invoke resume codex-worker",
          "test_dispatcher_in_flight.py test_dispatcher_in_flight_facts.py test_codex_session_runbook.py "
          "test_dispatcher_status.py",
          "`work_in_flight` counts any open closing PR and does not separate drafts; `derive_status` picks a "
          "gated PR among all linked PRs, `plan_in_flight` uses only the first."),
    _rule("wip_limit", "No new /implement once the in-implementation count reaches `WIP_LIMIT_IMPLEMENTATION`.",
          ADMISSION, [(ADMISSION, "wip_reached")],
          "poll invoke codex-worker",
          "test_wip_queue_limits.py test_dispatcher_scheduler.py test_dispatcher_scheduler_new_work.py "
          "test_dispatcher_admission.py",
          ""),
    _rule("limits_valid", "Whether the three limit variables are valid; invalid values fail closed.", SCHEDULER,
          [(SCHEDULER, "load_limits")],
          "poll invoke codex-worker",
          "test_wip_queue_limits.py test_dispatcher_scheduler.py test_dispatcher_admission.py"),
    _rule("file_overlap", "No start for a story whose files affected overlap a story in implementation.", ADMISSION,
          [(ADMISSION, "overlapping_paths")],
          "poll invoke",
          "test_dispatcher_facts_block.py test_dispatcher_scheduler.py test_wip_queue_limits.py "
          "test_dispatcher_admission.py"),
    _rule("next_new_work_selection", "Which issue to refine or implement next, and in what order.", SCHEDULER,
          [(SCHEDULER, "choose_next"), (SCHEDULER_SH, "scheduler_next"), (INVOKE, "ask_scheduler"),
           (INVOKE, "next_new_work"), (CODEX_WORKER, "_queue")],
          "poll invoke codex-worker",
          "test_dispatcher_scheduler.py test_dispatcher_scheduler_new_work.py test_codex_worker.py",
          "`_queue` picks by board status and the lowest `prio:` label; `choose_next` sorts by step, group, "
          "Epic prio, spec commit, prio, criterion prefix and number, and reads no board status."),
    _rule("refine_executor_split", "Whether the Actions dispatcher or the Codex worker refines a story.", SCHEDULER,
          [(SCHEDULER, "choose_next"), (SCHEDULER, "codex_refine_due"), (CODEX_WORKER, "_queue")],
          "poll invoke codex-worker",
          "test_codex_session_runbook.py test_dispatcher_scheduler.py",
          "the facts take the first `executor:` label, `_queue` needs exactly `executor:codex`: a story with "
          "`executor:codex` first and a second executor label is skipped by both."),
    _rule("executable_issue_type", "Whether an issue counts as a Story, Task or Bug.", ADMISSION,
          [(ADMISSION, "classify_executable_type"), (IN_FLIGHT_SH, "inflight_compute"),
           (CODEX_WORKER, "TYPE_LABELS")],
          "poll invoke resume codex-worker",
          "test_dispatcher_invoke_non_executable.sh test_dispatcher_facts_block.py test_dispatcher_admission.py",
          "the in-flight jq filter accepts any of several `type:` labels (in-flight rules: #556); `TYPE_LABELS` "
          "ignores the native issue type (Codex worker queue: Epic #518)."),
    _rule("executor_label_routing", "Which executor label decides a story and whether the dispatcher starts it.",
          ADMISSION, [(ADMISSION, "executor_labels"), (IN_FLIGHT_SH, "inflight_compute")],
          "poll invoke resume",
          "test_dispatcher_invoke_non_executable.sh test_dispatcher_codex_pr_review.py test_dispatcher_admission.py",
          "the in-flight jq counts the `executor:` labels itself (in-flight rules: #556); a story with two labels "
          "gets /implement and a hand-off but never a fix round."),
    _rule("review_path_routing", "A dispatcher /review session or the Codex review loop for a PR.", REVIEW_ROUTING_SH,
          [(REVIEW_ROUTING_SH, "REVIEW_EXECUTOR_ROUTING"), (REVIEW_ROUTING_SH, "CODEX_REVIEW_LOOP_ROUTING"),
           (REVIEW_ROUTING_SH, "review_by_dispatcher_session"), (INVOKE, "is_review_loop_story")],
          "poll invoke resume",
          "test_dispatcher_review_loop.py test_dispatcher_codex_pr_review.py"),
    _rule("model_selection", "Which model runs a session.", INVOKE,
          [(INVOKE, "EXECUTOR_MODEL"), (INVOKE, "REFINE_TIER_BY_TYPE"), (INVOKE, "refine_executor_label")],
          "invoke resume",
          "test_dispatcher_codex_pr_review.py test_dispatcher_refine_session.py"),
    _rule("session_bounds", "The turn and wall-clock limits of a session.", INVOKE,
          [(INVOKE, "AGENT_MAX_TURNS"), (INVOKE, "AGENT_MAX_WALLCLOCK_SECONDS")],
          "invoke resume",
          "test_dispatcher_claude_session.py test_dispatcher_usage_limit_pause.py"),
    _rule("fix_round_due", "Whether the bookkeeping job's findings exist for the PR's current head.", IN_FLIGHT,
          [(IN_FLIGHT, "findings_on_head"), (INVOKE, "fix_round_due"), (INVOKE, "fix_round_heads"),
           (CONFLICT_ROUND_SH, "fix_due")],
          "poll invoke resume",
          "test_dispatcher_in_flight_facts.py test_dispatcher_review_loop.py test_dispatcher_conflict_round.py",
          "`findings_on_head` and `fix_round_due` ignore the fix-round attempt marker, `conflict_round_rule` "
          "needs no attempt held: a tried round stays in `fix_round`, counts for the WIP limit and keeps the "
          "poll resumable."),
    _rule("fix_round_attempt_once_per_head", "No second fix round on a head that was already tried.", INVOKE,
          [(INVOKE, "fix_round_attempt_marker"), (INVOKE, "fix_round_attempt_superseded"),
           (CONFLICT_ROUND_SH, "conflict_round_rule")],
          "resume",
          "test_dispatcher_review_loop.py test_dispatcher_conflict_round.py test_dispatcher_marker_block.py",
          "`marker_holds` in `marker.py` takes the last marker in list order, the jq takes the newest "
          "`created_at`."),
    _rule("fix_round_max", "Stop after `CODEX_FIX_ROUND_MAX` fix rounds with a resource blocker.", INVOKE,
          [(INVOKE, "CODEX_FIX_ROUND_MAX")],
          "resume",
          "test_dispatcher_review_loop.py test_dispatcher_po_blocker_notify.py",
          "`plan_in_flight` does not apply the maximum, so a story over it stays in implementation and "
          "resumable."),
    _rule("no_push_round_expected", "Whether a fix round that pushed nothing is allowed.", REVIEW_POLICY,
          [(REVIEW_POLICY, "is_codex_review_request"), (INVOKE, "no_push_round_is_expected"),
           (INVOKE, "review_request_after")],
          "invoke resume",
          "test_dispatcher_review_loop.py"),
    _rule("hand_off_pending", "Whether an earlier run left a hand-off to finish.", IN_FLIGHT,
          [(IN_FLIGHT, "hand_off_pending"), (INVOKE, "hand_off_pending_prefix"),
           (INVOKE, "complete_pending_hand_off")],
          "poll resume",
          "test_dispatcher_in_flight.py test_dispatcher_in_flight_facts.py test_dispatcher_review_loop.py",
          "`hand_off_pending` takes the newest `created_at`, `marker_holds` the last in list order; "
          "`resume_fix_round` matches the full marker with the head, not the prefix."),
    _rule("post_implement_outcome", "After /implement: no PR, a draft PR, a round that pushed nothing, or a hand-off.",
          SESSION_BLOCKER,
          [(SESSION_BLOCKER, "open_session_blocker"), (INVOKE, "hand_off_to_codex_review_loop")],
          "invoke resume",
          "test_dispatcher_session_blocker.py test_dispatcher_review_loop.py"),
    _rule("conflict_round_owed", "A conflict round, or tried, blocked, unknown or ownerless, for a review-loop PR.",
          CONFLICT_ROUND_SH,
          [(CONFLICT_ROUND_SH, "conflict_round_rule"), (INVOKE, "conflict_round_state"),
           (INVOKE, "check_review_loop_pr"), (INVOKE, "pr_conflicts_with_main"), (POLL, "CONFLICT_DUE")],
          "poll resume",
          "test_dispatcher_conflict_round.py test_dispatcher_review_loop.py",
          "the poll applies the rule only to review-step rows, the resume pass also to merge-step and "
          "fix-round rows."),
    _rule("merge_owner_evidence", "Whether the head carries the dispatcher's merge-owner marker.", MARKER,
          [(MARKER, "marker_holds"), (CONFLICT_ROUND_SH, "owned"), (INVOKE, "head_has_merge_owner_marker"),
           (INVOKE, "block_ownerless_head"), (MERGE, "holds_rc"), (POLL, "OWNER_MISSING_DUE")],
          "poll resume",
          "test_dispatcher_in_merge_owner_missing.py test_dispatcher_conflict_round.py "
          "test_dispatcher_merge_script.py",
          "`$owned` also accepts the evidence-pending copy, `head_has_merge_owner_marker` only the marker and "
          "counts a failed read as present; `dispatcher-merge.sh` uses `marker holds`, so a later PO "
          "`/unblock` voids ownership."),
    _rule("attempt_once_per_head", "No second /merge or /review session on the same head.", MARKER,
          [(MARKER, "marker_holds"), (INVOKE, "merge_attempt_marker"), (INVOKE, "review_attempt_marker")],
          "resume",
          "test_dispatcher_marker_block.py test_dispatcher_codex_pr_review.py "
          "test_dispatcher_invoke_merge_cleanup_scripts.py"),
    _rule("pytest_check_green", "Hand off only when the `pytest` check passed on the head.", MERGE_GATE,
          [(MERGE_GATE, "_checks_value"), (INVOKE, "pytest_green_on_head")],
          "resume",
          "test_dispatcher_conflict_round.py test_merge_gate_summary.py",
          "only the conflict round checks `pytest` before a hand-off; `pytest_green_on_head` takes the newest "
          "run by `started_at`, the gate its required-checks evaluation."),
    _rule("draft_ready_signal", "A draft PR means implementing; a ready PR means review, merge or the review queue.",
          IN_FLIGHT,
          [(IN_FLIGHT, "plan_in_flight"), (STATUS, "derive_status"), (CONFLICT_ROUND_SH, "not-open"),
           (CODEX_RESERVE, "waiting_codex_prs")],
          "poll invoke resume codex-worker",
          "test_dispatcher_in_flight.py test_dispatcher_status.py test_codex_reserve.py",
          "`waiting_codex_prs` finds Codex PRs by the branch prefix, not by the linked issue."),
    _rule("open_blocker", "Whether a story has an open Blocked Declaration.", STATUS,
          [(STATUS, "other_blocker_open"), (CONFLICT_ROUND_SH, "open_blocker"),
           (SESSION_BLOCKER, "open_session_blocker"), (FACTS, "dependency_blocker"),
           (INVOKE, "latest_decision_blocker_json")],
          "poll invoke resume codex-worker",
          "test_dispatcher_session_blocker.py test_dispatcher_facts_block.py test_dispatcher_status.py "
          "test_dispatcher_conflict_round.py test_dispatcher_in_flight.py",
          "the jq `open_blocker` counts every type and is not lifted by a trusted Unblocked comment; "
          "`other_blocker_open` counts four types, only the newest declaration, and is (a standalone PO `/unblock` lifts it when `po_unblock` is set, as both callers do); "
          "`open_session_blocker` leaves out automation declarers."),
    _rule("po_unblock", "Whether a standalone PO `/unblock` line after a time lifts a blocker or marker.", WRITE,
          [(WRITE, "_po_unblocked_after"), (CONFLICT_ROUND_SH, "unblocked_after"), (FACTS, "_UNBLOCK_LINE"),
           (POLL, "PO_LOGIN"), (INVOKE, "DECISION_BLOCKER_PO_LOGIN"),
           (NOTIFY_RETRY, "DECISION_BLOCKER_PO_LOGIN"), (BOOKKEEPING, "DECISION_BLOCKER_PO_LOGIN")],
          "poll invoke resume codex-worker",
          "test_dispatcher_po_login_env.py test_dispatcher_marker_block.py test_dispatcher_in_flight.py "
          "test_dispatcher_invoke_resume.sh",
          "the PO login defaults differ: empty in `_Facts`, `DEFAULT_PO_LOGIN` in `write.py`, hard-coded in "
          "the poll; the Codex worker runs without `DECISION_BLOCKER_PO_LOGIN` and counts no `/unblock`."),
    _rule("stale_spec_gate", "No session for a story whose spec record is stale.", SCHEDULER,
          [(SCHEDULER, "choose_next"), (INVOKE, "resume_session_allowed"), (INVOKE, "read_resume_facts")],
          "poll invoke resume",
          "test_dispatcher_in_flight.py test_dispatcher_in_flight_facts.py test_dispatcher_scheduler.py",
          "the Codex worker `claim` starts /implement on board-status items with no stale-record check."),
    _rule("codex_claim_lease", "Whether the Codex worker's claim on a story is held, expired or retried.",
          CODEX_WORKER,
          [(CODEX_WORKER, "derive_state"), (CODEX_WORKER, "DEFAULT_LEASE_MINUTES"),
           (CODEX_WORKER, "DEFAULT_RETRY_MINUTES")],
          "codex-worker",
          "test_codex_worker.py"),
    _rule("codex_resume_branch", "Whether the Codex worker resumes an existing branch or PR for a story.",
          CODEX_WORKER,
          [(CODEX_WORKER, "resolve_resume")],
          "codex-worker",
          "test_codex_worker.py",
          "the foreign-PR check uses the broader `_CLOSES` pattern of `linked_pr_lookup`."),
    _rule("codex_eligibility_reserve", "Whether the Codex worker may start new work given the Claude reserve.",
          CODEX_RESERVE,
          [(CODEX_RESERVE, "below_reserve"), (CODEX_RESERVE, "is_new_work")],
          "codex-worker",
          "test_codex_reserve.py",
          "`is_new_work` means no Codex branch, the in-flight rule means no open linked PR: a branch without "
          "a PR is a resume for Codex and new work for the scheduler."),
    _rule("claude_allowance_usage_gate", "Whether a Claude session may start, given the usage limit and allowance.",
          INVOKE,
          [(INVOKE, "check_usage_limit"), (INVOKE, "check_subscription_allowance"),
           (INVOKE, "pause_at_usage_limit"), (CLAUDE_SESSION_SH, "claude_limit_stop")],
          "invoke resume",
          "test_dispatcher_allowance_invoke.py test_dispatcher_usage_limit.py test_dispatcher_usage_limit_pause.py"),
    _rule("evidence_retry_max", "How many times an unwritten evidence comment is retried before a blocker.", WRITE,
          [(WRITE, "DEFAULT_EVIDENCE_RETRY_MAX"), (INVOKE, "EVIDENCE_RETRY_MAX")],
          "resume",
          "test_dispatcher_evidence_unwritable.py",
          "`write.py` clamps the value to at least 1 and falls back to 3 on an invalid value; the shell "
          "compares the raw value."),
)
