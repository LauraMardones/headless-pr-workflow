# Dispatcher `/merge` and `/cleanup`: step-to-script map

Issue #264. The dispatcher (`scripts/dispatcher-invoke.sh`) runs `/merge` and
`/cleanup` as deterministic scripts: no model session, no allowance check, no
executor budget. This document maps every numbered step of the command files to
the script branch that implements it, and to the test that covers it.

`.claude/commands/merge.md` and `.claude/commands/cleanup.md` are unchanged and
still govern manual runs. They stay the source of truth for the port: **a later
edit to either file needs the matching change in the script and test below.**

Where a step needs a judgment a script cannot make, the script treats the case
as ambiguous, keeps everything and reports it (fail closed).

## `/merge` → `scripts/dispatcher-merge.sh --pr N --repo R`

Exit `0`: merged (or already merged). Exit `1`: blocked or failed, nothing more
was mutated than the step reached. Exit `2`: invalid invocation.

| `merge.md` step | Script branch | Helper reused | Test (`tests/test_dispatcher_merge_script.py`) |
|---|---|---|---|
| Decision documents (ADR-011) | PR files match `specs/<n>-<slug>/` or `docs/decisions/ADR-*.md` → exit 1, "the PO merges it", no mutation | `gh api pulls/N/files` | `test_decision_document_pr_is_refused` |
| GraphQL pre-flight | `gh api graphql` first; failure → exit 1, no mutation, no comment | `gh` | `test_graphql_preflight_failure_makes_no_mutation` |
| Merge ownership (`docs/MERGE-POLICY.md`) | `hpw merge-owner`; expected owner is supplied only when the dispatcher's own account posted the `merge-owner` marker for this PR and head right after its `/implement` session (`record_merge_owner` in `dispatcher-invoke.sh`); a manual `/implement` handoff or another commenter's comment is not evidence; otherwise unknown owner → exit 1, no merge. A failed marker write is left as an evidence-pending comment and posted before the merge for exactly that head (`ensure_merge_owner`, #348); when the pending copy cannot be written either (3 attempts), the run exits 1 before the hand-off (`dispatcher_error`) instead of advancing without ownership. Takeover is never assumed | `hpw merge-owner` | `test_owner_evidence_for_the_current_head_is_passed_to_merge_owner`, `test_only_the_dispatcher_owner_marker_for_this_head_is_owner_evidence`, `test_implement_session_is_followed_by_the_merge_owner_marker_on_the_pr`, `test_missing_owner_evidence_for_the_head_blocks_before_the_merge`, `test_ownership_gate_failure_blocks_even_with_evidence`, `test_348_ac1_pending_owner_marker_is_posted_for_the_current_head_before_the_merge`, `test_348_owner_marker_and_pending_copy_both_failing_stops_before_the_hand_off` |
| 1 fresh refresh | `gh api pulls/N` read immediately before the gate | `gh` | all |
| 2 verify head, review state, checks, target branch, blockers, draft | draft or not-open PR → exit 1; the rest is evaluated by the gate in step 5 | `scripts/merge-gate-summary` | `test_draft_pr_is_not_merged`, `test_closed_unmerged_pr_is_not_merged` |
| 3 review evidence in the right surfaces | owned by `merge-gate-summary`; not re-derived | `scripts/merge-gate-summary` | gate stub in each test |
| 4 approval sources | owned by `merge-gate-summary` (`approval=fail(...)` blocks) | `scripts/merge-gate-summary` | `test_gate_exit_1_blocks`, `test_gate_exit_2_blocks` |
| 5 gate verdict, exact `Merge gate:` line | exit `0` continues; `1` and `2` block; the line is kept and posted | `scripts/merge-gate-summary` | `test_gate_exit_0_merges_and_comments`, `test_gate_exit_1_blocks`, `test_gate_exit_2_blocks` |
| 5 head SHA must still apply | gate `head=` must prefix the refreshed head, and `hpw merge-pr` re-reads and passes the head SHA to the merge | `hpw merge-pr --execute` | `test_head_changing_after_the_gate_blocks` |
| 6 stale, missing, ambiguous or failing gate | exit 1, no merge. The dispatcher leaves the story in `In merge` (one attempt per head, #293) instead of writing `In implementation` / `In review` | n/a | `test_gate_exit_1_blocks`, `tests/test_dispatcher_review_loop.py` |
| 7 dirty or unrelated local state | the merge never touches the checkout; step 11 only deletes a branch from a clean checkout | n/a | `test_dirty_checkout_keeps_the_local_branch` |
| 8 do not rely on local branch state | the decision uses GitHub only | n/a | all |
| 9 merge, confirm merged state, set `Done` | `hpw merge-pr --execute --method squash`; merged state read back from its post-merge verification; `Done` is written by `finish_story` | `hpw merge-pr` | `test_gate_exit_0_merges_and_comments`, `test_merge_command_failure_exits_1` |
| 10–11 delete the local topic branch only when merged, not checked out, unused by another worktree, clean | `git branch -d` (never `-D`) after those four checks; a missing branch is not an error | `git` | `test_merged_local_branch_is_deleted`, `test_checked_out_branch_is_kept`, `test_branch_used_by_other_worktree_is_kept`, `test_dirty_checkout_keeps_the_local_branch` |
| 12–13 no worktree removal, no remote branch deletion | not implemented here (left to cleanup) | n/a | `test_merged_local_branch_is_deleted` |
| 14–15 never force; unsafe or squash-ambiguous → report | `git branch -d` refusing is reported as "needs a cleanup session" and is not a failure | `git` | `test_unmerged_local_branch_is_kept_not_forced` |
| Output: merge confirmation comment | PR comment: exact `Merge gate:` line, then `scripts/session-summary.sh --command merge --next cleanup`; 3 attempts, a failed post exits 1 with the text logged and leaves it as an evidence-pending comment that a later run posts once, before cleanup; a merged PR with no dispatcher confirmation at all also holds cleanup, and after the give-up blocker it waits for `/unblock` without counting further failures (`settle_merge_confirmation`, #348) | `scripts/session-summary.sh` | `test_gate_exit_0_merges_and_comments`, `test_confirmation_comment_failure_fails_the_phase_with_the_text_logged`, `test_failed_confirmation_leaves_the_exact_text_as_a_pending_comment`, `test_348_ac2_pending_merge_confirmation_is_posted_once_before_cleanup`, `test_348_missing_confirmation_records_no_failure_after_the_give_up_blocker` |

## `/cleanup` → `scripts/dispatcher-cleanup.sh --issue N --repo R`

The story is already `Done` when this runs. Exit `0`: finished, possibly with
kept items and manual commands in the report. Exit `1`: a step failed or the PR
is not merged; the dispatcher logs a warning and continues. The script never
fails because something was *kept*.

| `cleanup.md` step | Script branch | Helper reused | Test (`tests/test_dispatcher_cleanup_script.py`) |
|---|---|---|---|
| 1 confirm merged from GitHub | closed PR with `merged_at` that closes the issue (or `--pr N`); none → exit 1, nothing touched | `gh api pulls` | `test_unmerged_pr_touches_nothing`, `test_no_merged_pr_touches_nothing` |
| 2 identify branch, SHAs, base, worktrees, status | read by the two helpers below and included in the report | `hpw post-merge-sync`, `hpw branch-cleanup` | `test_report_names_what_was_done` |
| 3, 4 dirty base is not a stop | the sync helper classifies instead of stopping | `hpw post-merge-sync` | `test_stale_copy_is_restored_then_fast_forwarded` |
| 5 stale copy: backup, restore PR paths, fast-forward, verify, remove backup | `hpw post-merge-sync --execute` runs it only for the `already_synced`, `safe_fast_forward` and `verified_stale_pr_copy` classifications | `hpw post-merge-sync` | `test_stale_copy_is_restored_then_fast_forwarded` |
| 6 unrelated, ambiguous or unique work | blocked classification: no sync, nothing restored, safe next step reported (fail closed) | `hpw post-merge-sync` | `test_unrelated_dirty_base_is_left_alone` |
| 7–8 branch checked out, used by another worktree, dirty worktree | candidate kept with a reason | `hpw branch-cleanup` | `test_kept_branch_is_reported_not_failed` |
| 9–10 squash merge: compare effective content; delete only when merged or content-verified | candidate disposition decided by the helper | `hpw branch-cleanup` | `test_kept_branch_is_reported_not_failed` |
| 11 remote branch only when GitHub confirms containment | same helper, GitHub compare | `hpw branch-cleanup` | `test_report_names_what_was_done` |
| 12–14 worktree only when clean, unused, tied to the story, not current; never force | same helper (it never forces) | `hpw branch-cleanup` | `test_kept_branch_is_reported_not_failed` |
| 15 blocked step: exact safe command | the helpers' `manual_commands` are printed in the report | both helpers | `test_unrelated_dirty_base_is_left_alone` |
| 16 end-of-run report | deleted, kept, restored/fast-forwarded, why, manual follow-up printed to stdout | both helpers | `test_report_names_what_was_done` |
| Output: closing comment on the issue | `scripts/session-summary.sh --command cleanup --next done` posted on the issue (3 attempts; a failed post exits 1 with the text logged and leaves it as an evidence-pending comment and labels the story `evidence:pending`, so a later run finds it however old it is (the workflow's poll step counts the label as resumable work) and posts it once, also when the story is already Done; if the pending or the label write fails too the story returns to `In merge` so cleanup reruns, `finish_story`, #348) | `scripts/session-summary.sh` | `test_report_names_what_was_done`, `test_closing_comment_failure_fails_the_phase_with_the_text_logged`, `test_failed_closing_comment_leaves_the_exact_text_as_a_pending_comment`, `test_348_ac3_pending_cleanup_summary_is_posted_once_on_a_done_story`, `test_348_ac3_pending_cleanup_summary_is_recovered_however_old_it_is`, `test_348_ac3_cleanup_that_left_the_pending_copy_labels_the_done_story_for_a_later_run`, `test_348_ac3_pending_copy_without_the_label_goes_back_to_in_merge`, `test_348_workflow_resumes_for_a_story_labelled_evidence_pending` |

## Dispatcher wiring

`run_merge_or_block()` and `finish_story()` call the two scripts directly
(`MERGE_SCRIPT` and `CLEANUP_SCRIPT` can be overridden for tests). They call
neither `invoke_executor_command()`, nor `scripts/dispatcher-allowance.sh`, nor
`scripts/dispatcher-budget.sh`. The same two functions serve the Claude-tier
cycle, the paired `executor:codex` run and the resume pass at `In merge`.
