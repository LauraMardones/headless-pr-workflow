# Next-Step Comment Kinds

This is the inventory of every comment kind that asks the PO to act (Epic #566, issue #605). A comment kind is in scope when the flow waits for the PO. Each kind names the PO's next step in three parts: the command with its issue or PR number, where to run it, and what happens afterwards. The text comes from one shared source, `hpw next-step render` (`src/headless_pr_workflow/next_step.py`); a Blocked Declaration carries it in the `Next step:` field of `docs/PROJECT-STATUS.md` → Declaration format.

`tests/test_next_step_kinds_check.py` (the E4 check) reads this file. It fails when:

- a producer found in the repository by one of the markers below is not listed here, as a kind or as not in scope;
- a listed producer file does not exist;
- a `converted` kind's example lacks one of the three parts, read by the shared parser;
- a `pending` kind is not named in the check's pending list, or the pending list names a kind that is not `pending` here.

A story that converts a producer changes its status to `converted`, replaces the example with a rendered next step, and removes the kind from the check's pending list.

## Next-step text

The shared source writes one line in one of two forms, chosen by the place:

- a session command: ``Next step: run `/spec 418` in Claude Code or Codex; then the PO merges the spec PR and the next `/refine 545` continues.``
- a reply on GitHub: ``Next step: reply `/unblock` on #605 on GitHub; then the dispatcher resumes the story on its next run.``

The place is `Claude Code`, `Codex`, `Claude Code or Codex`, or `a reply on GitHub`. A reply is not run in a session, so it is not joined with a session place. A `Type: decision` declaration writes one `Next step <letter>:` line per option. The text never contains the Codex review trigger phrase (ADR-006); it names the command that posts it.

## Markers

The check finds producers by these markers. It scans `scripts/`, `src/` and `.claude/commands/`:

- `blocker`: a string literal that starts a comment with `## Blocked Declaration` in `scripts/` or `src/` (the heading after a quote, followed by an escaped or real line end), or a command file in `.claude/commands/` that mentions `Blocked Declaration` or `Blocked Status Protocol`. Readers that only test for the heading do not match.
- `next-action`: a line containing `Next action:`.
- `slack:<event>`: an event of the `case` in `scripts/slack-notify.sh`.
- `closure-request`: a line containing `Reply with exactly "yes"`, the `/verify-closure` request for the PO's confirmation.

A producer written in another way is not found. When you add one, add its marker here and in the check.

## Kinds

| Kind | Marker | Producer | Feature | Status | Example |
|---|---|---|---|---|---|
| dispatcher-conflict-blocker | blocker | `scripts/dispatcher-invoke.sh` | #589 | converted | ``Next step: run `/implement 606` in Claude Code or Codex; then the story starts once the stated condition holds.`` |
| dispatcher-mid-cycle-blocker | blocker | `scripts/dispatcher-invoke.sh` | #589 | converted | ``Next step: run `/implement 606` in Claude Code or Codex; then the PO replies /unblock on #606 and the dispatcher resumes the story.`` |
| dispatcher-fix-round-max-blocker | blocker | `scripts/dispatcher-invoke.sh` | #589 | converted | ``Next step: run `/implement 606` in Claude Code or Codex; then the PO replies /unblock on #606 and the dispatcher continues (or raises CODEX_FIX_ROUND_MAX first).`` |
| dispatcher-merge-owner-blocker | blocker | `scripts/dispatcher-invoke.sh` | #589 | converted | ``Next step: run `/merge 900` in Claude Code or Codex; then the PO runs /cleanup 900 (Merge Takeover).`` |
| bookkeeping-resource-blocker | blocker | `src/headless_pr_workflow/codex_review_bookkeeping.py` | #589 | converted | ``Next step: reply `/unblock` on #607 on GitHub; then the job acts on the next Codex result for the story once the cause named under Unblocked when is gone.`` |
| bookkeeping-external-blocker | blocker | `src/headless_pr_workflow/codex_review_bookkeeping.py` | #589 | converted | ``Next step: reply `/unblock` on #607 on GitHub; then the job acts on the next Codex result for the story once the cause named under Unblocked when is gone.`` |
| codex-worker-fix-round-blocker | blocker | `src/headless_pr_workflow/dispatcher/codex_worker.py` | #589 | converted | ``Next step: reply `/unblock` on #607 on GitHub; then the worker may start the next fix round once CODEX_FIX_ROUND_MAX is raised, or the new head gets its /review once the findings are fixed on the PR branch.`` |
| refine-decision-blocker | blocker | `.claude/commands/refine.md`, `.claude/commands/refine-epic.md`, `.claude/commands/refine-feature.md`, `.claude/commands/refine-story.md`, `.claude/commands/refine-task.md`, `.claude/commands/refine-bug.md` | #589 | converted | ``Next step a: run `/spec 418` in Claude Code or Codex; then the PO merges the spec PR and the next /refine 545 continues.`` |
| implement-decision-blocker | blocker | `.claude/commands/implement.md` | #589 | converted | ``Next step a: reply `/unblock` on #608 on GitHub; then the dispatcher resumes /implement 608 with the answer.`` |
| implement-conflict-blocker | blocker | `.claude/commands/implement.md` | #589 | converted | ``Next step: reply `/unblock` on #608 on GitHub; then the dispatcher resumes /implement 608 once the WIP slot or the files are free.`` |
| implement-other-blocker | blocker | `.claude/commands/implement.md` | #589 | converted | ``Next step: reply `/unblock` on #608 on GitHub; then the dispatcher resumes /implement 608 once the prerequisite is done.`` |
| implement-waiver-blocker | blocker | `.claude/commands/implement.md` | #589 | converted | ``Next step b: run `/implement 608` in Claude Code or Codex; then the findings are fixed in this story after /unblock with B chosen.`` |
| review-decision-blocker | blocker | `.claude/commands/review.md` | #589 | converted | ``Next step a: reply `/unblock` on #620 on GitHub; then the dispatcher resumes /review 620 with the answer.`` |
| review-waiver-blocker | blocker | `.claude/commands/review.md` | #589 | converted | ``Next step b: run `/implement 620` in Claude Code or Codex; then the findings are fixed in this story after /unblock with B chosen.`` |
| slack-po-blocker | slack:po_blocker | `scripts/slack-notify.sh` | #589 | converted | ``Next step: run `/implement 606` in Claude Code or Codex; then the story starts once the stated condition holds.`` |
| slack-decision-blocker | slack:decision_blocker | `scripts/slack-notify.sh` | #589 | converted | ``Next step a: reply `/unblock` on #608 on GitHub; then the dispatcher resumes the story with the answer.`` |
| slack-failure-stop | slack:failure_stop | `scripts/slack-notify.sh` | #560 | converted | ``Next step: reply `/unblock` on #503 on GitHub; then the dispatcher starts sessions again on its next run.`` |
| refine-decision-summary | next-action | `.claude/commands/refine.md`, `.claude/commands/refine-epic.md`, `.claude/commands/refine-feature.md`, `.claude/commands/refine-story.md`, `.claude/commands/refine-task.md`, `.claude/commands/refine-bug.md` | #590 | converted | Next step: reply `/unblock` on #545 on GitHub; then the dispatcher's next run starts `/refine 545`. |
| refine-scope-answer-summary | next-action | `.claude/commands/refine.md`, `.claude/commands/refine-epic.md`, `.claude/commands/refine-feature.md`, `.claude/commands/refine-story.md`, `.claude/commands/refine-task.md`, `.claude/commands/refine-bug.md` | #590 | converted | Next step: run `/spec 418` in Claude Code or Codex; then the PO merges the spec PR and the next `/refine 545` resumes. |
| refine-spec-pr-open-summary | next-action | `.claude/commands/refine.md`, `.claude/commands/refine-epic.md`, `.claude/commands/refine-feature.md`, `.claude/commands/refine-story.md`, `.claude/commands/refine-task.md`, `.claude/commands/refine-bug.md` | #590 | converted | Next step: reply `review and merge the spec PR` on #640 on GitHub; then the next `/refine 545` resumes. |
| implement-session-summary | next-action | `scripts/session-summary.sh` | #590 | converted | Next step: reply `/unblock` on #608 on GitHub; then the dispatcher resumes /implement 608 with Decision A. |
| review-session-summary | next-action | `scripts/session-summary.sh` | #590 | converted | Next step: reply `/unblock` on #620 on GitHub; then the dispatcher resumes /review 620 with Decision A. |
| verify-closure-request | closure-request | `.claude/commands/verify-closure.md` | #591 | pending | the technical-evidence comment that asks the PO for "yes" or "no" |
| slack-evidence-unwritable | slack:evidence_unwritable | `scripts/slack-notify.sh` | #591 | pending | `tests/test_slack_notify_evidence_unwritable.sh` |
| slack-feature-closure-confirmation | slack:feature_closure_confirmation | `scripts/slack-notify.sh` | #591 | pending | the request to confirm a Feature closure |
| slack-epic-closure-approval | slack:epic_closure_approval | `scripts/slack-notify.sh` | #591 | pending | the request to approve an Epic closure |
| slack-dispatch-trigger-stopped | slack:dispatch_trigger_stopped | `scripts/slack-notify.sh` | #591 | pending | "Dispatcher trigger stopped" (`tests/test_slack_notify_large_context.sh`) |
| slack-dispatcher-error | slack:dispatcher_error | `scripts/slack-notify.sh` | #591 | pending | "Dispatcher Error" with the last action |
| slack-red-flow-health | slack:red_flow_health | `scripts/slack-notify.sh` | #591 | pending | "Red Flow Health Signal" with the WIP and blocked counts |

## Not in scope

These producers match a marker, but the flow does not wait for the PO.

| Marker | Producer | Reason |
|---|---|---|
| next-action | `scripts/pr-context` | terminal output of a session tool, not a GitHub comment (Feature #590 → Out of scope) |
| next-action | `src/headless_pr_workflow/cli/__init__.py` | terminal output of `hpw next-action`, not a GitHub comment (Feature #590 → Out of scope) |
| next-action | `.claude/commands/cleanup.md` | a cleanup Session Summary hands no step to the PO (Feature #590 → Decisions) |
| slack:usage_limit_reached | `scripts/slack-notify.sh` | the dispatcher resumes by itself at 00:00 UTC; nothing waits for the PO |
| next-action | `.claude/commands/refine.md`, `.claude/commands/refine-epic.md`, `.claude/commands/refine-feature.md`, `.claude/commands/refine-story.md`, `.claude/commands/refine-task.md`, `.claude/commands/refine-bug.md` | `Next action: Ready for implementation` and `Next action: Refined; blocked by #X` of a refine Session Summary: the next step is the flow's, not the PO's (issue #612) |
| next-action | `.claude/commands/merge.md` | a merge Session Summary hands no step to the PO: `scripts/session-summary.sh` rejects `--next-step` for `merge` (issue #613) |
| next-action | `scripts/dispatcher-merge.sh`, `scripts/dispatcher-cleanup.sh`, `src/headless_pr_workflow/codex_review_bookkeeping.py` | the merge, cleanup and bookkeeping move Session Summaries call `scripts/session-summary.sh` without `--next-step`: the flow does not wait for the PO (issue #613) |
