# Headless PR Workflow

This document defines the normative workflow for assistant-agnostic, GitHub-centered pull request work.

## Non-Negotiable Invariants

- GitHub is the system of record for issues, pull requests, reviews, approvals, CI status, blockers, and merge state.
- Local branches, worktrees, editor state, terminal history, and assistant chat sessions are disposable execution contexts.
- A review approval applies only to the exact PR head SHA that was reviewed.
- If new commits are pushed after approval, approval must be re-evaluated against the new head SHA.
- Implementation and review must not happen in the same session for the same reviewed head SHA.
- Merge is allowed only after a fresh GitHub refresh confirms current head SHA, approvals, CI, blockers, target branch, and merge owner.
- Takeover between assistants, machines, and sessions must be recoverable from GitHub state plus explicit handoff notes.
- Repo-specific constraints must be implemented through adapters, not core workflow policy.
- Assistant-specific behaviors must be optional integrations, not normative requirements.

## Lifecycle

The workflow loops until the PR is either merged or intentionally abandoned:

1. Issue or task context is selected from GitHub.
2. An implementation session creates or takes over a branch/worktree.
3. Implementation produces commits and pushes them to the PR branch.
4. Review readiness checks run against the current GitHub PR state.
5. A separate review session reviews the current head SHA.
6. Blockers are recorded in GitHub review threads, comments, checks, or issue/PR metadata.
7. If blockers require changes, a new implementation loop pushes a new head SHA.
8. Approval is re-evaluated after every new push.
9. Merge readiness checks run after a fresh GitHub refresh.
10. The merge owner merges only the approved current head SHA.
11. Post-merge sync and cleanup are performed.

## State Model

The workflow distinguishes between durable and temporary state.

Durable state:

- GitHub issue state.
- GitHub PR state.
- PR head SHA.
- Reviews and approvals.
- Review threads and unresolved blockers.
- CI checks and check suites.
- Merge commit and merged branch state.
- Explicit handoff notes stored in GitHub or committed workflow metadata.

Temporary state:

- Local branch checkout.
- Worktree path.
- Assistant session memory.
- Terminal scrollback.
- Local scratch files.
- Unpushed commits.

Temporary state can help execution, but it must not be required to determine PR truth.

## Review Separation

For a reviewed head SHA, the same assistant session must not both implement and approve/review that SHA. A session may implement fixes after review, but the resulting new head SHA requires review by a separate review session.

This rule prevents self-review loops where an assistant validates its own implementation without an independent review context.

## Independent Review Flow

The approval gate is satisfied when a reviewer independent of the implementer found no blockers on the exact current head SHA (ADR-007). One maintainer with one GitHub account is the normal case; the workflow preserves review separation without inventing extra GitHub accounts or pretending that a formal GitHub approval exists.

`MERGE-POLICY.md` → Approval Gate: Independent Review defines the three kinds of evidence and when each fails closed:

1. A clean Codex review of the current head (the default for Claude-implemented stories). It needs a linked story; a PR with no linked story uses path 2 or path 3.
2. A separate-session review recorded as a PR review summary on the current head, naming the reviewing provider, which must differ from the implementing provider.
3. A formal GitHub approval of the current head.

Satisfying the approval gate does not waive any other merge condition:

- The review must still come from a reviewer independent of the session that implemented the current head SHA.
- The evidence must still be recorded on GitHub for the exact current head SHA.
- The PR must still be Ready for review, not draft.
- Required CI/checks must still pass for the current head SHA.
- Blocking review threads or comments must still be resolved, outdated, or explicitly waived according to repo policy.
- The merge owner must still perform a fresh GitHub refresh immediately before merge.

Practical sequence:

1. The implementation session pushes the candidate head SHA and requests review without self-approving that SHA.
2. An independent reviewer reviews the exact current head SHA: Codex posts its result on the PR, or a separate review session refreshes GitHub state and records blockers or a no-blockers PR review summary.
3. A separate review session that finds no blockers records the review summary described in `MERGE-POLICY.md` → Path 2, including the `Reviewer provider:` line, or a formal approval when GitHub allows it. The session generates the summary text with `hpw review-evidence-text` instead of composing it, and confirms with `hpw approval-check` that it was accepted.
4. If the review finds blockers, the implementation session makes changes and pushes a new head SHA. The earlier review is then stale and the loop restarts.
5. After a no-blockers review exists for the current head SHA, the merge owner performs a fresh refresh and re-checks merge readiness before merging that same SHA.

