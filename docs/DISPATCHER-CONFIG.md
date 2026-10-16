# Dispatcher Configuration Reference

This document covers all configurable variables for the headless PR workflow dispatcher.

---

## Usage limit

The PO caps how many autonomous sessions the dispatcher starts per UTC day with one repository variable (issue #320, ADR-012). It replaces the four daily token caps (`BUDGET_DAILY_*`), the token estimates per story size and the `budget_cap_reached` notification.

| Variable | Valid values | Suggested first value |
|---|---|---|
| `USAGE_LIMIT_SESSIONS_PER_DAY` | A whole number of zero or more, in decimal digits only (`08` is eight). `0` means no session starts. | `10` |

**Change it** under **Settings → Secrets and variables → Actions → Variables** (`https://github.com/<owner>/<repo>/settings/variables/actions`). No code change is needed; the new value applies on the next poll that finds work.

**What counts.** Every model session the dispatcher starts counts once, when it starts: `/implement` (including a fix round), `/review` of a Codex-implemented story, and refinement sessions that use the same start function. `/merge` and `/cleanup` are script-only phases (issue #264): they start no model session, so they neither count nor can be stopped by the limit. One full cycle of a Codex-implemented story therefore uses 2 sessions (`/implement` and `/review`), and a Claude-implemented story uses 1 per `/implement` or fix round; size the limit with that in mind. A session that fails, times out or is stopped by a provider limit still counts, so a loop of failing restarts reaches the limit. The allowance probe (#317) does not count, and a start refused by the reserve does not count. Codex Cloud review (ADR-006) is not started by the dispatcher and does not count.

**Where.** `invoke_executor_command()` in `scripts/dispatcher-invoke.sh` checks the limit first and the allowance second, so no probe turn is spent when the limit already stops the session. `scripts/dispatcher-usage-limit.sh` holds the count (`check`, `record`, `status`, `notify-due`, `mark-notified`).

**UTC-day reset.** `.dispatcher-usage/sessions` holds `YYYY-MM-DD:<count>`; a file dated another day reads as 0, and the cache key `session-state-YYYY-MM-DD-<run id>` changes each UTC day. The entry also holds the allowance hold. A lost or evicted cache entry reads as a count of 0, so the limit can be exceeded after a cache loss. The workflow's "Save session state cache" step runs with `always()`, so a failed invoke step is still counted. Caches are scoped by ref, so only scheduled runs and manual runs on the default branch start sessions: a manual `workflow_dispatch` on any other ref runs `dispatcher-invoke.sh` with `--dry-run`, so it neither starts nor counts a session that the scheduled runs could not see.

**Log lines.**

```
[USAGE LIMIT] 3 of 10 sessions today — start
[USAGE LIMIT SKIP] #<N>: /<step> not started; 10 of 10 sessions today
[USAGE LIMIT ERROR] USAGE_LIMIT_SESSIONS_PER_DAY is not set or is not a whole number of zero or more (value: "<value>") — starting nothing
```

**When the limit stops a session** the story is left where a refusal by the reserve leaves it, the run exits `0` and starts no other session (it does not go on to the next story): before `/implement` the story goes back to "Ready for implementation"; before a fix round it keeps its place in "In implementation" and nothing is written; before `/review` one comment starting `## Usage limit` names the step, `N of M sessions today` and the reset time (00:00 UTC), and is not posted again while it is the story's newest comment for that step. Nothing restarts `/review` until #316; run it by hand after the reset.

**Notification.** The first stop of a UTC day sets the invoke step output `usage_limit_reached=true`. The workflow step "Notify usage limit reached" runs in the same run, sends one `usage_limit_reached` Slack message (`sessions_today`, `limit`, `settings_url`: the count, the limit and a link to the variables page) and only then writes the day's marker. If the send fails, no marker is written and the next run tries again; a second stop on the same day after a successful send sends nothing. A full retry with a durable record is #295.

**Invalid value.** A missing, empty or invalid value (`abc`, `-1`, `7.5`, `10 `, `1e3`) starts no session. The run log has the `[USAGE LIMIT ERROR]` line, the job fails and no `dispatcher_error` Slack message is sent. Before `/implement` the story goes back to "Ready for implementation"; before `/review` it gets a `## Usage limit` comment.

`--dry-run` neither counts nor notifies and logs `[DRY RUN] Would check usage limit before /<step>`.

---

## WIP and queue limits

The PO sets every WIP and queue limit in repository variables (issue #358). No script holds a number or a default for them, so the dispatcher, the flow review and the docs cannot disagree.

| Variable | Valid values | Suggested value | Read by |
|---|---|---|---|
| `WIP_LIMIT_IMPLEMENTATION` | A whole number of one or more, in decimal digits only. The most stories in "In implementation" at the same time. | `2` | `scripts/dispatcher-invoke.sh` (Step 5), `scripts/flow-review.sh` (violation, red and yellow status, `WIP count: N/<limit>`) |
| `READY_BUFFER_MIN` | A whole number of zero or more. The fewest stories kept in "Ready for implementation". | `3` | the shared check; the #306 scheduler will use it |
| `READY_BUFFER_MAX` | A whole number, `READY_BUFFER_MIN` or more. The most stories kept in "Ready for implementation". | `5` | the shared check; the #306 scheduler will use it |

**Change them** under **Settings → Secrets and variables → Actions → Variables** (`https://github.com/<owner>/<repo>/settings/variables/actions`). No code change is needed; the new value applies on the next run. The workflow passes all three to the "Run dispatcher poll" step (the poll runs `flow-review.sh`) and the "Run dispatcher invoke" step.

**Board column limits are only visual.** A limit set on a column of the project board is read by nothing. The PO sets it by hand to match the variable.

**The shared check.** `scripts/lib/limits.sh` (`load_limits`) checks all three variables and is sourced by `dispatcher-invoke.sh` and `flow-review.sh`, so a missing or invalid buffer value is found on the first run, not when the #306 scheduler starts reading it. Any script that reads a limit uses it instead of its own check.

**Log lines.**

```
Active WIP count: 2 (limit: 3)
FAIL: WIP limit reached (3 >= 3)
WIP count: 2/3
[LIMITS ERROR] <VARIABLE> is not set or is empty — starting nothing
[LIMITS ERROR] <VARIABLE> is not a whole number (value: "<value>") — starting nothing
[LIMITS ERROR] WIP_LIMIT_IMPLEMENTATION must be 1 or more (value: "<value>") — starting nothing
[LIMITS ERROR] READY_BUFFER_MIN (<min>) is greater than READY_BUFFER_MAX (<max>) — starting nothing
```

**Unset or invalid value.** There is no fallback to `2`, `3` or `5`. When any of the three is unset, empty or invalid (`abc`, `0` for the WIP limit, `-1`, `2.5`, or a minimum above the maximum), `dispatcher-invoke.sh` starts nothing before it reads the board: no board status is written, no blocker comment is posted and no `dispatcher_error` Slack message is sent; the job fails with the `[LIMITS ERROR]` line, like `[USAGE LIMIT ERROR]`. `flow-review.sh` prints the error to stderr, exits `2` and prints no report or status. `dispatcher-poll.sh` does not hide it: it exits `2`. The dispatcher therefore stops until the PO sets all three variables.

**When the WIP limit is reached** the dispatcher posts a conflict blocker whose "Unblocked when" line names the configured limit (`Fewer than <limit> stories are in "In implementation"…`).

---

## Executor Secrets — `CLAUDE_CODE_OAUTH_TOKEN`

The dispatcher needs credentials to start the sessions of the story cycle. There is exactly **one** required secret, not one per `executor:` tier:

| Secret | Type | Covers |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Repository secret | All three Claude tiers (`executor:claude-code-haiku`, `-sonnet`, `-opus`): every Claude session runs Claude Code signed in with the PO's subscription (issue #314, ADR-012). The same secret signs in the allowance probe; see [Subscription token](#subscription-token--claude_code_oauth_token). |

One subscription token serves every Claude tier — Haiku, Sonnet and Opus are model aliases chosen per session, not separate credentials (issue #252 removed the per-tier secrets). The [usage limit](#usage-limit) is one number for all sessions, not per tier.

No metered model-provider API key is used (issue #315 removed the OpenAI path, the last one). The metered Anthropic API key is no longer used. Since issue #314 it is not read by any script and not wired into the workflow; delete the repository secret and revoke the key at the provider (#308 → PO steps). Every `ANTHROPIC_*` variable is removed from the environment Claude Code runs in, so a metered key can never be used even if one is set again.

Provision the secret at:

`https://github.com/<owner>/<repo>/settings/secrets/actions`

The secret must also be explicitly wired into `.github/workflows/dispatcher.yml`'s "Run dispatcher invoke" step `env:` block as `CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}`; it is the only model-provider secret that step names. GitHub Actions never auto-injects a secret into a step's environment — it only becomes visible if the workflow file explicitly references it via `${{ secrets.NAME }}`. This wiring was missing entirely prior to issue #252, so no executor secret of any name ever reached the dispatcher regardless of what was configured in Settings.

**If a secret is absent**, `invoke_executor_command()` in `scripts/dispatcher-invoke.sh` fails closed with `Error: Secret '<name>' is not set in the environment.` before starting the session, and the dispatcher posts a mid-cycle blocker comment on the story issue. A `CLAUDE_CODE_OAUTH_TOKEN` that is empty after whitespace removal counts as absent.

### Executor invocation mechanism

`invoke_executor_command()` resolves the provider and model from the `EXECUTOR_PROVIDER` and `EXECUTOR_MODEL` tables in `scripts/dispatcher-invoke.sh` (data-driven, same pattern as `EXECUTOR_ROUTING`) and loads the task prompt: the `.claude/commands/<slash_command>.md` file with `$ARGUMENTS` substituted — the same instructions a manually-run command follows. Then it starts the session by provider:

| Provider | Executor labels | Session |
|---|---|---|
| `claude-code` | `claude-code-haiku`, `-sonnet`, `-opus` | Claude Code on the runner, on the subscription (ADR-012, issue #314) |

`invoke_executor_command()` knows one provider, `claude-code`. An executor label with no `EXECUTOR_PROVIDER` row (`executor:codex` has none) returns non-zero with `Error: No provider/model entry for executor '<label>' in EXECUTOR_PROVIDER/EXECUTOR_MODEL.` and starts nothing. The dispatcher does not start `/implement` for `executor:codex` (no subscription mechanism, ADR-012): see [Review path](#review-path-issues-263-and-293).

This covers every model session the dispatcher starts: `/implement` (also the fix-round `/implement` of the resume pass) and `/review` of a Codex-implemented PR (under `claude-code-opus`). `/merge` and `/cleanup` are **script-only phases** (issue #264): the dispatcher runs `scripts/dispatcher-merge.sh` and `scripts/dispatcher-cleanup.sh` and starts no session for them, in the Claude-tier cycle, and the resume pass at `In merge`. They have no executor tier, no allowance check, no executor budget, and no wall-clock session bound. ADR-012's session-start mechanism does not cover them. The step-by-step map to `merge.md` and `cleanup.md` is in [DISPATCHER-MERGE-CLEANUP-MAP.md](DISPATCHER-MERGE-CLEANUP-MAP.md).

#### Claude Code sessions

The session runs in the repository checkout with `GH_TOKEN` inherited, so `git`, `gh` and the repository scripts work as in a manual session. It uses the Claude Code version pinned in the workflow (see [Pinned Claude Code version](#pinned-claude-code-version)), the same install the allowance probe uses. The exact command line, with the task prompt on stdin:

```
claude -p --output-format stream-json --verbose --permission-mode bypassPermissions \
  --strict-mcp-config --model <alias> --max-turns "$AGENT_MAX_TURNS"
```

- **Model aliases.** `EXECUTOR_MODEL` names `haiku`, `sonnet` and `opus`. The pinned Claude Code version decides which model each alias means, so a model change is a deliberate version bump.
- **Never `--bare`.** Bare mode ignores the OAuth token (ADR-012 → Candidate B).
- **Flags.** Checked on 2026-10-14 against Claude Code 2.1.295 and against the pinned 2.1.296: each flag above is accepted, and an unknown option is rejected. `--max-turns` is no longer listed in `claude --help` but is still accepted. No flag changed from the issue's command line. The AC19 proof run of #314 then ran two live sessions with these flags on the pinned 2.1.296, with the metered API secrets deleted: [run 38534590704](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/38534590704) (2026-10-14), both `success` with `apiKeySource none`.
- **Token and environment.** `scripts/lib/claude-session.sh`, shared with the allowance probe, removes every whitespace character from `CLAUDE_CODE_OAUTH_TOKEN` and every variable whose name starts with `ANTHROPIC_` from Claude Code's environment.
- **Bounds.** `--max-turns` is `AGENT_MAX_TURNS`. The session runs under `timeout` set to the time left of `AGENT_MAX_WALLCLOCK_SECONDS` since `DISPATCH_JOB_START_TS`. When no time is left, the session does not start.
- **Tools.** `--permission-mode bypassPermissions` allows any command, as the API loop's `bash` tool did. Isolation comes from the fresh GitHub-hosted runner (ADR-012 → Consequences).

The stream-json output is written to a temporary file, parsed by message type (not line order) after the session ends, and deleted before the step returns. The step succeeds only when the stream has a `result` message with `subtype` `success` and `is_error` `false`, and Claude Code exited 0; the `result` text then goes to stdout. Every other outcome fails closed (non-zero return, then the existing mid-cycle blocker or decision-blocker path):

| Outcome | Log line (stderr) |
|---|---|
| Wall clock used up before the start | `[SESSION ERROR] /<command> #<n> not started: no time left of AGENT_MAX_WALLCLOCK_SECONDS ...` |
| `timeout` stopped the session | `[SESSION ERROR] /<command> #<n> stopped by the wall-clock bound ...` |
| Token rejected (`401`) | `[SESSION ERROR] /<command> #<n>: the subscription token was rejected (401). Run 'claude setup-token' again and save the CLAUDE_CODE_OAUTH_TOKEN secret with nothing before or after the token` |
| `init` reports an `apiKeySource` other than `none` (or none at all) | `[SESSION ERROR] apiKeySource=<value>; only the subscription token may be used` |
| A provider usage limit (`rate_limit`) | `[SESSION RATE LIMIT] /<command> #<n> stopped at a provider usage limit; resume is #316` |
| Claude Code exits non-zero | `[SESSION ERROR] /<command> #<n>: Claude Code exited with code <rc>`, then the last five lines of its stderr |
| No `result` message | `[SESSION ERROR] /<command> #<n>: the stream has no result message` |
| `subtype` other than `success` (for example `error_max_turns`), or `is_error` `true` | `[SESSION ERROR] /<command> #<n>: result subtype <subtype>, is_error <bool>; not treating as success` |

After every session the job log also has one line per tool call and, when the stream has a `result`, one summary line:

```
[session tool] Bash: git status
[SESSION] /implement #314 on opus: success, 11 turns, 45 s, apiKeySource none, input 120345 / output 8123 tokens
```

- A tool line shows the tool name and the first 200 characters of its input: the `command` for `Bash`, the input as JSON for other tools, line breaks replaced by spaces.
- `input` is the sum of `input_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens` from the result's `usage`; `output` is `output_tokens`.
- The token, the full stream and the `init` message (which carries account fields) are never printed. The token value is masked as `[redacted]` in tool lines, in the result text and in the stderr lines shown after a failed exit.

Under `--dry-run` a Claude-provider call logs `[DRY RUN] Would start Claude Code (<alias>) on the subscription for /<command> <target>` and starts nothing.

See [ADR-012](decisions/ADR-012-session-start-mechanism.md) for why sessions start this way: Claude Code on the GitHub Actions runner, signed in with the PO's subscription token, in place of the embedded API loop.

### Review path (issues #263 and #293)

How a story is reviewed is looked up from two tables in `scripts/dispatcher-invoke.sh` (alongside `EXECUTOR_ROUTING`), keyed by the story's `executor:` label. The script does not branch on an executor name anywhere else.

| `/implement` executor label | Review | Table |
|---|---|---|
| `claude-code-haiku` | Codex review loop (Codex Cloud, ADR-006) | `CODEX_REVIEW_LOOP_ROUTING` |
| `claude-code-sonnet` | Codex review loop (Codex Cloud, ADR-006) | `CODEX_REVIEW_LOOP_ROUTING` |
| `claude-code-opus` | Codex review loop (Codex Cloud, ADR-006) | `CODEX_REVIEW_LOOP_ROUTING` |
| `codex` | `/review` session under `claude-code-opus` | `REVIEW_EXECUTOR_ROUTING` |

- **Codex review loop.** The dispatcher starts no `/review` session. It hands the story over after `/implement` and resumes it on a later run; see [Codex review loop hand-off and resume](#codex-review-loop-hand-off-and-resume).
- **Paired session (resume entry).** `invoke_executor_command()` normally resolves the provider and model from the story's own `executor:` label. For the `/review` call of the review entry (see [Review of a Codex-implemented PR](#review-of-a-codex-implemented-pr)) the dispatcher passes a third argument that names the reviewing executor instead, so `/review` runs under a different provider than `/implement`. The session counts once toward the [usage limit](#usage-limit), whichever executor runs it.
- **Neither table.** An executor label with no row in either table gets a mid-cycle blocker after `/implement`.

The path is computed from the story's existing `executor:` label and is never written back to the issue. The label continues to mean "the `/implement` executor" only.

This is specific to the dispatcher's own invocation path. The manually-invoked `/review` command (`.claude/commands/review.md`, run by a human or an interactive session) reads neither table and its reviewer choice is unchanged.

Session tuning is overridable via environment variables (defaults shown), primarily for tests:

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_MAX_TURNS` | `60` | Turns before failing closed: Claude Code's `--max-turns` |
| `AGENT_MAX_WALLCLOCK_SECONDS` | `10800` (3h) | Total elapsed budget for the whole Actions job (shared across `/implement`/`/review` sessions and any chained stories via `DISPATCH_JOB_START_TS`; the script-only `/merge` and `/cleanup` start no session and are not bounded by it, not reset per call) — the enforced bound against GitHub Actions' 6h job ceiling (see ADR-003). A Claude Code session runs under `timeout` set to the time left. |
| `CLAUDE_BIN` | `claude` | The Claude Code binary, for the sessions and the allowance probe (test hook) |

---

## GitHub Token — `PROJECT_TOKEN`

The dispatcher workflow (`.github/workflows/dispatcher.yml`) and the Codex review bookkeeping workflow (`.github/workflows/codex-review-bookkeeping.yml`) require a **repository secret named `PROJECT_TOKEN`** — not the built-in `GITHUB_TOKEN`. The built-in `GITHUB_TOKEN` does not receive the `project` permission for user-owned GitHub Projects v2 boards. With it, the `repository.projectsV2` GraphQL query returns an empty list, `scripts/dispatcher-poll.sh` exits with `Error: No GitHub Projects (v2) board linked to <repo>`, and no board status can be written.

Provision a **classic personal access token (PAT)** with the scopes below and store it as a repository secret (not a variable) named `PROJECT_TOKEN`:

`https://github.com/<owner>/<repo>/settings/secrets/actions`

This table is the only list of the token's scopes. Other documents and comments link here instead of repeating it.

| Scope | Reason |
|---|---|
| `repo` | Read and write access to repository contents, issues and pull requests: every `gh` read, comment, PR update and merge, and the `resolveReviewThread` mutation with which the Codex review bookkeeping job resolves superseded Codex review threads. |
| `project` | Read and write access to the Projects v2 board. The full scope is needed because story status is written with `updateProjectV2ItemFieldValue`; read-only project access cannot move a board item. |
| `workflow` | GitHub refuses a merge or push from a classic PAT without this scope when the change touches a file under `.github/workflows/`. Dispatcher-started sessions run `gh` with this token. |
| `read:org` | Needed by the PR read of every `hpw` command (`fetch_pr_context()` in `src/headless_pr_workflow/github/pr_context.py`), including `hpw codex-review-bookkeeping` and `scripts/merge-gate-summary`. The read asks `gh pr view --json` for `reviewRequests`, and for that field `gh` requests a team's `name` and `slug` and its organisation's `login`. GitHub checks the token for this scope even when the PR has no team review request and the repository belongs to a user account. |

Both workflows map the secret to the `GH_TOKEN` environment variable (`GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}`), which `scripts/dispatcher-invoke.sh` and `scripts/dispatcher-poll.sh` read via `GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"`. No other changes to the scripts are needed.

The Codex review bookkeeping job resolves review threads with this token (`resolveReviewThread`, see [Codex review loop](HEADLESS-PR-WORKFLOW.md#codex-review-loop)). GitHub's GraphQL reference for the mutation names the classic `repo` scope (`public_repo` for a public repository), which the table already lists, so no scope is added. This was confirmed from that reference, not by a live call: the first run with a thread to resolve is the live confirmation, and a refused call exits non-zero naming the missing scope.

### Token of each step in a dispatcher-started session

The token of a dispatcher-started session is `PROJECT_TOKEN`: the workflow sets `GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}` and `scripts/lib/claude-session.sh` removes only the variables whose names start with `ANTHROPIC_`, so the session keeps it (pinned by `tests/test_set_project_status.py`). The four steps that need `gh` with GraphQL all use it (issue #318):

| Step | Token | Scope that matters | Evidence |
|---|---|---|---|
| Board status write (`scripts/set-project-status`) | `PROJECT_TOKEN` | `project` | Pending: live proof run of #318 |
| `scripts/ac-summary.sh` | `PROJECT_TOKEN` | `repo` | Pending: live proof run of #318 |
| Review-thread resolution (`resolveReviewThread`) | `PROJECT_TOKEN` | `repo` | Bookkeeping job (Actions); session proof pending |
| `scripts/merge-gate-summary` | `PROJECT_TOKEN` | `repo`, `read:org`, and **admin access** to read branch protection | Pending: live proof run of #318 |

The merge gate reads `repos/<repo>/branches/main/protection`, which GitHub allows only with admin access to the repository. A token without it makes the gate report `checks=fail(required-unavailable)` and `/merge` stops. The built-in `GITHUB_TOKEN` of a job does not have it. If the proof run shows that `PROJECT_TOKEN` cannot read it, the PO picks the token that can (for example a fine-grained token with "Administration: read"); the gate is not weakened.

**Writing a story's board status.** A session writes a status with one command, not with hand-written GraphQL:

```
scripts/set-project-status --issue <n> --status "<Status name>" --repo <owner/repo> [--dry-run]
```

It prints `Status: #<n> <old> -> <new>` (or `already <new>`) and exits `0` only after it has read the status back from the board. It exits `1`, with one line on stderr naming the failed step, when there is no token, GraphQL refuses the call (the line names the missing scope and points to this section), the issue is not on the board, the status is not an option, or the read-back disagrees; `2` for invalid arguments. The commands keep the best-effort rule: a non-zero exit is recorded in the Session Summary with `--deviation`. The trial of ADR-012 skipped a write because no such command existed (the session reported "I found no script in this run that writes board status"), not because GraphQL or the token failed.

**If the secret is absent**, the `gh` CLI fails to authenticate, the workflow exits with an error on the first API call and no story is dispatched.

**If a scope is missing**, GitHub refuses the query that needs it. `hpw codex-review-bookkeeping` then exits non-zero and its error names the failed step, the missing scope and this section (see [Codex review loop](HEADLESS-PR-WORKFLOW.md#codex-review-loop) → Failure).

---

## Slack Notifications

The dispatcher sends Slack notifications through `scripts/slack-notify.sh` for these events. There are no "story dispatched" or "closed" events: normal transitions are not notified.

| Event | Sent when | Retried |
|---|---|---|
| `decision_blocker` | a session left a `Type: decision` blocker | yes |
| `feature_closure_confirmation` | all stories of a Feature are Done | yes |
| `epic_closure_approval` | all Features of an Epic are Done | yes |
| `ready_for_refinement` | an issue enters "Ready for refinement" | yes, by the poll (issue #257) |
| `red_flow_health` | the board health is Red | re-sent on every poll while Red |
| `budget_cap_reached` | every ready story is budget-blocked | no |
| `dispatcher_error` | the invoke run exited unexpectedly; or the dispatcher gave up writing merge or cleanup evidence after 3 runs (issue #348, once per item) | no: the failing run is itself red; the give-up message is sent once |

**Retry of notifications that need PO action (issue #295).** `dispatcher-invoke.sh` only queues `decision_blocker`, `feature_closure_confirmation` and `epic_closure_approval` in `$DISPATCHER_STATE_DIR/<owner>/<repo>/actionable-events` (restored and saved by the same `actions/cache` steps as the refinement state). The workflow step **Retry actionable Slack notifications** (`scripts/dispatcher-notify-retry.sh run`) runs on every cycle, also when no story is ready or resumable, sends each undelivered event and records it as delivered only after `slack-notify.sh` exits 0. A delivered event is not sent again while it stays unresolved. An event stops being retried, even if it was never delivered, when it is resolved: a decision blocker by a `/unblock` line from the PO or by closing the issue, a closure event by closing the Feature or Epic. A new decision blocker on the same issue is a new event. Before `dispatcher-invoke.sh` chains to the next story it makes one extra delivery attempt (`run --drain`), so an alert is not held back by the chained sessions; that attempt records a success but never counts a failure. If the state file cannot be written, `register` delivers the event synchronously instead and exits non-zero when that fails too, and the retry step ends the run red with an `[NOTIFY] Error:` line.

**Bound and red run.** A failed delivery never aborts the dispatcher; the step exits 0 and retries on the next cycle. After 3 failed cycles in a row (about 15 minutes) the step exits 1, so the run ends red, and one comment is posted on the affected issue naming the event and the first failed attempt. Later failing cycles stay red but add no comment, and Slack still receives the message when it recovers. Missing or unreadable state warns and is treated as empty; in a malformed state file only the malformed lines are dropped and valid records are kept. At most one extra notification. Under `--dry-run` nothing is sent, recorded or commented. A manual run on a non-default ref only reports what it would send; an event queued in such a run is not saved and is not delivered.

To enable notifications, add `SLACK_WEBHOOK_URL` as a **repository secret** (not a variable):

`https://github.com/<owner>/<repo>/settings/secrets/actions`

| Secret | Required value |
|---|---|
| `SLACK_WEBHOOK_URL` | Incoming webhook URL from your Slack app (format: `https://hooks.slack.com/services/...`) |

The dispatcher workflow passes this secret to the shell environment automatically — no changes to workflow files are needed beyond what is already wired.

**If the secret is absent or invalid**, `slack-notify.sh` exits with code 1 and a missing webhook never aborts the dispatch work. The non-retried notifications (`budget_cap_reached`, `dispatcher_error`) fail silently and the run continues normally. The retried actionable events stay queued: after 3 failed cycles in a row the **Retry actionable Slack notifications** step turns the run red and comments once on the affected issue, until the secret is fixed.

`.github/workflows/codex-review-bookkeeping.yml` also reads `SLACK_WEBHOOK_URL`, in its notify step only, to send a `dispatcher_error` message when a bookkeeping run fails (at most one a day while the failure lasts; see `HEADLESS-PR-WORKFLOW.md` → "Codex review loop"). A missing secret there is a warning, not a failure of its own.

For local development, copy `.env.example` to `.env` and set your webhook URL there. `.env` is listed in `.gitignore` and must not be committed.

### Ready for refinement notification state

`scripts/dispatcher-poll.sh` sends one `ready_for_refinement` notification per issue for each **continuous stay** in **Ready for refinement** (issue #257). Its de-duplication state is one issue number per line in `.dispatcher-state/<owner>/<repo>/notified-refinement` (override the directory with `DISPATCHER_STATE_DIR`). It holds issue numbers only: no credentials, message text, or webhook responses.

Lifecycle, applied on every poll except `--dry-run`:

- **Delivery:** an issue is recorded only after `slack-notify.sh` exits `0`, which logs `[POLL] Notified: ...` on stderr.
- **Retry:** a failed delivery logs `[POLL] Warning: slack-notify.sh failed for #N; will retry next cycle`, stays unrecorded, and is retried on the next poll. The poll still exits `0`.
- **Exit and re-entry:** recorded issues that are no longer in **Ready for refinement** are dropped, so an issue that leaves and later returns notifies once more.
- **Missing, unreadable, or malformed state:** a missing file (a first run or a cache miss) starts from empty state silently. An unreadable or malformed file logs a `[POLL] Warning:` and starts from empty state. A failed write logs a warning. None of these abort the poll or change its stdout JSON.
- **`--dry-run`:** reads the state, logs `[DRY RUN] Would notify: ...`, sends no Slack notification, and never writes the state.

GitHub-hosted runners are ephemeral, so `.github/workflows/dispatcher.yml` persists `.dispatcher-state/` with `actions/cache`:

1. **Restore refinement notification state** runs before **Run dispatcher poll** on every enabled run. Its `dispatcher-state-` prefix restores the most recently saved snapshot.
2. **Save refinement notification state** runs right after a successful poll on every enabled default-branch run, whether or not an issue is ready for implementation. Cache entries are immutable, so each run saves under a unique `dispatcher-state-<run_id>-<run_attempt>` key.
3. **Default branch only writes state.** Actions caches are scoped by ref, so a snapshot saved by a run on another ref would be invisible to the scheduled default-branch runs, and they would notify again. **Resolve notification state scope** therefore marks every scheduled run, and every manual run dispatched on the default branch, as the state writer. A manual `workflow_dispatch` on any other ref still restores the default-branch snapshot. It runs the poll with `--dry-run`, so it sends no Slack notification (including Red flow health) and the save step is skipped.
4. A workflow-level `concurrency` group (`dispatcher-<owner>/<repo>`, `cancel-in-progress: false`) serializes scheduled and manual runs, so no two runs read the same snapshot and race to write it. GitHub keeps at most one pending run per group; a newer pending run replaces an older pending one, and in-progress runs are never cancelled.

**Residual risk:** if the cache is evicted (GitHub removes entries unused for 7 days, or the oldest entries past the repository's size limit) or a save fails, the next poll starts from empty state. Each issue still in **Ready for refinement** can then be notified once more.

**Interim mechanism:** this cache exists only because the dispatcher runs on ephemeral GitHub-hosted runners. #259 moves the dispatcher to persistent infrastructure, where local disk supersedes the cache steps. That migration must keep the continuous-stay, exit/re-entry, delivery-only recording, and retry semantics above, and must revalidate #257's acceptance criteria before the cache steps are removed.

---

## Dispatcher Toggle

| Variable | Values | Default |
|---|---|---|
| `DISPATCHER_ENABLED` | `true` / `false` | `true` |

Set this repository variable to `false` to pause all autonomous dispatcher runs without disabling the workflow file. The dispatcher checks this value at startup and exits cleanly when it is `false`.

To update this variable:

`https://github.com/<owner>/<repo>/settings/variables/actions`

---

## Usage Limit Notification

The first time the usage limit stops a session on a UTC day, the dispatcher workflow sends a `usage_limit_reached` Slack notification (see [Usage limit](#usage-limit)). The Slack message contains:
- A header identifying the pause: `:no_entry: Daily session limit reached -- Dispatcher Paused`
- The sessions started today and the limit, and that no new work starts until 00:00 UTC
- A direct link to [GitHub Settings - Variables](https://github.com/LauraMardones/headless-pr-workflow/settings/variables/actions) to change the limit or toggle `DISPATCHER_ENABLED`

To enable this notification, ensure `SLACK_WEBHOOK_URL` is configured as a repository secret (see Slack Notifications section above).

---

## Executable Issue Types in the Invoke Loop

Only Stories, Tasks, and Bugs are executable units for `/implement`. Before executor-label routing, `scripts/dispatcher-invoke.sh` classifies every candidate taken from **Ready for implementation** (issue #256):

| Type metadata | Result |
|---|---|
| Exactly one `type:story`, `type:task`, or `type:bug` label | Executable |
| No `type:` label, native GitHub issue type `Story`, `Task`, or `Bug` | Executable (the native type is only a fallback) |
| `type:epic`, `type:feature`, any other `type:` label, more than one `type:` label, another native type, or no type at all | Non-executable |

Type is never inferred from the title or from an `executor:` label. A non-executable candidate is not treated as fatal. The dispatcher:

- writes one warning to stderr naming the issue and its detected, missing, or unsupported type
- excludes the item from reselection for the rest of the run
- continues to the next ready item:

```
Warning: #<N> is not an executable Story, Task, or Bug (detected type: type:epic); skipping it for this run and continuing selection.
```

If no executable item remains, the run exits `0` and invokes no executor. `--dry-run` makes the same skip decisions without any executor invocation or GitHub mutation.

This skip applies only to the item's type. A Story, Task, or Bug with a missing or unrecognised `executor:` label is still a fatal configuration error (non-zero exit), and the error lists the supported labels:

```
       Add one of: executor:claude-code-haiku, executor:claude-code-opus, executor:claude-code-sonnet, executor:codex
```

`scripts/dispatcher-poll.sh` is unchanged. Its `ready_for_implementation` list and count report every item in that board status, executable or not, and the invoke step applies the type guard.

---

## Usage Limit Check in the Invoke Loop

Every session start goes through `invoke_executor_command()`. It calls `check_usage_limit` first (see [Usage limit](#usage-limit)), then `check_subscription_allowance`, and counts the session with `record_session_start` just before it starts. A fix round is an `/implement` session and counts like any other.

Under `--dry-run` the check is logged and nothing is counted or sent:

```
[DRY RUN] Would check usage limit before /<step>
```

---

## Codex review loop hand-off and resume

A Claude-implemented story is reviewed by Codex Cloud, not by a dispatcher session (ADR-006, issue #293). Codex answers asynchronously, so the dispatcher ends the story's run after `/implement` and picks the story up again on a later run. Everything is decided from GitHub facts read at the start of that run: the board status, the linked PR and hidden markers in comments. Nothing is remembered on the runner. The dispatcher honours its own markers only on comments written by its own account (the login behind `GH_TOKEN`), and the bookkeeping job's marker only on comments by that job's account, so a marker pasted into a comment by anyone else has no effect.

The `codex-review-bookkeeping` workflow does the part in between. It acts only on a story in `In review`, and moves it to `In merge` after a clean review with green required checks and a passed approval gate, or to `In implementation` after findings. `/implement` posts the review request; the dispatcher posts none.

### Hand-off after `/implement`

For a story whose executor has a `CODEX_REVIEW_LOOP_ROUTING` row, after `/implement` succeeds the dispatcher:

- finds the linked open PR. With no linked open PR, or a PR that is still a draft, it posts the mid-cycle blocker for `/implement`, leaves the story in `In implementation` and exits `1`;
- writes `In review` only when the story is still `In implementation` and no fix round is due. It never overwrites `In review` or `In merge`, because the bookkeeping job may already have acted on Codex's answer;
- writes nothing when Codex answered with findings while the session was still running: the story is already back in `In implementation` with a fix round due on the new head, and the next run starts it;
- when that `In review` write fails, or the story is still in "Ready for implementation" because no status write of the run took effect, posts one recovery comment with a hidden marker for the PR and exits `1`. The story is not reported as handed off and is not picked again as the next ready story. A later run finds the marker on a story still in `In implementation` or "Ready for implementation", with a non-draft PR and no fix round due, and completes the hand-off without starting `/implement` again (`[RESUME] #<n>: completing the hand-off on PR #<pr>`), also when the PR head has changed since. It then posts a second marker, so the hand-off is completed once. Without this the bookkeeping job, which acts only on `In review`, would never see Codex's answer;
- starts no `/review` session;
- logs `[HANDOFF] #<n>: handed to the Codex review loop (Codex Cloud) on PR #<pr>; no /review session started` and goes on to the next ready story.

### Resume at `In merge`

A story in `In merge` is taken up at `/merge`, whatever its executor. `/merge` and `/cleanup` here are the script-only phases (issue #264): no session starts and no allowance is checked. The board status is not trusted on its own: the merge gates inside `scripts/dispatcher-merge.sh` decide whether the PR merges.

| Facts | What the dispatcher does |
|---|---|
| A linked open PR that is not a draft | `/merge`, then `Done` and `/cleanup`, then the feature and epic closure check. Log: `[RESUME] #<n>: /merge PR #<pr>` |
| No open PR, and a merged PR that closes the story | `Done` and `/cleanup`, no `/merge`. This finishes a run that stopped between merge and cleanup. |
| Neither (including a PR that is still a draft) | One `Type: external` blocker on the story, posted once. |

**Evidence that could not be written (issue #348).** The `merge-owner` marker, the merge confirmation and the cleanup closing Session Summary are durable evidence. When a write of one fails (after 3 attempts), the writer leaves one *evidence-pending* comment with the exact text as a hidden base64 payload (`scripts/lib/evidence-pending.sh`; nothing is kept on the runner). The dispatcher posts that text once on a later run. Only comments of the dispatcher's own account count, for the pending comment and for the proof that the text is already posted; a copy written by anyone else proves nothing.

| Item | Pending comment on | Posted by a later run |
|---|---|---|
| `merge-owner` | the PR, for the head the `/implement` session produced | before `/merge`, and only while the PR is still at that head; a pending marker for another head is never posted (`[RESUME SKIP] ... is for head <sha>, not <sha>`) |
| `merge-confirmation` | the PR, for the merged head | before `Done` and `/cleanup`; the story stays `In merge` until it is posted. A merge script that merged and then failed only on this comment is not a merge blocker |
| `cleanup-summary` | the story, which is already `Done` | by the resume pass, through the `evidence:pending` label (see below) |

A retry posts nothing when the exact text is already on the issue or PR (no duplicate), and `/merge` is still not run twice for one head. Each failed run adds a `evidence-retry-failed` marker comment on the story. After 3 failed runs for the same item the dispatcher posts one `Type: external` blocker on the story, sends one Slack `dispatcher_error`, and stops (`[RESUME SKIP] ... retrying stopped after 3 runs, waiting for a PO /unblock line`). Other stories are not held back. A standalone `/unblock` from the PO, after the evidence is posted by hand or the cause is fixed, starts the count again. Log lines: `[RESUME] #<n>: posting the missing <item> evidence on #<target>`, `[RESUME SKIP] #<n>: the <item> evidence ...`.

**The `evidence:pending` label.** No board state leads back to a `Done` story, so when `/cleanup` leaves the closing summary as a pending comment the dispatcher also adds the label `evidence:pending` to the story. Each resume pass lists the issues with that label (one call, whatever their age), posts each pending summary once, and removes the label once the summary is on the story or nothing of the dispatcher's own is pending. While retrying has stopped after the 3-run limit, the label stays until the PO's `/unblock` lets the next run post the summary. The label is only an index: anyone can add or remove it, but what is posted is still read from the dispatcher's own pending comment, so a label added by someone else only causes a read. The workflow's poll step also lists the label (one call per cycle) and counts a labelled story as resumable work, so `dispatcher-invoke.sh --resume` runs even when nothing is ready, `In merge` or `In implementation`; a failed read of the label counts as pending. The dispatcher creates the label in the repository before it first adds it, because GitHub refuses to add a label the repository does not have; you can also create it by hand. A failed label write counts as no pending copy (gap (2) below).

Two gaps have no pending comment to retry, so they are closed separately. (1) A merged PR with no `Merge gate:` confirmation from the dispatcher, for example when the merge script failed before it wrote one, does not reach cleanup: each run counts a `merge-confirmation-missing` failure and, after 3, posts the blocker and the Slack message; a PO `/unblock` after that accepts the gap and cleanup follows. (2) When `/cleanup` fails and neither its closing summary nor a pending copy with the `evidence:pending` label is on the story (the pending or the label write failed too), the story goes back from `Done` to `In merge` so the next run reruns cleanup, with the same 3-run limit (`cleanup-summary-unrecorded`); after the limit the story stays `Done` with the blocker.

**One merge attempt per head.** When `/merge` fails, the mid-cycle blocker records the PR head it was tried on, and the story stays in `In merge`. Later runs do not run the merge script for it and log `[RESUME SKIP] #<n>: /merge was already tried on PR #<pr> at head <sha>`. Another attempt needs a new PR head, or a standalone `/unblock` line from the PO posted after the blocker (the resolving-comment rule of issue #262). The same applies when `/merge` fails in the same run as `/review` for an `executor:codex` story.

### Review of a Codex-implemented PR

`executor:codex` stories are not started for `/implement`: in "Ready for implementation" they are skipped with `Warning: #<n> has executor:codex, which the dispatcher does not start for /implement (no subscription mechanism, ADR-012); skipping it for this run and continuing selection.` The skip changes and spends nothing (no board write, comment, blocker, notification, counted session or allowance probe), and the story is not returned again in the same run. It is a lookup, not a branch on the label: an executor with no `EXECUTOR_PROVIDER` row is not started for `/implement`.

A PR that Codex implemented outside the dispatcher is still reviewed by it. The resume pass has a fourth entry, after the three above. A review is due when all of these hold:

- the story's board status is `In review`;
- it has exactly one `executor:` label, and that label has a `REVIEW_EXECUTOR_ROUTING` row (`scripts/lib/review-routing.sh`, shared with `scripts/dispatcher-poll.sh`);
- it has a linked open PR that is not a draft.

The dispatcher then runs `/review <pr>` through `invoke_executor_command()` under the executor the table names (`claude-code-opus`). The usage limit and the allowance reserve apply as for any session. Log: `[RESUME] #<n>: /review on PR #<pr> under claude-code-opus`. A story that waits for Codex Cloud (a `CODEX_REVIEW_LOOP_ROUTING` row) logs nothing; a story with zero or two `executor:` labels, no linked open PR or a draft PR logs one `[RESUME SKIP] #<n>: <reason>` line.

After `/review` succeeds the board status is read again. When it is `In merge`, the script-only `/merge` and `/cleanup` and the closure check run as for any `In merge` story. Otherwise the entry ends with `[RESUME] #<n>: /review left the story in "<status>"; no merge`: when `/review` found blockers the fixes are made outside the dispatcher, as the implementation was.

**One `/review` attempt per head.** When `/review` fails, the mid-cycle blocker records the PR head it was tried on. Later runs log `[RESUME SKIP] #<n>: /review was already tried on PR #<pr> head <sha7>; waiting for a new head or /unblock` and start no session. A new PR head, or the PO's `/unblock`, allows one new attempt.

`scripts/dispatcher-poll.sh` lists such stories as `review_due` (number and title), and the workflow counts `review_due` in `resumable`, so the invoke step runs for them even when nothing else is due. A board whose only `In review` story is Claude-tier gives `review_due: []`. Under `--dry-run` the entry logs `[DRY RUN] Would start Claude Code (opus) on the subscription for /review <pr>` and starts nothing.

### Fix round

A fix round is due when all of these hold:

- the story's board status is `In implementation`;
- it has exactly one `executor:` label, and that label has a `CODEX_REVIEW_LOOP_ROUTING` row;
- it has a linked open PR;
- the PR has a comment by the bookkeeping job's account that carries the job's hidden marker with `action=move-in-implementation` and `head=` equal to the PR's current head.

The dispatcher then runs `/implement` for the story, which continues on the existing PR, and applies the hand-off again. Log: `[RESUME] #<n>: fix round <k> of <max>, /implement on PR #<pr>`.

- A fix round is picked up on the next run. It does not wait for the stale threshold.
- The story already holds its place within the WIP limit, so the WIP and file-overlap checks are not run against it again. This holds on both sides: the dispatcher does not run its pre-flight for a fix round, and `.claude/commands/implement.md` step 7 tells the session to skip its own WIP pre-flight for a story that is already `In implementation` with an open PR. The allowance check before the session still applies. When it refuses, the story stays in `In implementation` and the log line is `[RESERVE SKIP] #<n>: fix round not started; …`. It is not put back to "Ready for implementation", unlike a refusal before a first `/implement`.
- Once the session pushes a commit, the marker no longer names the PR's head, so the same findings start no second round.
- The dispatcher reads only the bookkeeping job's marker. It does not parse Codex comments or reviews.
- The bookkeeping job's account is the login behind the dispatcher's own token, because both workflows use `PROJECT_TOKEN`. Set `CODEX_BOOKKEEPING_LOGIN` in the environment if the two ever differ. A marker in a comment by any other account is ignored.

**Maximum fix rounds.** The dispatcher counts the PR heads for which the bookkeeping job recorded findings, one per head. When the count is above `CODEX_FIX_ROUND_MAX`, it starts no session, logs `[RESUME SKIP] #<n>: fix round <k> on PR #<pr> is above the maximum of <max>` and posts one `Type: resource` blocker on the story, once. A round with only Out of scope findings pushes no new head, so it starts no further round; the story waits for the PO's waiver.

| Variable | Valid values | Default |
|---|---|---|
| `CODEX_FIX_ROUND_MAX` | whole number of `1` or more | `3` |

Set it as a repository variable; `.github/workflows/dispatcher.yml` passes it to the invoke step. An unset or empty variable uses the default. Any other value stops the run with an error before any session starts.

**One fix-round attempt per head.** A fix round that fails, or an `/implement` session that ends on the same PR head it started on while the findings for that head are still open, is not repeated on the same head, whatever status the session left the story in. The mid-cycle blocker for `/implement` records the head, and later runs log `[RESUME SKIP] #<n>: the fix round was already tried on PR #<pr> at head <sha>` until the PR has a new head or the PO posts a standalone `/unblock` line.

### Order and entry into a run

Each run handles resumable work before it starts new work:

1. stories in `In merge`. The board is read again after each one that finishes, so the feature and epic closure check sees stories finished earlier in the same run as `Done`;
2. fix rounds that are due, and hand-offs that are still pending;
3. stories in **Ready for implementation**, as before.

`scripts/dispatcher-poll.sh` reports the candidates in two more keys of its JSON, `in_merge` and `in_implementation` (each `[{number, title}]`), from the board read it already makes. `ready_for_implementation` and `ready_for_refinement` are unchanged. `.github/workflows/dispatcher.yml` starts the invoke step, and the steps that prepare a session before it, when a story is ready for implementation **or** either new list is non-empty:

```
bash scripts/dispatcher-invoke.sh --repo <owner/repo> --issue <n>   # resumable work first, then story <n>
bash scripts/dispatcher-invoke.sh --repo <owner/repo> --resume      # resumable work only
```

The workflow uses `--resume` when no story is ready for implementation. It runs the invoke step when a story is ready, `In merge` or `In implementation`, or when an issue carries the `evidence:pending` label (issue #348). A run in which nothing is due starts no session and exits `0`.

The board must have the `In review` and `In merge` Status options. A missing option stops the run with an error that names it, before any session starts. It is not skipped with a warning.

### Exit codes and `--dry-run`

| Exit | Meaning |
|---|---|
| `0` | The story was handed to the review loop, a resume entry completed, a story was skipped by a bound, or there was nothing to do. |
| `1` | A session failed and a blocker was posted. |
| non-zero, no blocker | A missing board option or an invalid `CODEX_FIX_ROUND_MAX`. |

Under `--dry-run` each of the lines above is logged with a `[DRY RUN]` prefix, the hand-off is logged as `[DRY RUN] Would hand #<n> to the Codex review loop …`, and no session starts and nothing on GitHub changes.

### Known limits

- A session that ends without an error counts as a success. After `/merge` the dispatcher writes `Done` without reading the PR's merged state itself; `scripts/dispatcher-merge.sh` exits 0 only after `hpw merge-pr` confirmed the merge (or GitHub already reported it merged).
- The bookkeeping job resolves Codex's own finding threads from older heads when Codex returns a clean result on the current head, and does not move a story to `In merge` while an unresolved thread it may not resolve remains (a human's thread, a Codex thread on the current head, or one whose review commit cannot be read): it posts a `Type: external` blocker instead. A thread the PO or another reviewer opened is still resolved by a person, and a defect named in an auto-resolved thread that Codex did not re-check can reach `main` (accepted risk, ADR-006 revisit rule). See [HEADLESS-PR-WORKFLOW.md → Codex review loop](HEADLESS-PR-WORKFLOW.md#codex-review-loop).
- Stale recovery does not roll back a story that waits for a fix round. While the allowance is below the reserve, the run ends at the fix round, before the stale check. A story held by one of the two fix-round blockers above is skipped by the stale check: it waits for the PO.
- The merged-PR lookup for a story in `In merge` reads the 100 most recently updated closed PRs.

---

## Subscription allowance and reserve

Before every model session start (`/implement` and `/review`), the dispatcher runs `scripts/dispatcher-allowance.sh` (issue #317, ADR-012). The script reads how much of the PO's five-hour and weekly Claude subscription allowance is left. A session starts only when **both** windows are at or above the reserve the PO has set. When the allowance cannot be read, nothing starts.

### Reserve variables

Set these as repository variables under **Settings → Secrets and variables → Actions → Variables**:

`https://github.com/<owner>/<repo>/settings/variables/actions`

| Variable | Valid values | Default |
|---|---|---|
| `RESERVE_FIVE_HOUR_PERCENT` | whole number `0`–`100` | `10` |
| `RESERVE_WEEKLY_PERCENT` | whole number `0`–`100` | `20` |

- The reserve is the share of each window, in percent, that autonomous work must leave untouched. Remaining share = (1 − `utilization`) × 100. A start needs remaining ≥ reserve in both windows; equality is a start.
- An unset or empty variable uses the default. GitHub passes an unset `vars.X` as an empty string.
- Any other value (`abc`, `-1`, `101`, `10%`, `7.5`) is an `[ALLOWANCE ERROR]` that names the variable and the value. No probe runs.
- A change applies on the next poll that finds work. No code change is needed. A hold written under the old values is ignored.

### Subscription token — `CLAUDE_CODE_OAUTH_TOKEN`

The probe signs in with the repository secret `CLAUDE_CODE_OAUTH_TOKEN`, an OAuth token for the PO's subscription created with `claude setup-token`.

- Save the secret with nothing before or after the token. The scripts remove every whitespace character before use, but a token with stray characters inside it is rejected with `401 OAuth access token is invalid`, which does not name the cause.
- The current token was created on 2026-10-08 and lasts one year. Renew it before 2027-10-08 by running `claude setup-token` again and replacing the secret.
- The script and the dispatcher log only the token's presence and its length after whitespace removal, never its value.

### The probe

One Claude Code turn on Haiku, with no tools, a fixed short prompt, and the repository's instructions left out (it runs from an empty temporary directory):

```
claude -p "Reply with the single word OK." --model haiku --tools "" --strict-mcp-config \
  --no-session-persistence --output-format stream-json --verbose
```

- `--bare` is never used: bare mode ignores the OAuth token (ADR-012 → Candidate B).
- Every variable whose name starts with `ANTHROPIC_` is removed from the probe's environment by `scripts/lib/claude-session.sh`, the helper the sessions use too (issue #314). If the init message does not report `apiKeySource: "none"`, the result is an error, so the probe can never bill a metered key.
- The probe times out after 120 seconds (override with `ALLOWANCE_PROBE_TIMEOUT`).
- The script reads `rate_limit_info.unifiedWindows.five_hour` and `.seven_day` (`utilization`, `resetsAt`) and `rate_limit_info.overageStatus` from the `rate_limit_event` line. Anthropic does not document `unifiedWindows`.

### Pinned Claude Code version

`.github/workflows/dispatcher.yml` installs Claude Code in the "Install Claude Code" step, only when the poll found work:

```
npm install -g @anthropic-ai/claude-code@2.1.296
```

The pin keeps the undocumented event shape stable. Every Claude session runs on the same install (issue #314), so the pin also fixes the session flags and what the model aliases mean. To change the version, edit that step, then run the dispatcher once with `workflow_dispatch` and check that the `[RESERVE]` line still shows both windows and the `[SESSION]` line shows `success` and `apiKeySource none`. `tests/test_dispatcher_workflow_allowance.py` checks that the version here matches the workflow.

### Outcomes and log lines

| Exit | Decision | Log line (stderr of the script) | Dispatcher behavior |
|---|---|---|---|
| `0` | `start` | `[RESERVE] five-hour 79% left (reserve 10%), weekly 71% left (reserve 20%) — start` | The session starts. |
| `1` | `refuse` | `[RESERVE SKIP] weekly 15% left, below reserve 20%; resets 2026-10-14T08:00Z` or `[RESERVE HOLD] below reserve until 2026-10-14T08:00Z; not probing` | Before `/implement`: no session, no comment, the story goes back to "Ready for implementation" for a later poll, and the run exits 0. Before `/review`: no session, one comment on the story that names the step, the window, the share left, the reserve and the reset time, and the run exits 0. The comment is not posted again while it is already the story's newest comment for that step. |
| `2` | `error` | `[ALLOWANCE ERROR] <reason> — starting nothing` | No session. The job fails, without a `dispatcher_error` Slack message. After `/implement`, the story gets the same comment with the reason. |

A probe that is itself stopped by a usage limit (result error `rate_limit`) with no readable `rate_limit_event` is a `refuse`, not an `error`. An `overageStatus` other than `"rejected"` is an `error`: usage credits look turned on, so a session could be billed past the limit.

A refusal partway through a cycle leaves the story at its current step. A story in `In merge` is not affected: its `/merge` is a script and never checks the allowance (issues #293, #264). A refusal or an error before a fix round changes nothing on the board: the story keeps its place in `In implementation`, no comment is posted, and the fix round is tried again on a later run, before new work. For `/review` nothing restarts the step automatically until #316; the PO can run it by hand after the reset time. `/merge` and `/cleanup` are script-only and are never refused or held by the allowance (issue #264). Under `--dry-run`, the dispatcher logs `[DRY RUN] Would check subscription allowance before /<step>` and does not run the script.

The script prints exactly one JSON object on stdout in every exit path:

```json
{
  "decision": "start | refuse | error",
  "source": "probe | hold | none",
  "reason": "string; empty on start",
  "five_hour": { "utilization": 0.21, "remaining_percent": 79, "reserve_percent": 10, "resets_at": "2026-10-10T15:00:00Z" },
  "weekly":    { "utilization": 0.29, "remaining_percent": 71, "reserve_percent": 20, "resets_at": "2026-10-14T08:00:00Z" },
  "api_key_source": "none",
  "overage_status": "rejected"
}
```

Values that were not read are `null`. `remaining_percent` is rounded down for display (and shown as `0` when utilization is above 1); the decision uses the unrounded value.

### The hold

A refusal from a probe writes `.dispatcher-allowance/hold.json` (override with `ALLOWANCE_STATE_DIR`) with `until` set to the latest reset time among the windows below the reserve, and the two reserve values in force. Until that time, and while both reserve values are unchanged, the script refuses with `source: "hold"` and does not run Claude Code. The remaining share only goes up at a reset, so no chance to start is lost. A missing, unreadable or malformed hold file means the script probes; it never means a start without a probe.

The hold lives in the same `actions/cache` entry as the usage limit's session count, whose key changes every UTC day. A hold therefore lasts at most until the next UTC day; after that, one probe re-creates it.

### A repeated `[ALLOWANCE ERROR]`

An `[ALLOWANCE ERROR]` on every poll with work most likely means the undocumented `rate_limit_event` shape has changed, or the token has expired. Each such run fails visibly in Actions (and in GitHub's failed-run email) but sends no Slack message. The remedy is to set `DISPATCHER_ENABLED=false`, read the reason in the log, and either renew the token or revisit ADR-012.

## The dispatcher script is read completely before any session starts

bash reads a script from the file in pieces, so a session that rewrites `scripts/dispatcher-invoke.sh` while the run waits for it (any story that changes the dispatcher does) could make the run resume in the middle of a line (issue #345). The whole body of the script is therefore one brace group closed by `}; exit $?` on a single line: bash parses it completely before the first session starts, and nothing is read from the file afterwards. Keep it that way: no code after the group.

The `exec bash "$0"` tail calls start a new process on purpose, so the next story runs the code that was just merged. The other files are safe: `scripts/lib/claude-session.sh`, `scripts/lib/decision-blocker.sh` and `scripts/lib/evidence-pending.sh` are sourced once at start, and `dispatcher-usage-limit.sh`, `dispatcher-allowance.sh`, `slack-notify.sh`, `dispatcher-change-check.sh` and `session-summary.sh` run as separate processes that read the file at call time. Any new script the dispatcher sources after a session, or runs for the length of a session, needs the same treatment; check `dispatcher-merge.sh` and `dispatcher-cleanup.sh` (#264) when they land.
