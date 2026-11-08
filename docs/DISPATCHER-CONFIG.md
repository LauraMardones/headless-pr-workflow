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

**When the limit stops a session** the story is left where a refusal by the reserve leaves it, the run exits `0` and starts no other session (it does not go on to the next story): before `/implement` the story goes back to "Ready for implementation"; before a fix round it keeps its place in "In implementation" and nothing is written, unless the session pushed a new head: then the head is handed off at the pause ([Pause at a provider usage limit](#pause-at-a-provider-usage-limit)); before `/review` one comment starting `## Usage limit` names the step, `N of M sessions today` and the reset time (00:00 UTC), and is not posted again while it is the story's newest comment for that step. A later poll tries `/review` again once the usage limit and the allowance check allow it.

**Notification.** The first stop of a UTC day sets the invoke step output `usage_limit_reached=true`. The workflow step "Notify usage limit reached" runs in the same run, sends one `usage_limit_reached` Slack message (`sessions_today`, `limit`, `settings_url`: the count, the limit and a link to the variables page) and only then writes the day's marker. If the send fails, no marker is written and the next run tries again; a second stop on the same day after a successful send sends nothing. A full retry with a durable record is #295.

**Invalid value.** A missing, empty or invalid value (`abc`, `-1`, `7.5`, `10 `, `1e3`) starts no session. The run log has the `[USAGE LIMIT ERROR]` line, the job fails and no `dispatcher_error` Slack message is sent. Before `/implement` the story goes back to "Ready for implementation"; before `/review` it gets a `## Usage limit` comment.

`--dry-run` neither counts nor notifies and logs `[DRY RUN] Would check usage limit before /<step>`.

---

## WIP and queue limits

The PO sets every WIP and queue limit in repository variables (issue #358). No script holds a number or a default for them, so the dispatcher, the flow review and the docs cannot disagree.

| Variable | Valid values | Suggested value | Read by |
|---|---|---|---|
| `WIP_LIMIT_IMPLEMENTATION` | A whole number of one or more, in decimal digits only. The most stories in "In implementation" at the same time. | `2` | the [scheduler](#scheduler-new-work-from-github-facts), `scripts/dispatcher-invoke.sh` (Step 5), `scripts/flow-review.sh` (violation, red and yellow status, `WIP count: N/<limit>`) |
| `READY_BUFFER_MIN` | A whole number of zero or more. The fewest ready stories the scheduler keeps. | `3` | the shared check; the [scheduler](#scheduler-new-work-from-github-facts) uses it for the ready buffer |
| `READY_BUFFER_MAX` | A whole number, `READY_BUFFER_MIN` or more. The most ready and waiting stories the scheduler keeps. | `5` | the shared check; the [scheduler](#scheduler-new-work-from-github-facts) uses it for the ready buffer |

**Change them** under **Settings → Secrets and variables → Actions → Variables** (`https://github.com/<owner>/<repo>/settings/variables/actions`). No code change is needed; the new value applies on the next run. The workflow passes all three to the "Run dispatcher poll" step (the poll runs the scheduler and `flow-review.sh`) and the "Run dispatcher invoke" step (it runs the scheduler again).

**Board column limits are only visual.** A limit set on a column of the project board is read by nothing. The PO sets it by hand to match the variable.

**The shared check.** `scripts/lib/limits.sh` (`load_limits`) checks all three variables and is sourced by `dispatcher-invoke.sh` and `flow-review.sh`, so a missing or invalid buffer value is found on the first run. The scheduler checks the same three variables itself and reports `unreadable` for a bad value. Any script that reads a limit uses it instead of its own check.

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

## Scheduler: new work from GitHub facts

The dispatcher starts new work from the scheduler's decision, never from a board status (issue #474, ADR-010, Feature #460). Moving a card to "Ready for implementation" or "Ready for refinement" starts nothing, and the scheduler's choice gets its session whatever its board status. The decision rule is `hpw dispatcher scheduler next` (issue #473); `scripts/dispatcher-poll.sh` and `scripts/dispatcher-invoke.sh` only call it, through `scripts/lib/scheduler.sh`, and act on its result.

```
scripts/hpw dispatcher scheduler next --repo <owner/repo> --in-implementation <n>[,<n>...] [--dry-run]
```

**Order of actions in a run** (#460 → Decisions "Order of actions in a run"). One model session at a time:

1. work in flight, as before (the resume pass: "In merge", fix rounds, pending hand-offs, "In review", conflict rounds);
2. then one scheduler decision for new work. The scheduler orders the rest: re-refinement of stale issues (Epic first), Epic refinement after a new spec merge, Feature refinement, the start of a ready story's implementation, and story refinement for the ready buffer.

**When it is asked.** The poll asks to decide whether the invoke step runs. The invoke step (`dispatcher-invoke.sh --repo <owner/repo>`, no `--issue`) asks again right before it starts new work, after the work in flight, so the decision uses fresh facts. After a finished `/implement` cycle (the hand-off to the Codex review loop), and after a story the run skips as non-executable, the next story also comes from a new scheduler call, not from the board.

**Acting on the result.**

| Result | What the dispatcher does |
|---|---|
| `refine #N` | One refinement session for `#N` through the `--refine` path ([Refinement sessions](#refinement-sessions)); the run ends after it, with no chained session. |
| `implement #N` | `/implement #N` with the usual cycle and the board write to "In implementation". A story this run already started or skipped is not started again: the run exits `0`. |
| `idle` | No new-work session. The run log has `[SCHEDULER] idle`. Exit code unchanged. |
| `unreadable` | No new-work session. The run log has `[SCHEDULER] unreadable: <error>`. Work in flight in the same run still runs, and the next run (five minutes later, ADR-013) asks again. Exit code unchanged. An output the dispatcher cannot read counts as `unreadable`. |

**The rules** (in the scheduler, issue #473):

- **Ready buffer.** Story refinement starts only while fewer than `READY_BUFFER_MIN` stories are ready and fewer than `READY_BUFFER_MAX` are ready or refined and waiting; otherwise the story is skipped with `buffer-full`.
- **WIP limit.** No implementation starts while the `--in-implementation` list has `WIP_LIMIT_IMPLEMENTATION` or more stories (`wip-limit`).
- **File overlap.** A ready story whose `## Files affected` names a file of a listed story is skipped (`file-overlap #<n> <path>`).
- **No-spec rule.** Nothing under an Epic with no merged spec is refined. The run log carries the scheduler's `[SCHEDULER] skip #<epic> no-merged-spec: <reason>` line; no comment is posted.
- Every skipped issue gets one `[SCHEDULER] skip #<n> <reason>` line on stderr. The poll and the invoke step pass these lines to the run log unchanged.

**`--in-implementation`.** The dispatcher passes the stories on the board in "In implementation", "In review" and "In merge": the work in flight as it finds it today. The scheduler never starts a listed story, so a story in review or merge is not started again. Until #475 finds work in flight from PR facts (with its own `--in-flight` list), the stories in review and merge also count for the scheduler's WIP limit and file overlap, so fewer new stories may start than the limit allows (PO decision on PR #485).

**Gates, unchanged for both kinds.** The pause switch (`DISPATCHER_ENABLED` not `true`: the workflow guard skips the poll and the invoke step, so the scheduler is not called and no session starts), the [usage limit](#usage-limit), the [allowance reserve](#subscription-allowance-and-reserve) and one session at a time (the workflow's `concurrency` group).

**Poll output.** The poll's JSON has `next_issue` (the issue of a `refine` or `implement` action, or `null`), `next_action` (the scheduler's `action` object: `kind`, `issue`, `type`, `epic`, `step`, `reason`; or `null`) and `scheduler_result` (`refine`, `implement`, `idle` or `unreadable`). It no longer has `ready_for_implementation` or `ready_for_refinement`. The workflow's invoke-step condition is unchanged in meaning: the step runs when `next_issue` is set or `resumable` is true.

**Retired.** The `ready_for_refinement` Slack notification and its `notified-refinement` saved state are gone (#460 → Decisions "No stored state is needed"). A leftover `notified-refinement` file in the Actions cache does no harm: nothing reads it, and it expires with the cache.

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
- **Git authentication (issue #337).** The checkout keeps no credentials in git config and has the full history (`fetch-depth: 0`). The step "Configure git to authenticate through gh" runs `gh auth setup-git` with `PROJECT_TOKEN`, so git asks `gh` for credentials: a session's plain `git push`, `git fetch` and `git merge` use `PROJECT_TOKEN`, with no `extraheader` or `credential.helper` override and no `--unshallow`.
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
| A provider usage limit (`rate_limit`) | `[SESSION RATE LIMIT] /<command> #<n> stopped at a provider usage limit; resets <UTC time or unknown>` (exit code 75, a pause, not a failure: see [Pause at a provider usage limit](#pause-at-a-provider-usage-limit)) |
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

A dispatcher-started session also pushes with this token: its `git push` goes through `gh auth setup-git` (issue #337), so the `repo` and `workflow` scopes above cover the push, with no new scope.

The Codex review bookkeeping job resolves review threads with this token (`resolveReviewThread`, see [Codex review loop](HEADLESS-PR-WORKFLOW.md#codex-review-loop)). GitHub's GraphQL reference for the mutation names the classic `repo` scope (`public_repo` for a public repository), which the table already lists, so no scope is added. This was confirmed from that reference, not by a live call: the first run with a thread to resolve is the live confirmation, and a refused call exits non-zero naming the missing scope.

### Token of each step in a dispatcher-started session

The token of a dispatcher-started session is `PROJECT_TOKEN`: the workflow sets `GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}` and `scripts/lib/claude-session.sh` removes only the variables whose names start with `ANTHROPIC_`, so the session keeps it (pinned by `tests/test_set_project_status.py`). The four steps that need `gh` with GraphQL all use it (issue #318):

| Step | Token | Scope that matters | Evidence |
|---|---|---|---|
| Board status write (`scripts/set-project-status`) | `PROJECT_TOKEN` | `project` | [Dispatcher run 41328774189](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/41328774189/job/124914234657#step:12:96): the `/implement #412` session set "In review" |
| `scripts/ac-summary.sh` | `PROJECT_TOKEN` | `repo` | [Dispatcher run 41206624527](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/41206624527/job/124552899111#step:12:84): exit `2` (issue read, no checkbox list) in the `/implement #344` session |
| Review-thread resolution (`resolveReviewThread`) | `PROJECT_TOKEN` | `repo` | Bookkeeping job (Actions): [run 41342523068](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/41342523068/job/124955357930#step:4:40) resolved a thread on PR #424 |
| `scripts/merge-gate-summary` | `PROJECT_TOKEN` | `repo`, `read:org`, and **admin access** to read branch protection | [Dispatcher run 41350157331](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/41350157331/job/124979233267#step:12:35): `Merge gate: … checks=pass` for PR #430 |

All four were proven on the runner in ordinary runs (issue #414); the evidence is on [#308](https://github.com/LauraMardones/headless-pr-workflow/issues/308#issuecomment-6584021065).

The merge gate reads `repos/<repo>/branches/main/protection`, which GitHub allows only with admin access to the repository. A token without it makes the gate report `checks=fail(required-unavailable)` and `/merge` stops. The built-in `GITHUB_TOKEN` of a job does not have it. If the proof run shows that `PROJECT_TOKEN` cannot read it, the PO picks the token that can (for example a fine-grained token with "Administration: read"); the gate is not weakened.

**Writing a story's board status.** A session writes a status with one command, not with hand-written GraphQL:

```
scripts/set-project-status --issue <n> --status "<Status name>" --repo <owner/repo> [--dry-run]
```

It prints `Status: #<n> <old> -> <new>` (or `already <new>`) and exits `0` only after it has read the status back from the board. It exits `1`, with one line on stderr naming the failed step, when there is no token, GraphQL refuses the call (the line names the missing scope and points to this section), the issue is not on the board, the status is not an option, or the read-back disagrees; `2` for invalid arguments. The commands keep the best-effort rule: a non-zero exit is recorded in the Session Summary with `--deviation`. The trial of ADR-012 skipped a write because no such command existed (the session reported "I found no script in this run that writes board status"), not because GraphQL or the token failed.

**If the secret is absent**, the `gh` CLI fails to authenticate, the workflow exits with an error on the first API call and no story is dispatched.

**If a scope is missing**, GitHub refuses the query that needs it. `hpw codex-review-bookkeeping` then exits non-zero and its error names the failed step, the missing scope and this section (see [Codex review loop](HEADLESS-PR-WORKFLOW.md#codex-review-loop) → Failure).

---

## GitHub writes — `hpw dispatcher write`

Every comment, board status and label the dispatcher writes goes through one building block, `scripts/hpw dispatcher write` (ADR-014 → Block 1, issue #432; code in `src/headless_pr_workflow/dispatcher/`). It runs `gh` with the token above, writes, reads the result back from GitHub and retries. A write that is not confirmed by the read-back is a failure, never a success.

```
scripts/hpw dispatcher write comment      --repo <owner/repo> --number <n> --body-file <path> [--attempts <n>] [--evidence-item <item> --evidence-head <sha|->] [--dry-run]
scripts/hpw dispatcher write board-status --repo <owner/repo> --issue <n> --status "<name>" [--attempts <n>] [--dry-run]
scripts/hpw dispatcher write label        --repo <owner/repo> --number <n> (--add|--remove) <name> [--attempts <n>] [--dry-run]
scripts/hpw dispatcher write retry-pending --repo <owner/repo> --target <n> --item <item> --ref <ref> --story <n> [--expected-head <sha>] [--dry-run]
```

- **stdout:** one JSON line, on success and on failure: `ok`, `block`, `operation`, `result`, `error` (`"<step>: <reason>"` or `null`), and per operation `url`, `created_at`, `attempts`, or `before`/`after` and `sent` for a board status (`sent` is `true` when a status mutation was sent, so a failed write may still have changed the board).
- **Exit codes:** `0` `written`, `already` or `dry-run` (`retry-pending`: `none`, `posted`); `1` `pending`, `failed`, `missing` or `stale`; `4` `retry-pending` stopped until the PO's `/unblock` (`gave-up`); `2` invalid arguments. A write never exits `3`.
- **Bounds:** at most `--attempts` tries (default 3), `COMMENT_RETRY_DELAY` seconds apart (default 2). A comment is posted at most once per call: before a retry the block looks for an identical comment of the dispatcher's own account that was not there before the first try. The comment body is sent on stdin (`gh api --input -`), never on the command line.
- **Evidence:** with `--evidence-item`, a comment that still fails is left as the evidence-pending comment of issue #348 (up to 3 more tries) and the result is `pending`. `retry-pending` posts that text on a later run, counts a failed run on the story, and returns `gave-up` when `EVIDENCE_RETRY_MAX` (default 3) is reached; the bash caller then posts the give-up blocker.
- **The token** and request headers are never printed.

The bash functions callers use (`post_comment`, `set_project_status`, `set_project_status_checked`, `set_project_status_ready`, `evidence_retry`, `label_pending_cleanup`) are thin shims around the block in `scripts/dispatcher-invoke.sh`; `HPW_BIN` (default `scripts/hpw`) names the `hpw` to run, as in `dispatcher-merge.sh`. `dispatcher-merge.sh`, `dispatcher-cleanup.sh` and `dispatcher-notify-retry.sh` call the block directly. Each call is a separate process, so a merged change to the block takes effect on the next call (see [The dispatcher script is read completely before any session starts](#the-dispatcher-script-is-read-completely-before-any-session-starts)).

**Board writes fail closed.** A board status that is not written stops its step and leaves a state a later run retries. The shim logs one line: `[BOARD ERROR] #<n>: could not set "<status>" (<error>)`.

| Where | Status | When it is not written |
|---|---|---|
| `finish_story` after a merge | `Done` | `/cleanup` does not run; the story stays `In merge`, so the merged-PR resume reruns it on a later run. When the write was sent but not confirmed, `In merge` is written back first; if that fails too, `Done` may be on the board and no later run would resume the story, so `/cleanup` runs now |
| `finish_story`, cleanup evidence missing | `In merge` | the run fails (non-zero) |
| Stale-story rollback | `Ready for implementation` | written before the Recovery Comment; no Recovery Comment is posted and the story is not counted as recovered, so the next run tries again without a duplicate comment. When the write was sent but not confirmed, `In implementation` is written back first; if that fails too, the rollback may be on the board, so the Recovery Comment is posted |
| Reserve skip and allowance error before `/implement` | `Ready for implementation` | no session starts (as before); the run still ends as handled |
| Hand-off, fix-round and pause rollbacks | `In review`, `Ready for implementation` | as before: the caller keeps its retryable state |
| E1, before `/implement` | `In implementation` | **best-effort**: the session still starts and the hand-off keeps the story from starting twice (ADR-014 → Decision → 7) |

**Change check.** `scripts/dispatcher-change-check.sh` has the check id `verified-write`: a `gh api` call with a write method, `-f body=` or `--input`, a GraphQL `mutation`, `gh issue|pr comment|edit|close|reopen|ready|merge|review`, `gh label`, or `curl` with a write method fails the check outside the shims above. It also checks the `scripts/lib/*.sh` files the dispatcher sources. Its exceptions are an allowlist of function name and check id in the script, one per "stays" row of ADR-014 → Decision → 7.

## Markers — `hpw dispatcher marker`

Every hidden dispatcher marker (`<!-- hpw:dispatcher action=<action> key=value ... -->`) is written and read through one building block, `scripts/hpw dispatcher marker` (ADR-014 → Block 2, issue #433). A marker counts only on a comment whose `user.login` is the account that should have written it, so a marker pasted by anyone else never stops a fix round, writes a board status or starts a merge.

```
scripts/hpw dispatcher marker write  --repo <owner/repo> --number <n> --action <action> [--field key=value ...] [--body-file <path>] [--once] [--attempts <n>] [--dry-run]
scripts/hpw dispatcher marker holds  --repo <owner/repo> --number <n> --marker <text-or-prefix> [--cancel <marker>] [--author dispatcher|bookkeeping]
scripts/hpw dispatcher marker list   --repo <owner/repo> --number <n> [--marker <m>] [--author dispatcher|bookkeeping]
scripts/hpw dispatcher marker whoami --repo <owner/repo>
```

- **Accounts:** resolved once per process. `dispatcher` is `DISPATCHER_LOGIN` or the login behind `GH_TOKEN`; `bookkeeping` is `CODEX_BOOKKEEPING_LOGIN` or the same login. `--author` defaults to `dispatcher`.
- **`holds`:** the newest own comment carrying the marker (a substring, so a prefix works) holds unless a later standalone `/unblock` line from `DECISION_BLOCKER_PO_LOGIN`, or a later own comment carrying `--cancel`, follows it. Result words: `holds` (exit `0`), `absent`, `unblocked`, `cancelled` (exit `1`), `unreadable` (exit `3`).
- **`write`:** posts the marker (after the text of `--body-file`, when given) through the verified write above, read-back included. With `--once` it posts nothing and reports `already` when the same marker holds; when the markers cannot be read it posts nothing and reports `failed`. The marker text is unchanged, so markers already on GitHub stay valid.
- **`list`:** the own comments (`id`, `created_at`, `body`), optionally only those carrying `--marker`. **`whoami`:** `dispatcher` and `bookkeeping`.
- **Fail closed:** a login that cannot be resolved (`gh api user` fails or returns an empty login, and `DISPATCHER_LOGIN` is unset) or comments that cannot be read give `unreadable` (exit `3`); no comment from any author then matches. The bash shim `marker_holds <issue> <marker> <on-read-error> [<cancel>]` needs the caller's direction as a mandatory `0` (treat as held) or `1` (treat as absent): a caller that stops when the marker holds passes `0`, one that acts when it holds passes `1`, so a failed read starts no session, posts no second blocker, writes no board status and merges nothing.
- **Output:** one JSON line on stdout (`ok`, `block: "marker"`, `operation`, `result`, `error`, plus `url`/`created_at` for `holds`, `comments` for `list`, `dispatcher`/`bookkeeping` for `whoami`); log lines on stderr; the token is never printed. Exit `2` is invalid arguments.

The bash functions callers use (`marker_holds`, `resolve_dispatcher_login`, `bookkeeping_login`, `bot_comments_json`, `marker_list_json`, `newest_own_comment_body`, the CLAIM/CONFIRM helpers of the decision-blocker resume) are thin shims in `scripts/dispatcher-invoke.sh`; `dispatcher-merge.sh` reads the owner marker with `holds`, `dispatcher-notify-retry.sh` reads the evidence with `list`, and `dispatcher-poll.sh` takes its login from `whoami`. The "already the newest comment" checks of the allowance, usage-limit and pause comments look at the newest comment of the dispatcher's own account.

**Change check.** `scripts/dispatcher-change-check.sh` has the check id `own-marker`: outside the shims above it flags a literal `hpw:dispatcher` or `hpw:codex-review-bookkeeping`, a `gh api user` or `/user` call, a jq filter on `.user.login`, and a match on `DECISION_BLOCKER_CLAIM_MARKER` or `DECISION_BLOCKER_RESUME_MARKER`. It covers `scripts/dispatcher-*.sh` and `scripts/lib/*.sh`. Its exceptions are an allowlist of function name and check id, one per "stays" row of ADR-014 → Decision → 7.

## Saved state — `hpw dispatcher state`

The state the dispatcher keeps between runs is read, checked and written through one building block, `scripts/hpw dispatcher state` (ADR-014 → Block 3, issue #434). A broken file is never used as if it were good and never overwrites good state, and a cancelled or re-run workflow does not lose state.

```
scripts/hpw dispatcher state read         --name <name> [--repo <owner/repo>]
scripts/hpw dispatcher state write        --name <name> --data-file <path> [--repo <owner/repo>] [--dry-run]
scripts/hpw dispatcher state read-pending --number <n> --item <item> [--expected-head <sha>] --repo <owner/repo>
```

| Name | File (formats and paths unchanged) | Malformed or unreadable |
|---|---|---|
| `actionable-events` | `.dispatcher-state/<owner>/<repo>/actionable-events` (`DISPATCHER_STATE_DIR`) | the retry step sends nothing and leaves the file; a malformed *line* is dropped while valid lines are kept; a new event is delivered directly |
| `usage-sessions` | `.dispatcher-usage/sessions` (`YYYY-MM-DD:<count>`, `USAGE_LIMIT_STATE_DIR`) | **starts no session**: `[USAGE LIMIT ERROR]`, exit `2`. A file dated another day is the normal start of a new day: 0 sessions |
| `usage-notified` | `.dispatcher-usage/notified` (`YYYY-MM-DD`) | the notification is sent again |
| `allowance-hold` | `.dispatcher-allowance/hold.json` (`ALLOWANCE_STATE_DIR`) | the allowance check probes again |

- **`read`** never changes the file. Result words and exit codes: `ok`, `missing` (the name's empty value) exit `0`; `malformed`, `stale` (another day, for the two day files) exit `1`; `unreadable` (not a regular file, or it cannot be read) exit `3`. `read` prints `reason` (`not-a-file` or `read-failed`) for `unreadable`.
- **`write`** checks the data against the name's format, writes a temporary file, renames it into place and reads it back (`written`, exit `0`; `failed`, exit `1`, with one stderr line naming the file and the step). A path that is not a regular file, or a file that cannot be read, is moved aside to `<file>.unreadable` and never overwritten. A write stopped before the rename leaves the old file whole. `--dry-run` writes nothing and returns `dry-run`.
- **`read-pending`** finds the dispatcher's own evidence-pending comment through Block 2 and returns `none`, `ok` (with `payload`), `stale-head` (the payload was recorded for another head), `malformed` or `unreadable`. `write retry-pending` reads its payload through it.
- **Output:** one JSON line (`ok`, `block: "state"`, `operation`, `result`, `name`, `data` or `payload`, `error`); exit `2` is an unknown name or invalid arguments. The block adds no wait and no retry.
- The bash callers are thin shims: `load_state` and `save_state` in `dispatcher-notify-retry.sh`, `read_count`, `record`, `notify-due` and `mark-notified` in `dispatcher-usage-limit.sh`, and the hold in `dispatcher-allowance.sh`.

**Workflow rules** (`.github/workflows/dispatcher.yml`): every `actions/cache/save` key contains `${{ github.run_id }}-${{ github.run_attempt }}`, so a re-run never collides with the first attempt, and restores match by prefix, so the newest snapshot wins; every state save has `always()` in its `if:`, so it also runs after a failed or cancelled step; only the state writer (a scheduled run, or a run on the default branch) saves `.dispatcher-state`, and other refs run the poll, invoke, trigger-check and retry steps with `--dry-run`.

**Workflow steps are scripts.** Every `run:` in `dispatcher.yml` is one command: `bash scripts/dispatcher-workflow-step.sh <step>`, or the pinned `npm install`. The step script holds the guard (`guard`), the state-writer rule (`scope`), the cache dates (`cache-date`, `cache-save-date`), `gh auth setup-git` (`git-auth`), the `--dry-run` and `--refine` choices (`poll`, `invoke`, `trigger-check`, `retry`) and the usage-limit notification context (`usage-limit-notify`). The poll reports `next_issue`, `next_action`, `scheduler_result`, `evidence_pending` and `resumable` in its JSON; a failed read of the `evidence:pending` label counts as pending.

**Change check.** `scripts/dispatcher-change-check.sh` has the check id `saved-state`: outside the shims above it flags a redirect, `cp`, `mv`, `rm`, `cat`, `read <` or `mkdir` on a path under `.dispatcher-state`, `.dispatcher-usage` or `.dispatcher-allowance`, or on a variable named `*STATE_DIR`, `*STATE_FILE`, `HOLD_FILE`, `SESSIONS_FILE` or `NOTIFIED_FILE`. On `dispatcher.yml` it flags an `actions/cache/save` key without `github.run_attempt`, a cache save whose `if:` has no `always()`, and a `run:` step that calls `gh` directly.

## Slack Notifications

The dispatcher sends Slack notifications through `scripts/slack-notify.sh` for these events. There are no "story dispatched" or "closed" events: normal transitions are not notified.

| Event | Sent when | Retried |
|---|---|---|
| `decision_blocker` | a session left a `Type: decision` blocker, whether it failed or exited 0 (a first or fix-round `/implement`, or a `/review` of a Codex-implemented story, that leaves an Out of scope finding waiting for the PO's waiver) | yes |
| `po_blocker` | the dispatcher posted a blocker only the PO can resolve (issue #359): fix-round maximum, "In merge" with no PR, evidence retry given up, a failed or no-progress `/implement`, `/review` or `/merge` | yes |
| `evidence_unwritable` | required merge or cleanup evidence has been missing for 15 minutes and the story's comments cannot be written, so #348's blocker cannot be posted (issue #372) | yes |
| `feature_closure_confirmation` | all stories of a Feature are Done | yes |
| `epic_closure_approval` | all Features of an Epic are Done | yes |
| `red_flow_health` | the board health is Red | re-sent on every poll while Red |
| `budget_cap_reached` | every ready story is budget-blocked | no |
| `dispatcher_error` | the invoke run exited unexpectedly | no: the failing run is itself red |
| `dispatch_trigger_stopped` | the newest `workflow_dispatch` run of `dispatcher.yml` on `main` is more than 15 minutes old, or none exists (issue #339, [Dispatcher trigger](#dispatcher-trigger)) | yes, until a newer dispatched run exists |

**Retry of notifications that need PO action (issue #295).** `dispatcher-invoke.sh` queues `decision_blocker`, `po_blocker`, `evidence_unwritable`, `feature_closure_confirmation` and `epic_closure_approval`, and `scripts/dispatcher-trigger-check.sh` queues `dispatch_trigger_stopped` (issue #339; it is resolved when a newer dispatched run exists, uses issue `0` and never posts an issue comment, so a persistent delivery failure only turns the run red), in `$DISPATCHER_STATE_DIR/<owner>/<repo>/actionable-events` (restored and saved by the `actions/cache` steps of [Dispatcher state cache](#dispatcher-state-cache)). The workflow step **Retry actionable Slack notifications** (`scripts/dispatcher-notify-retry.sh run`) runs on every cycle, also when no story is ready or resumable, sends each undelivered event and records it as delivered only after `slack-notify.sh` exits 0. A delivered event is not sent again while it stays unresolved. An event stops being retried, even if it was never delivered, when it is resolved: a decision blocker or `po_blocker` by a `/unblock` line from the PO after the declaration or by closing the issue, a closure event by closing the Feature or Epic, `evidence_unwritable` when the evidence is posted by the dispatcher's own account or #348's give-up blocker for the item is on the story (a closed story does not resolve it, because the cleanup case concerns a story that is already `Done`). A new decision blocker or `po_blocker` on the same issue is a new event (the key is the `created_at` of the declaration comment). `po_blocker` is queued by `post_blocker_once` and `post_mid_cycle_blocker` for every `Type: resource` or `Type: external` blocker the dispatcher posts; `Type: conflict` blockers (WIP limit, file overlap) clear on their own and send nothing. After a session that exits 0 the event is queued only while the blocker is open, that is, no later PO comment has a line that is exactly `/unblock` (the rule of `docs/PROJECT-STATUS.md`, Decision blockers); the state de-duplicates a re-run over the same declaration (issue #357). A blocker the dispatcher does not post again because its marker still holds queues no second event. Before `dispatcher-invoke.sh` chains to the next story, and after each story the resume pass handles ("In merge", fix rounds, pending hand-offs, "In review"; issue #349), it makes one extra delivery attempt (`run --drain`), so an alert is not held back by the chained or later resumed sessions or by the `/implement` session that follows the resume pass; that attempt records a success but never counts a failure. If the state file cannot be written, `register` delivers the event synchronously instead and exits non-zero when that fails too, and the retry step ends the run red with an `[NOTIFY] Error:` line. A state file that exists as a regular file but cannot be read (issue #352) is never overwritten or removed, because the events in it stay recoverable: `run` sends nothing, leaves the file unchanged, writes an `[NOTIFY] Error:` line and exits 1; `run --drain` writes a `[NOTIFY] Warning:` line and exits 0; `register` delivers the event directly (exit 0 on success, 1 on failure) and writes no state file. Once the file is readable again the next `run` delivers what it holds. When `register` exits non-zero, `dispatcher-invoke.sh` carries on with its loop, writes an `Error:` line naming the event type and issue number, and exits 1 at its end, also across a chained `exec`; this is not reported as `dispatcher_error`. The steps after the invoke step (usage-limit notice, session-state save, retry, state save) still run. The `.dispatcher-state` cache is also saved when the job is cancelled, so an event queued during the invoke step survives; the save keeps its guards (enabled, successful poll, default-branch state writer).

**Bound and red run.** A failed delivery never aborts the dispatcher; the step exits 0 and retries on the next cycle. After 3 failed cycles in a row (about 15 minutes) the step exits 1, so the run ends red, and one comment is posted on the affected issue naming the event and the first failed attempt. Later failing cycles stay red but add no comment, and Slack still receives the message when it recovers. Missing state starts empty; in a malformed state file only the malformed lines are dropped and valid records are kept; a state file that exists but cannot be read is left unchanged and makes `run` red (see above). Under `--dry-run` nothing is sent, recorded or commented. A manual run on a non-default ref only reports what it would send; an event queued in such a run is not saved and is not delivered.

To enable notifications, add `SLACK_WEBHOOK_URL` as a **repository secret** (not a variable):

`https://github.com/<owner>/<repo>/settings/secrets/actions`

| Secret | Required value |
|---|---|
| `SLACK_WEBHOOK_URL` | Incoming webhook URL from your Slack app (format: `https://hooks.slack.com/services/...`) |

The dispatcher workflow passes this secret to the shell environment automatically — no changes to workflow files are needed beyond what is already wired.

**If the secret is absent or invalid**, `slack-notify.sh` exits with code 1 and a missing webhook never aborts the dispatch work. The non-retried notifications (`budget_cap_reached`, `dispatcher_error`) fail silently and the run continues normally. The retried actionable events stay queued: after 3 failed cycles in a row the **Retry actionable Slack notifications** step turns the run red and comments once on the affected issue, until the secret is fixed.

`.github/workflows/codex-review-bookkeeping.yml` also reads `SLACK_WEBHOOK_URL`, in its notify step only, to send a `dispatcher_error` message when a bookkeeping run fails (at most one a day while the failure lasts; see `HEADLESS-PR-WORKFLOW.md` → "Codex review loop"). A missing secret there is a warning, not a failure of its own.

For local development, copy `.env.example` to `.env` and set your webhook URL there. `.env` is listed in `.gitignore` and must not be committed.

### Dispatcher state cache

GitHub-hosted runners are ephemeral, so `.github/workflows/dispatcher.yml` persists `.dispatcher-state/` (the `actionable-events` state above) with `actions/cache`. The step names come from issue #257, when the cache held the retired refinement-notification state (issue #474):

1. **Restore refinement notification state** runs before **Run dispatcher poll** on every enabled run. Its `dispatcher-state-` prefix restores the most recently saved snapshot.
2. **Save poll notification state** and **Save refinement notification state** save it on every enabled default-branch run after a successful poll. Cache entries are immutable, so each save uses a unique key with `<run_id>-<run_attempt>`.
3. **Default branch only writes state.** Actions caches are scoped by ref, so **Resolve notification state scope** marks every scheduled run, and every manual run dispatched on the default branch, as the state writer. A manual `workflow_dispatch` on any other ref restores the default-branch snapshot and runs the poll, invoke, trigger-check and retry steps with `--dry-run`; the save steps are skipped.
4. A workflow-level `concurrency` group (`dispatcher-<owner>/<repo>`, `cancel-in-progress: false`) serializes scheduled and manual runs, so no two runs read the same snapshot and race to write it.

**Interim mechanism:** this cache exists only because the dispatcher runs on ephemeral GitHub-hosted runners. #259 moves the dispatcher to persistent infrastructure, where local disk supersedes the cache steps.

---

## Executable Issue Types in the Invoke Loop

Only Stories, Tasks, and Bugs are executable units for `/implement`. Before executor-label routing, `scripts/dispatcher-invoke.sh` classifies every story the [scheduler](#scheduler-new-work-from-github-facts) chose for `/implement`, or `--issue` named (issue #256):

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

A Claude-implemented story is reviewed by Codex Cloud, not by a dispatcher session (ADR-006, issue #293). Codex answers asynchronously, so the dispatcher ends the story's run after `/implement` and picks the story up again on a later run. Everything is decided from GitHub facts read at the start of that run: the board status, the linked PR and hidden markers in comments. Nothing is remembered on the runner. The dispatcher honours its own markers only on comments written by its own account (the login behind `GH_TOKEN`), and the bookkeeping job's marker only on comments by that job's account, so a marker pasted into a comment by anyone else has no effect. Both rules live in `hpw dispatcher marker` (see [Markers](#markers--hpw-dispatcher-marker)).

The `codex-review-bookkeeping` workflow does the part in between. It acts only on a story in `In review`, and moves it to `In merge` after a clean review with green required checks and a passed approval gate, or to `In implementation` after findings. `/implement` posts the review request; the dispatcher posts none.

### Hand-off after `/implement`

For a story whose executor has a `CODEX_REVIEW_LOOP_ROUTING` row, after `/implement` succeeds the dispatcher:

- finds the linked open PR. With no linked open PR, or a PR that is still a draft, it posts the mid-cycle blocker for `/implement`, leaves the story in `In implementation` and exits `1`;
- when that draft PR conflicts with main (`mergeable == false` or `mergeable_state == "dirty"`, read from `gh api repos/<repo>/pulls/<n>`), posts a blocker that names the merge conflict with main, says no CI run can start and gives "the PR branch is merged with main and the conflict resolved" as the unblock condition; it still exits `1`. A clean PR, an unknown mergeability (`null`) and a failed read keep the generic blocker. GitHub starts no `pull_request` workflow for a conflicting PR, so the required `pytest` check stays absent; this, not the push token, stalled PR #371. `/implement` merges `origin/main` into the branch before its final push and reports `merge conflict with main` when it cannot resolve the conflict (issue #381);
- writes `In review` only when the story is still `In implementation` and no fix round is due. It never overwrites `In review` or `In merge`, because the bookkeeping job may already have acted on Codex's answer;
- writes nothing when Codex answered with findings while the session was still running: the story is already back in `In implementation` with a fix round due on the new head, and the next run starts it;
- when that `In review` write fails, or the story is still in "Ready for implementation" because no status write of the run took effect, posts one recovery comment with a hidden marker for the PR and exits `1`. The story is not reported as handed off and is not picked again as the next ready story. A later run finds the marker on a story still in `In implementation` or "Ready for implementation", with a non-draft PR and no fix round due, and completes the hand-off without starting `/implement` again (`[RESUME] #<n>: completing the hand-off on PR #<pr>`), also when the PR head has changed since. It then posts a second marker, so the hand-off is completed once. Without this the bookkeeping job, which acts only on `In review`, would never see Codex's answer;
- starts no `/review` session;
- logs `[HANDOFF] #<n>: handed to the Codex review loop (Codex Cloud) on PR #<pr>; no /review session started` and goes on to the next ready story.

### Resume at `In merge`

A story in `In merge` is taken up at `/merge`, whatever its executor. `/merge` and `/cleanup` here are the script-only phases (issue #264): no session starts and no allowance is checked. The board status is not trusted on its own: the merge gates inside `scripts/dispatcher-merge.sh` decide whether the PR merges.

| Facts | What the dispatcher does |
|---|---|
| A linked open PR that is not a draft, and GitHub reports it clean | `/merge`, then `Done` and `/cleanup`, then the feature and epic closure check. Log: `[RESUME] #<n>: /merge PR #<pr>` |
| That PR conflicts with main, and the story is a review-loop story (one `executor:` label with a `CODEX_REVIEW_LOOP_ROUTING` row) | No `/merge` and no merge-attempt marker. When the [conflict-round rule](#conflict-round) owes a round, it starts after every `/merge` of the run. Log: `[RESUME] #<n>: PR #<pr> conflicts with main; the conflict round starts after this run's merges` |
| That PR conflicts with main, and the story has no review-loop row (for example `executor:codex`) | No `/merge` and no session. One `Type: external` blocker that names the merge conflict with main and the PR, once per PR head, sent as a `po_blocker`. Unblocked when: the PR branch is merged with main and the conflict resolved, or the PO posts `/unblock`. |
| GitHub has not computed the PR's mergeability yet (`mergeable == null`) | Nothing in this run: no `/merge`, no conflict round, no marker, no blocker. Log: `[RESUME SKIP] #<n>: GitHub has not computed mergeability for PR #<pr> yet; trying again on the next run` |
| No open PR, and a merged PR that closes the story | `Done` and `/cleanup`, no `/merge`. This finishes a run that stopped between merge and cleanup. |
| Neither (including a PR that is still a draft) | One `Type: external` blocker on the story, posted once. |

"Conflicts with main" is the test of the hand-off above: `mergeable == false` or `mergeable_state == "dirty"` from `gh api repos/<repo>/pulls/<n>` (issue #409). When that read fails, `/merge` runs as before and its merge gate decides.

**Evidence that could not be written (issue #348).** The `merge-owner` marker, the merge confirmation and the cleanup closing Session Summary are durable evidence. When a write of one fails (after 3 attempts), the writer leaves one *evidence-pending* comment with the exact text as a hidden base64 payload (`scripts/lib/evidence-pending.sh` format; the merge confirmation and the closing summary through `hpw dispatcher write comment --evidence-item`; nothing is kept on the runner). The dispatcher posts that text once on a later run. Only comments of the dispatcher's own account count, for the pending comment and for the proof that the text is already posted; a copy written by anyone else proves nothing.

| Item | Pending comment on | Posted by a later run |
|---|---|---|
| `merge-owner` | the PR, for the head the `/implement` session produced | before `/merge`, and only while the PR is still at that head; a pending marker for another head is never posted (`[RESUME SKIP] ... is for head <sha>, not <sha>`) |
| `merge-confirmation` | the PR, for the merged head | before `Done` and `/cleanup`; the story stays `In merge` until it is posted. A merge script that merged and then failed only on this comment is not a merge blocker |
| `cleanup-summary` | the story, which is already `Done` | by the resume pass, through the `evidence:pending` label (see below) |

A retry posts nothing when the exact text is already on the issue or PR (no duplicate), and `/merge` is still not run twice for one head. Each failed run adds a `evidence-retry-failed` marker comment on the story. After 3 failed runs for the same item the dispatcher posts one `Type: external` blocker on the story, which sends one retried Slack `po_blocker` (no `dispatcher_error`), and stops (`[RESUME SKIP] ... retrying stopped after 3 runs, waiting for a PO /unblock line`). Other stories are not held back. A standalone `/unblock` from the PO, after the evidence is posted by hand or the cause is fixed, starts the count again. Log lines: `[RESUME] #<n>: posting the missing <item> evidence on #<target>`, `[RESUME SKIP] #<n>: the <item> evidence ...`.

**The `evidence:pending` label.** No board state leads back to a `Done` story, so when `/cleanup` leaves the closing summary as a pending comment the dispatcher also adds the label `evidence:pending` to the story. Each resume pass lists the issues with that label (one call, whatever their age), posts each pending summary once, and removes the label once the summary is on the story or nothing of the dispatcher's own is pending. While retrying has stopped after the 3-run limit, the label stays until the PO's `/unblock` lets the next run post the summary. The label is only an index: anyone can add or remove it, but what is posted is still read from the dispatcher's own pending comment, so a label added by someone else only causes a read. The workflow's poll step also lists the label (one call per cycle) and counts a labelled story as resumable work, so `dispatcher-invoke.sh --resume` runs even when nothing is ready, `In merge` or `In implementation`; a failed read of the label counts as pending. The dispatcher creates the label in the repository before it first adds it, because GitHub refuses to add a label the repository does not have; you can also create it by hand. A failed label write counts as no pending copy (gap (2) below).

**The story cannot be written (issue #372).** #348 counts failed runs with marker comments on the story, so it counts nothing when the story's comments cannot be written. When a run cannot write the `evidence-retry-failed` marker or the give-up blocker on the story, the dispatcher measures how long the item has been missing from a GitHub timestamp: the `created_at` of its own evidence-pending comment, or else the linked PR's `merged_at` (`merge-confirmation-missing`, `cleanup-summary-unrecorded`). At 15 minutes or more (`EVIDENCE_UNWRITABLE_MINUTES`, default 15) it queues one `evidence_unwritable` event in the notification state, keyed by story, timestamp, item and ref, so a later run queues no second one and a different item is a separate event. It logs `[RESUME SKIP] #<n>: the <item> evidence on #<target> has been missing for <minutes> min and the story cannot be written; PO alert queued`. The Slack message names the story, the item, the PR or issue it belongs on, the minutes, and what the PO can do: fix the cause of the failed write or, when a pending comment exists, post its text by hand. The event is retried like the other actionable events and stops when the evidence is posted or the give-up blocker is on the story. Only comments of the dispatcher's own account count. The state decides only whether to send the alert; merge and cleanup decisions still use GitHub facts. If the state is lost, one extra alert is possible. Nothing is queued or sent under `--dry-run`.

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

**Maximum fix rounds.** The dispatcher counts the PR heads for which the bookkeeping job recorded findings, one per head. When the count is above `CODEX_FIX_ROUND_MAX`, it starts no session, logs `[RESUME SKIP] #<n>: fix round <k> on PR #<pr> is above the maximum of <max>` and posts one `Type: resource` blocker on the story, once; the blocker is also sent to Slack as a `po_blocker` event (type, `Unblocked when`, state of the work, a link and the `/unblock` resume line), retried like `decision_blocker`. A round with only Out of scope findings pushes no new head, so it starts no further round; the story waits for the PO's waiver.

| Variable | Valid values | Default |
|---|---|---|
| `CODEX_FIX_ROUND_MAX` | whole number of `1` or more | `3` |

Set it as a repository variable; `.github/workflows/dispatcher.yml` passes it to the invoke step. An unset or empty variable uses the default. Any other value stops the run with an error before any session starts.

**One fix-round attempt per head.** A fix round that fails, or an `/implement` session that ends on the same PR head it started on while the findings for that head are still open, is not repeated on the same head, whatever status the session left the story in. The mid-cycle blocker for `/implement` records the head, and later runs log `[RESUME SKIP] #<n>: the fix round was already tried on PR #<pr> at head <sha>` until the PR has a new head or the PO posts a standalone `/unblock` line.

### Conflict round

When the dispatcher merges one story, another dispatcher PR can start to conflict with `main` (issue #409). For a review-loop story (exactly one `executor:` label with a `CODEX_REVIEW_LOOP_ROUTING` row) the dispatcher then resolves the conflict itself, in a *conflict round*: one `/implement` session under the story's own executor that merges `origin/main` into the PR branch, resolves the conflict, runs the focused tests and pushes (`.claude/commands/implement.md` step 10).

**The rule (issue #449).** A stopped conflict round is never resumed. The dispatcher keeps no record of an open round. Every run decides from GitHub facts only, with one rule that `scripts/dispatcher-poll.sh` and the resume pass of `scripts/dispatcher-invoke.sh` share (`conflict_round_rule` in `scripts/lib/conflict-round.sh`). A conflict round is owed when all of these hold, and only then:

- the story is a review-loop story in `In implementation`, `In review` or `In merge`;
- its linked open, non-draft PR conflicts with main (`mergeable == false` or `mergeable_state == "dirty"`);
- no fix round is due: no bookkeeping `move-in-implementation` marker for the current head without a fix-round attempt marker that holds for it. A completed fix round on the head (its findings and its attempt marker) does not hide an owed round;
- the story has no open blocker: a `## Blocked Declaration` with no later standalone `/unblock` line from the PO. A dispatcher blocker whose hidden marker names an older PR head is not open;
- no conflict-round attempt marker holds for the current head.

The poll lists such stories in `In review` under `conflict_due`; stories in `In implementation` and `In merge` are already listed under `in_implementation` and `in_merge`. The next run starts one fresh round.

What the round does:

- It runs no WIP or file-overlap check: the story already holds its place.
- After the usage limit and the allowance check let the session start, the story moves to `In implementation`. A refused start, or a failed write of that status, changes nothing: the story keeps its status, and the next run applies the rule again. Log: `[RESUME] #<n>: PR #<pr> conflicts with main; conflict round, /implement on PR #<pr>`.
- The session keeps the PR Ready for review for the whole round (implement.md step 10), also when its `pytest` check fails or is still pending at the end, so a round that stops leaves a PR the rule can read.
- Success is judged from the PR, not from a new head. After a session that exits `0`, the dispatcher reads the PR again. When it no longer conflicts and the newest run of the required `pytest` check on the current head completed with success, the dispatcher posts the merge-owner marker for the current head (or its evidence-pending copy) and applies the [hand-off](#hand-off-after-implement): `In review`, and the session requests the Codex review. The head reaches `In merge` only through the bookkeeping job (a clean review on that head, green required checks, a passed approval gate), and a later run merges it with the unchanged script-only `/merge` and `/cleanup`. No PO action is needed.
- No PO alert is sent while the dispatcher resolves the conflict.

**One conflict round per head.** When the session fails, the PR still conflicts after it (the session reports `merge conflict with main on PR #<n>`), or the `pytest` check on the head is not green (failed, still running, absent or unreadable), the dispatcher posts one `Type: external` blocker on the story and exits `1`. The blocker names the merge conflict with main and the PR, says it is the PO's only alert for this conflict, and gives "the PR branch is merged with main and the conflict resolved, or the PO posts /unblock" as the unblock condition. It carries a hidden attempt marker for the PR's current head and is sent once as a `po_blocker`. Later runs start no second round on that head and log `[RESUME SKIP] #<n>: the conflict round was already tried on PR #<pr> at head <sha7>; waiting for a new head or a PO /unblock line`. A new head, or a standalone `/unblock` line from the PO, allows one new attempt.

**A stopped round.** A usage-limit pause posts only the standard [pause record](#pause-at-a-provider-usage-limit), with step `conflict round`, for the PO's information; the dispatcher never reads it. A cancelled run or a failed write of the pause record leaves nothing at all. In each case the next run applies the rule to the PR as it is then. While the PR still conflicts, a fresh round starts. When the session pushed before it stopped and the PR is clean, its head has no merge-owner evidence, and the story gets the blocker below. Only pushed commits survive.

**A head left without an owner.** A review-loop story is `In implementation` or `In review`, its linked open non-draft PR is not conflicting, no fix round is due, no hand-off is pending, and the current head has no merge-owner evidence: a session stopped after its push, before the hand-off finished. The dispatcher starts no session. It posts one `Type: resource` blocker, once per head, sent as one `po_blocker`. The blocker says that the merge owner is missing for head `<sha7>` on PR #<pr>, that a session stopped after its push before the hand-off finished, and that the PO can follow [`docs/TAKEOVER-RULES.md` → Merge Takeover](TAKEOVER-RULES.md) or post `/unblock` after pushing a new head. Log: `[RESUME SKIP] #<n>: PR #<pr> head <sha7> has no merge-owner evidence; blocker posted`. A later run on the same head posts nothing more. The poll reports such a story in `In review` under `owner_missing_due` until the blocker is posted, so the workflow runs the invoke step even when nothing else is due; a story in `In implementation` is already reported under `in_implementation`. The blocker applies to every review-loop story, so a fix round or a first `/implement` stopped after its push gets it too. **In `In merge` (issue #406).** The same rule holds for every story `In merge`, whatever its executor: when the linked open non-draft PR is not conflicting and its head still has no dispatcher merge-owner marker after the pending copy was posted (`ensure_merge_owner`), the dispatcher does not run `/merge`. It posts one `Type: resource` blocker per head with the `merge-owner-missing` marker, sent as one `po_blocker` (no `merge-attempt` marker, no `dispatcher_error`). The blocker says that the head was not pushed by a dispatcher session (for example a manual fix round), so the dispatcher does not merge it, and that the PO merges PR #<pr> by hand with `/merge <pr>` and then runs `/cleanup <pr>` ([`docs/TAKEOVER-RULES.md` → Merge Takeover](TAKEOVER-RULES.md)). Later runs on the same head, also after `/unblock`, log `...; the blocker is already posted` and post nothing. A head with the marker or a pending copy for exactly that head still goes to the merge script. A manual fix round after `fix-round-max` ends the same way: the head it pushes is merged by the PO by hand. If the story stays `In merge` after a manual merge, the merged-PR path asks for the dispatcher's own merge confirmation (issue #348 give-up blocker applies).

**Not a fix round.** Conflict rounds do not count toward `CODEX_FIX_ROUND_MAX`: only the bookkeeping job's findings markers count. A story without a review-loop row gets no conflict round; see the `In merge` table.

### Order and entry into a run

Each run handles resumable work before it starts new work:

1. stories in `In merge`. The board is read again after each one that finishes, so the feature and epic closure check sees stories finished earlier in the same run as `Done`. A conflicting PR of a review-loop story is only queued here;
2. fix rounds that are due, and hand-offs that are still pending;
3. stories in `In review` (a `/review` session for a Codex-implemented PR; a conflicting review-loop PR is queued);
4. the queued conflict rounds. Every `/merge` of the run has run by then, so each round starts from the newest `main`;
5. then one [scheduler](#scheduler-new-work-from-github-facts) decision for new work, asked right then.

`scripts/dispatcher-poll.sh` reports the candidates in more keys of its JSON, `in_merge`, `in_implementation`, `review_due`, `conflict_due` and `owner_missing_due` (each `[{number, title}]`). `conflict_due` lists the review-loop stories in `In review` that the [conflict-round rule](#conflict-round) owes a round. `owner_missing_due` lists, by the same rule, the review-loop stories in `In review` whose clean head has no merge-owner evidence and no missing-merge-owner blocker yet ([A head left without an owner](#conflict-round)). Together they cost one PR read per review-loop story in `In review` per poll, and two comment reads when GitHub has computed that PR's mergeability. A story whose PR or comments cannot be read is not listed, and the poll writes `[POLL] Warning: could not read PR #<pr> for #<n>; conflict_due and owner_missing_due skip it this cycle` or `[POLL] Warning: could not read the comments of #<n> or PR #<pr>; conflict_due and owner_missing_due skip it this cycle`. The poll also reports the scheduler's choice (`next_issue`, `next_action`, `scheduler_result`). `.github/workflows/dispatcher.yml` starts the invoke step, and the steps that prepare a session before it, when the scheduler chose new work (`next_issue` is set) **or** any of these lists is non-empty:

```
bash scripts/dispatcher-invoke.sh --repo <owner/repo>               # resumable work first, then the scheduler's new work
bash scripts/dispatcher-invoke.sh --repo <owner/repo> --issue <n>   # resumable work first, then story <n> (manual)
bash scripts/dispatcher-invoke.sh --repo <owner/repo> --resume      # resumable work only (manual)
```

The workflow always uses the first form (or `--refine <n>` for the `refine_issue` input); `--issue` and `--resume` stay for manual runs. It runs the invoke step when the scheduler chose new work, when a story is `In merge` or `In implementation`, when `review_due`, `conflict_due` or `owner_missing_due` is non-empty, or when an issue carries the `evidence:pending` label (issue #348). A run in which nothing is due starts no session and exits `0`.

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
- Stale recovery does not roll back a story that waits for a fix round. While the allowance is below the reserve, the run ends at the fix round, before the stale check. A story held by one of the two fix-round blockers above, by a conflict-round blocker or by the missing-merge-owner blocker is skipped by the stale check: it waits for the PO.
- A session that stops after its push, before the hand-off finished, is not continued, except a fix round stopped by a provider usage limit (issues #446, #447), whether the session had already written `In review` or not: the pause finishes it in the same run by recording the merge owner for the pushed head and handing it off. In the remaining cases the story gets one `Type: resource` blocker for a head without merge-owner evidence ([conflict round](#conflict-round) → A head left without an owner), and the PO finishes it by a merge takeover or a new head. This applies to every review-loop story in `In implementation` or `In review`, including one whose PR was implemented outside the dispatcher; a story `In merge` gets the same blocker with the manual-merge way out (see above).
- A story paused at a provider usage limit waits at its step (fix round, `/review`) or in `Ready for implementation` (new `/implement`) until the usage limit and the allowance check allow a start; the run ends at that refused start, before the stale check, so stale recovery does not roll back a fix-round or `/review` story. Only pushed commits survive a pause.
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

A refusal partway through a cycle leaves the story at its current step. A story in `In merge` is not affected: its `/merge` is a script and never checks the allowance (issues #293, #264). A refusal or an error before a fix round changes nothing on the board: the story keeps its place in `In implementation`, no comment is posted, and the fix round is tried again on a later run, before new work. For `/review` a later poll tries the step again once the allowance allows it. `/merge` and `/cleanup` are script-only and are never refused or held by the allowance (issue #264). Under `--dry-run`, the dispatcher logs `[DRY RUN] Would check subscription allowance before /<step>` and does not run the script.

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

## Refinement sessions

`bash scripts/dispatcher-invoke.sh --repo <owner/repo> --refine <n> [--dry-run]` starts one refinement session for issue `<n>` (issue #319, ADR-012, Feature #460 criterion F2.5). It runs `.claude/commands/refine.md` with `$ARGUMENTS` set to `<n>` through `invoke_executor_command()`, so the token cleaning, the `ANTHROPIC_*` scrub, `CLAUDE_SESSION_ARGS`, the session count and the log lines are the implementation session's. `<n>` must be a positive whole number; `--refine` cannot be combined with `--issue` or `--resume` (exit `1`, nothing starts).

**How it starts.** In the scheduled run a `refine #N` decision of the [scheduler](#scheduler-new-work-from-github-facts) starts it, after the work in flight (issue #474), and the run ends after the session. A manual run also starts it: **Actions → Dispatcher → Run workflow**, with the input `refine_issue` set to the issue number. With `refine_issue` set the run passes the guard, installs Claude Code, restores the session state cache and runs only `dispatcher-invoke.sh --refine <refine_issue>` in the "Run dispatcher invoke" step, whatever the poll found; the usage-limit notification step is unchanged. With `refine_issue` empty the run is as before.

**Tier.** The model comes from the issue's type, not from its `executor:` label: `type:epic` and `type:feature` run on `opus`, every other issue (`type:story`, `type:task`, `type:bug`, or no `type:` label) on `sonnet`. The rule is fixed in `refine_executor_label()`; there is no repository variable for it.

**Gates, reused.** The pause switch (`DISPATCHER_ENABLED` not `true`: the workflow guard starts nothing, also with `refine_issue` set), the usage limit ([Usage limit](#usage-limit): a refinement session counts once, when it starts) and the allowance check ([Subscription allowance and reserve](#subscription-allowance-and-reserve): below the reserve in either window, no session starts). A refused start posts the same one comment on the issue as for `/review` and exits as for any session.

**One session, no branch.** The mode starts exactly one model session and then exits: no resume pass, no `/implement`, no `/review`, no `/merge`, no `/cleanup`, no chained story. The dispatcher runs no `git push`, no `git checkout -b` and no `gh pr create` and writes no board status; the `/refine` command writes the board status, and never the dispatcher moves the issue to "Ready for implementation". The workflow's `concurrency` group still serializes runs.

**Outcomes and log lines.**

| Outcome | Run | Written |
|---|---|---|
| Session finished (`result` success) | exit `0`, `[REFINE] #<n>: /refine finished` | if the session left an open decision blocker, one `decision_blocker` event (as after `/implement`) |
| Provider usage limit (`rate_limit`) | exit `0`, `[SESSION RATE LIMIT] /refine #<n> ...` | one `## Paused at a provider usage limit` comment with step `/refine <n>`; no board write, no `dispatcher_error`; a later `--refine <n>` starts a new session from the issue as it is |
| Failed session, wall-clock stop or 401 | exit `1`, `[SESSION ERROR]` line | the failure blocker and `po_blocker` event of a failed `/implement`; the 401 text is the same (run `claude setup-token` again and save the secret with nothing before or after it) |

`[REFINE] #<n>: starting /refine on <model>` is logged before the gates run. `--dry-run` prints `[DRY RUN] Would start Claude Code (<model>) on the subscription for /refine <n>` and starts nothing.

**After `/unblock`.** A refinement decision blocker answered with a standalone `/unblock` line is resumed by a fresh `--refine <n>` session that reads the answer (`refine.md` → Tech Lead rules → Resume after `/unblock`). The mode posts no CLAIM or CONFIRM marker and moves nothing; the stale-recovery and decision-blocker resume of the implementation pass read only "In implementation" stories and do not see a story in "In refinement". Deciding when that session is due is #473.

## Pause at a provider usage limit

A session stopped by the subscription's provider usage limit (issue #316, ADR-012) is not a failure and needs no PO action: the limit ends by itself at the reset.

**Detection.** `claude_limit_stop` in `scripts/lib/claude-session.sh` decides it from the session's stream: a final `result` that is not a success (`is_error` true or `subtype` not `success`) and has `rate_limit` or `rate_limit_error` in `error`, `subtype`, `error_type` or `error_category`, or `api_error_status` 429; or a `rate_limit_event` with `rate_limit_info.status` `rejected` when the stream has no successful result. Everything else (non-zero exit without a limit signal, wall-clock bound, 401, `apiKeySource` not `none`, missing `result`) stays a session error with the mid-cycle blocker. `run_claude_code_session()` returns `75` for a limit stop and sets `CLAUDE_LIMIT_RESET_TS` to the reset time in Unix seconds (`rate_limit_info.resetsAt`, else the latest `unifiedWindows.*.resetsAt` at utilization 1 or more), or to an empty string. `invoke_executor_command()` passes the code through.

**Placement.** The run exits `0` and starts no other session.

| Session | Story afterwards | Written |
|---|---|---|
| New `/implement` (Step E1) | `Ready for implementation`; the next start continues on the existing branch and PR | the pause record |
| Fix round, no new head | stays `In implementation`; the same round starts again on a later poll | the pause record, whose `Next:` line names the board status read after the session; no fix-round attempt marker, no merge-owner marker |
| Fix round that pushed a new head (issues #446, #447) | `In review`, the Codex review requested for the new head. This holds whether the session was still `In implementation` (the hand-off writes `In review`) or had already written `In review` before the stop (the hand-off leaves it there; the bookkeeping job posts the missing review request) | in the same run: the merge-owner marker for the new head, the usual hand-off, then the pause record saying the head was handed off and naming the story's board status; no fix-round attempt marker, nothing read back later; the dispatcher posts no review request itself |
| Fix round that pushed a new head while the PR is still a draft (issue #446) | keeps its status (`In implementation`) | the pause record says the head is not ready for review; no merge-owner marker, no hand-off, no blocker (a draft PR is outside the missing-merge-owner rule) |
| `/review` of a Codex-implemented PR | stays `In review` | the pause record; no per-head review attempt marker |
| Conflict round | stays where it is (`In implementation`, or `In review` when the session set it); the next run decides from the PR ([conflict round](#conflict-round)) | the pause record with step `conflict round`, for the PO only; no marker |

No `## Blocked Declaration` is posted, no Slack message is sent and no actionable event is registered.

**The record.** One comment headed `## Paused at a provider usage limit` names the step and target, the branch, the PR number and head SHA (or `no PR yet`), the reset time in UTC (or `unknown — the dispatcher checks the allowance on every poll`), the commits the runner had not pushed and whether it had uncommitted changes, what the dispatcher does next, and "No PO action is needed." It is not posted again while it is the story's newest comment for the same step and PR head. The run log has one `[SESSION RATE LIMIT]` line.

**Restart.** There is no timer. The usage limit check and the allowance check with its hold refuse every start until the window has reset, and a restart is counted by the usage limit like any session, which bounds a loop of limit stops. `/implement` continues on an existing branch for the story, with or without a PR (`.claude/commands/implement.md` steps 9 and 10).

**What is lost.** Only commits the session pushed survive: the next runner is fresh. The dispatcher does not commit or push leftover work, so half-done changes never reach the PR; the record counts what was not pushed.

## The dispatcher script is read completely before any session starts

bash reads a script from the file in pieces, so a session that rewrites `scripts/dispatcher-invoke.sh` while the run waits for it (any story that changes the dispatcher does) could make the run resume in the middle of a line (issue #345). The whole body of the script is therefore one brace group closed by `}; exit $?` on a single line: bash parses it completely before the first session starts, and nothing is read from the file afterwards. Keep it that way: no code after the group.

The `exec bash "$0"` tail calls start a new process on purpose, so the next story runs the code that was just merged. The other files are safe: `scripts/lib/claude-session.sh`, `scripts/lib/decision-blocker.sh` and `scripts/lib/evidence-pending.sh` are sourced once at start, and `dispatcher-usage-limit.sh`, `dispatcher-allowance.sh`, `slack-notify.sh`, `dispatcher-change-check.sh`, `session-summary.sh` and `hpw dispatcher write` (issue #432) run as separate processes that read the file at call time. Any new script the dispatcher sources after a session, or runs for the length of a session, needs the same treatment; check `dispatcher-merge.sh` and `dispatcher-cleanup.sh` (#264) when they land.