A separate-session review summary makes three facts explicit:

- No blockers remain for the exact current head SHA.
- Which provider reviewed it.
- The review is the approval to rely on for that current head SHA.

The evidence surface for a separate-session review is the GitHub PR review submission summary attached to the reviewed head SHA. The evidence surface for a Codex review is the Codex bot's PR conversation comment naming the reviewed commit. No other top-level issue or PR conversation comment is a source of approval evidence.

This keeps the flow compatible with deterministic commands:

- `approval-check` treats evidence as satisfying the approval gate only when its recognizer accepts it for the current head SHA, and reports the source (`formal`, `codex-review` or `separate-session-review`) and the reason for any rejection.
- `pr-context` normalizes review submissions from `latestReviews` and `reviews`, and reads the PR timeline, commits and linked stories the recognizers need. When these cannot be read completely, the Codex review and separate-session review paths fail closed.
- `pre-merge` must still fail if the PR is draft, if checks are missing or failing, if mergeability is bad, if unresolved review threads remain active, or if the reviewed SHA is no longer current.

### Codex review loop

For a Claude-implemented story (`executor:claude-code-*`), the review is requested and followed up without a review session (ADR-006):

1. **Request on ready.** `/implement` opens the PR as a draft and pushes freely. When it marks the PR Ready for review, it posts one PR conversation comment that starts with the Codex review trigger phrase from ADR-006 → Decision. After a round of fixes for Codex findings it posts exactly one new request, after the round's last push and after the story is back in `In review`. It posts no request on intermediate pushes, none on an issue, and none for an `executor:codex` story.
2. **The bookkeeping job.** `.github/workflows/codex-review-bookkeeping.yml` runs `hpw codex-review-bookkeeping` when the Codex bot comments on or reviews a PR, every 15 minutes for every open, non-draft PR, and on manual dispatch. It calls no model. An event only names the PR: the job reads the PR fresh from GitHub and decides from current facts, so a redelivered event is a no-op. It runs with the `PROJECT_TOKEN` secret; the scopes that token needs are listed in [DISPATCHER-CONFIG.md → GitHub Token — `PROJECT_TOKEN`](DISPATCHER-CONFIG.md#github-token--project_token).
3. **When it acts.** Only when the PR is open and not a draft, it has exactly one linked story with exactly one `executor:` label starting with `executor:claude-code-`, and that story's board status is exactly `In review`. Otherwise it does nothing and reports why.

"For the current head" means after the last commit or force-push in the PR timeline.

| Situation on the current head | Action |
|---|---|
| Clean Codex review accepted by the approval gate, required checks green, no unresolved review thread other than superseded Codex threads | Resolve the superseded Codex threads; post the Session Summary (`Next: merge`) listing them; move the story `In review` → `In merge`. |
| Clean, required checks green, but an unresolved thread the job may not resolve (a human's thread, a Codex thread on the current head, or a Codex thread whose review commit cannot be read) that the review-thread gate blocks on | Resolve nothing, do not move the story; one `Type: external` blocker on the story naming each remaining thread and why. |
| Clean, but a reviewer requested changes | Nothing. The approval gate does not pass, and that reviewer owns the next step. |
| Clean, a required check still running | Nothing; the scheduled run looks again. |
| Clean, a required check failed | Post the Session Summary naming the check (`Next: implementation`); move `In review` → `In implementation`. |
| Codex review with findings | Post the Session Summary linking the review (`Next: implementation`); move `In review` → `In implementation`. |
| Usage-limit reply, or a request with no answer | Nothing until the back-off has passed, then one new review request. |
| No request for the current head | One review request. |
| Reply in an unknown format | No board move; one `Type: external` blocker on the story naming the reply. |
| The job has posted its maximum number of requests for the head and another is due | One `Type: resource` blocker on the story; no further request for that head. |

Rules:

- **Progress comment.** Codex keeps one "Codex Review Summary" comment up to date on the PR. It is not a reply: a request stays pending while it says a review is running.
- **Bounds.** Two repository variables set the bounds, so the PO changes them without a code change: `CODEX_REVIEW_RETRY_HOURS` (back-off, default `5`, the Codex usage window) and `CODEX_REVIEW_MAX_REQUESTS` (requests the job posts per head, default `3`).
- **Blockers** use the Declaration format in `PROJECT-STATUS.md` and are posted on the story. The board has no `Blocked` option, so the story stays in `In review`. No Slack notification is sent for them.
- **Session Summary.** The job posts the exact output of `scripts/session-summary.sh --command review` as a PR comment. Its `Deviation:` line says the bookkeeping job produced it without a model session.
- **Idempotency.** Every comment the job posts ends with a hidden marker naming the PR head and the kind of action. The job skips anything already done for that head. All state lives on GitHub.
- **Review threads.** On a clean result accepted by the approval gate with green required checks, the job reads all review threads of the PR completely. It resolves an unresolved thread only when the Codex bot opened it (first comment by the bot account, type `Bot`; replies never change this) in a review of a known commit other than the current head, whether or not GitHub shows it as outdated. A thread opened by any other account, a Codex thread from a review of the current head, and a Codex thread whose review commit cannot be read are never resolved. The order of mutations is: resolve the threads, post the Session Summary, move the story. The Session Summary lists each resolved thread with its link, the commit of the Codex review that opened it and the clean Codex result that superseded it, or says no Codex threads were resolved; after a partial failure the next run finishes the rest and the single Session Summary also lists the superseded Codex threads already resolved. The blocker is unblocked when the listed threads are resolved on GitHub or a new head is pushed; the next run then moves the story. This is resolution on Codex's own later evidence, not a waiver ([MERGE-POLICY.md](MERGE-POLICY.md#path-1-clean-codex-review)); the review-thread gate of `/merge` is unchanged.
- **Fresh refresh.** Nothing is posted or moved on the strength of one read. Immediately before the first mutation the job reads the PR, the story status, the required checks, the review threads (when read) and the story comments again and decides again. It acts only when both readings lead to the same decision; otherwise it stops with an error and changes nothing, and the next run decides for the new state. A change in the instant after the second read cannot be excluded by any job; `/merge` refreshes every gate again before merging.
- **Failure.** When PR, story or board state cannot be read completely, a board status option is missing, or a mutation fails, the command exits non-zero, names the failed step and claims no action it did not complete. When GitHub refuses a read or write because the token lacks a scope, the error names the missing scope and the token section linked above, for example ``PR #<n>: failed at step `read PR`: the GitHub token lacks a required scope: `read:org` …``. A missing scope is a failure like any other: the PR is never skipped or reported as "nothing to do".
- **Command not on the default branch yet.** The job runs the default branch's code. On the PR that introduces the command, the default branch does not have it, so the job skips with a notice and exits successfully instead of failing a check on that PR.
- **Trigger phrase.** The phrase is written only in a PR conversation comment that requests a review, by `/implement` or by this job (`AGENTS.md` → Codex Review Requests).

`/review` remains the path for `executor:codex` stories and for a separate-session review (path 2).

## Deterministic Automation

Scripts should cover facts and gates that can be determined mechanically:

- Current PR head SHA.
- Latest relevant approval SHA.
- Whether new commits exist after approval.
- CI/check status for the current SHA.
- Unresolved review threads.
- Target branch policy.
- Merge ownership.
- Worktree and branch hygiene.

Humans and assistants should focus on interpretation, architecture, UX, product judgment, risk assessment, and decisions that cannot be reduced to deterministic checks.

## Refinement Pipeline

Issue refinement follows the rolling, just-in-time strategy defined in [`REFINEMENT-PIPELINE.md`](REFINEMENT-PIPELINE.md). That document specifies buffer targets, the usage-feedback loop, parent-linking requirements, and escalation triggers.

## Extension Model

Core workflow policy defines what must be true. Adapters define how a specific repo or assistant satisfies optional local conventions.

- Core workflow is reusable across repositories.
- Repo adapters encode project-specific checks.
- Assistant adapters encode optional prompt and session conventions.
- No adapter may weaken core approval, review separation, or merge policy.
