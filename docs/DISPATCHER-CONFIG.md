# Dispatcher Configuration Reference

This document covers all configurable variables for the headless PR workflow dispatcher.

---

## Token Budget Variables

The dispatcher tracks daily token consumption per executor type to prevent the PO's API subscription from being exhausted by autonomous runs. All budget variables are **safety backstops** against multi-window runaway usage within a UTC day. Normal enforcement is by the subscription provider on a per-5-hour-window basis; these caps are a secondary protection layer.

Set these as GitHub Actions repository variables under **Settings → Secrets and variables → Actions → Variables**:

`https://github.com/<owner>/<repo>/settings/variables/actions`

| Variable | Recommended value | Rationale |
|---|---|---|
| `BUDGET_DAILY_HAIKU` | `500000` | ~90–95 % of estimated Claude Pro 5-hour-window capacity for Haiku across a full day |
| `BUDGET_DAILY_SONNET` | `1000000` | ~90–95 % of estimated Claude Pro 5-hour-window capacity for Sonnet across a full day |
| `BUDGET_DAILY_OPUS` | `400000` | Opus is expensive and used sparingly; conservative safety backstop |
| `BUDGET_DAILY_CODEX` | `10000000` | Effectively no dispatcher-level daily cap; 100 % of Codex window available to the dispatcher |

These values were decided on 2026-06-07 as part of Feature #164 (Token budget management and pause/resume).

### How the budget check works

`scripts/dispatcher-budget.sh` reads counter files from `.dispatcher-budget/` (a directory created at runtime, not committed to source control). Each counter file holds a single line in the format `YYYY-MM-DD:<usage>`. When the UTC date changes, the counter automatically resets — no manual intervention is needed.

The calling workflow (`.github/workflows/dispatcher.yml`) is responsible for saving and restoring the `.dispatcher-budget/` directory using `actions/cache` with key `budget-YYYY-MM-DD-{executor_type}` so that counter state persists across workflow runs within the same UTC day.

### Adjusting caps

To raise or lower a cap, update the corresponding repository variable. Changes take effect on the next dispatcher run. To disable the daily cap for a specific executor type, set the variable to a very large number (e.g., `999999999`).

### Interpreting remaining-token output

`bash scripts/dispatcher-budget.sh check <executor_type>` prints the remaining tokens as a bare integer to stdout (e.g., `750000`) and exits:
- `0` — budget available, dispatcher may proceed
- `1` — cap reached, dispatcher should pause

---

## Executor Secrets — `CLAUDE_CODE_OAUTH_TOKEN` and `OPENAI_API_KEY_CODEX`

The dispatcher needs credentials to start the sessions of the story cycle. There are exactly **two** required secrets, not one per `executor:` tier:

| Secret | Type | Covers |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Repository secret | All three Claude tiers (`executor:claude-code-haiku`, `-sonnet`, `-opus`): every Claude session runs Claude Code signed in with the PO's subscription (issue #314, ADR-012). The same secret signs in the allowance probe; see [Subscription token](#subscription-token--claude_code_oauth_token). |
| `OPENAI_API_KEY_CODEX` | Repository secret | `executor:codex`, through the direct OpenAI API loop, until #315 |

One subscription token serves every Claude tier — Haiku, Sonnet and Opus are model aliases chosen per session, not separate credentials (issue #252 removed the per-tier secrets). Per-tier **budget** enforcement (`BUDGET_DAILY_HAIKU`/`_SONNET`/`_OPUS`/`_CODEX`, see above) is a separate mechanism and stays per-tier.

The metered Anthropic API key is no longer used. Since issue #314 it is not read by any script and not wired into the workflow; delete the repository secret and revoke the key at the provider (#308 → PO steps). Every `ANTHROPIC_*` variable is removed from the environment Claude Code runs in, so a metered key can never be used even if one is set again.

Provision both secrets at:

`https://github.com/<owner>/<repo>/settings/secrets/actions`

Both secrets must also be explicitly wired into `.github/workflows/dispatcher.yml`'s "Run dispatcher invoke" step `env:` block as `CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}` and `OPENAI_API_KEY_CODEX: ${{ secrets.OPENAI_API_KEY_CODEX }}`. GitHub Actions never auto-injects a secret into a step's environment — it only becomes visible if the workflow file explicitly references it via `${{ secrets.NAME }}`. This wiring was missing entirely prior to issue #252, so no executor secret of any name ever reached the dispatcher regardless of what was configured in Settings.

**If a secret is absent**, `invoke_executor_command()` in `scripts/dispatcher-invoke.sh` fails closed with `Error: Secret '<name>' is not set in the environment.` before starting the session, and the dispatcher posts a mid-cycle blocker comment on the story issue. A `CLAUDE_CODE_OAUTH_TOKEN` that is empty after whitespace removal counts as absent.

### Executor invocation mechanism

`invoke_executor_command()` resolves the provider and model from the `EXECUTOR_PROVIDER` and `EXECUTOR_MODEL` tables in `scripts/dispatcher-invoke.sh` (data-driven, same pattern as `EXECUTOR_ROUTING`) and loads the task prompt: the `.claude/commands/<slash_command>.md` file with `$ARGUMENTS` substituted — the same instructions a manually-run command follows. Then it starts the session by provider:

| Provider | Executor labels | Session |
|---|---|---|
| `claude-code` | `claude-code-haiku`, `-sonnet`, `-opus` | Claude Code on the runner, on the subscription (ADR-012, issue #314) |
| `openai` | `codex` | Bounded tool-use loop against the OpenAI Chat Completions API with a single `bash` tool (issue #254), until #315 |

This covers every Claude session the dispatcher starts: `/implement` (also the fix-round `/implement` of the resume pass), `/review` of a Codex-implemented story (under `claude-code-opus`), and `/merge` and `/cleanup` of a Claude-tier story (also from the resume pass at `In merge`).

#### Claude Code sessions

The session runs in the repository checkout with `GH_TOKEN` inherited, so `git`, `gh` and the repository scripts work as in a manual session. It uses the Claude Code version pinned in the workflow (see [Pinned Claude Code version](#pinned-claude-code-version)), the same install the allowance probe uses. The exact command line, with the task prompt on stdin:

```
claude -p --output-format stream-json --verbose --permission-mode bypassPermissions \
  --strict-mcp-config --model <alias> --max-turns "$AGENT_MAX_TURNS"
```

- **Model aliases.** `EXECUTOR_MODEL` names `haiku`, `sonnet` and `opus`. The pinned Claude Code version decides which model each alias means, so a model change is a deliberate version bump.
- **Never `--bare`.** Bare mode ignores the OAuth token (ADR-012 → Candidate B).
- **Flags.** Checked on 2026-10-14 against Claude Code 2.1.295 and against the pinned 2.1.296: each flag above is accepted, and an unknown option is rejected. `--max-turns` is no longer listed in `claude --help` but is still accepted. No flag changed from the issue's command line. A live session on the subscription is not part of this check; it is proven by the dispatcher proof run of #314 (AC19).
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
- **Paired session.** `invoke_executor_command()` normally resolves the provider, model and secret from the story's own `executor:` label. For the Step E2 `/review` call the dispatcher passes a third argument that names the reviewing executor instead, so `/review` runs under a different provider than `/implement`. Its token cost is attributed to the *reviewing* executor's budget counter (see Budget Check in the Invoke Loop, below).
- **Neither table.** An executor label with no row in either table gets a mid-cycle blocker after `/implement`.

The path is computed from the story's existing `executor:` label and is never written back to the issue. The label continues to mean "the `/implement` executor" only.

This is specific to the dispatcher's own invocation path. The manually-invoked `/review` command (`.claude/commands/review.md`, run by a human or an interactive session) reads neither table and its reviewer choice is unchanged.

Session tuning is overridable via environment variables (defaults shown), primarily for tests:

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_MAX_TURNS` | `60` | Turns before failing closed: Claude Code's `--max-turns`, and the OpenAI loop's turn cap |
| `AGENT_MAX_TOKENS` | `16384` | `max_tokens` requested per API turn (OpenAI loop only, until #315) |
| `AGENT_TOOL_TIMEOUT` | `300` | Seconds a single bash-tool command may run (OpenAI loop only, until #315) |
| `AGENT_API_TIMEOUT` | `600` | Seconds a single API call may take (OpenAI loop only, until #315) |
| `AGENT_MAX_WALLCLOCK_SECONDS` | `10800` (3h) | Total elapsed budget for the whole Actions job (shared across `/implement`/`/review`/`/merge`/`/cleanup` and any chained stories via `DISPATCH_JOB_START_TS`, not reset per call) — the enforced bound against GitHub Actions' 6h job ceiling (see ADR-003). A Claude Code session runs under `timeout` set to the time left. |
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

**If the secret is absent**, the `gh` CLI fails to authenticate, the workflow exits with an error on the first API call and no story is dispatched.

**If a scope is missing**, GitHub refuses the query that needs it. `hpw codex-review-bookkeeping` then exits non-zero and its error names the failed step, the missing scope and this section (see [Codex review loop](HEADLESS-PR-WORKFLOW.md#codex-review-loop) → Failure).

---

## Slack Notifications

The dispatcher sends Slack notifications for key events (story dispatched, blocked, error, closed) via `scripts/slack-notify.sh`. To enable notifications, add `SLACK_WEBHOOK_URL` as a **repository secret** (not a variable):

`https://github.com/<owner>/<repo>/settings/secrets/actions`

| Secret | Required value |
|---|---|
| `SLACK_WEBHOOK_URL` | Incoming webhook URL from your Slack app (format: `https://hooks.slack.com/services/...`) |

The dispatcher workflow passes this secret to the shell environment automatically — no changes to workflow files are needed beyond what is already wired.

**If the secret is absent or invalid**, `slack-notify.sh` exits with code 1, but `dispatcher-invoke.sh` wraps the call in `|| true` so a missing webhook never aborts the workflow. Notifications silently fail; the dispatch run continues normally.

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

## Budget Cap Notification

When every "Ready for implementation" story in a dispatcher run is skipped due to daily token cap limits, the dispatcher workflow sends a `budget_cap_reached` Slack notification. This notification fires only when `all_budget_blocked=true` is output by the invoke step. It does not fire if the dispatcher exits early for other reasons (e.g., `DISPATCHER_ENABLED=false`, no stories available, or only some stories were budget-blocked).

The Slack message contains:
- A header identifying the pause: `:no_entry: Budget Cap Reached -- Dispatcher Paused`
- Per-executor remaining token counts (Haiku, Sonnet, Opus, Codex)
- A direct link to [GitHub Settings - Variables](https://github.com/LauraMardones/headless-pr-workflow/settings/variables/actions) to adjust caps or toggle `DISPATCHER_ENABLED`

The notification step calls `dispatcher-budget.sh check <executor_type> || true` for each executor type to obtain remaining tokens, then passes the values to `scripts/slack-notify.sh budget_cap_reached`. The `|| true` ensures the step captures the remaining count even when exit code 1 (cap reached) is returned.

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

If no executable item remains, the run exits `0` and invokes no executor. Non-executable skips are tracked separately from budget skips, so they never cause `all_budget_blocked=true`. `--dry-run` makes the same skip decisions without any executor invocation or GitHub mutation.

This skip applies only to the item's type. A Story, Task, or Bug with a missing or unrecognised `executor:` label is still a fatal configuration error (non-zero exit), and the error lists the supported labels:

```
       Add one of: executor:claude-code-haiku, executor:claude-code-opus, executor:claude-code-sonnet, executor:codex
```

`scripts/dispatcher-poll.sh` is unchanged. Its `ready_for_implementation` list and count report every item in that board status, executable or not, and the invoke step applies the type guard.

---

## Budget Check in the Invoke Loop

Budget enforcement is wired into `scripts/dispatcher-invoke.sh` (Story #203). Before each `/implement` invocation, the dispatcher calls `dispatcher-budget.sh check <executor_type>`. If the daily cap is reached, the story is skipped and the loop continues to the next available story:

```
[BUDGET SKIP] #<N>: <story title> — <executor_type> daily cap reached; skipping
```

After each successful `/implement` invocation, the counter is incremented:

```
[BUDGET] Increment: <executor_type> +<tokens> after #<N>
```

If every available "Ready for implementation" story is skipped due to budget, `all_budget_blocked=true` is written to `$GITHUB_OUTPUT` (guarded: only when the env var is set). This output is consumed by the Slack pause notification step (Story C, issue #204).

For a story with a paired `/review` session (`executor:codex`), each successful `/review` invocation adds a second, independent increment that attributes cost to the *reviewing* executor (issue #263) — a different `<executor_type>` than the `/implement` increment logged just above it:

```
[BUDGET] Increment: <executor_type> +<tokens> after #<N> (review)
```

A story that uses the Codex review loop gets no review increment: the dispatcher starts no review session for it (issue #293). A fix round is an `/implement` session and adds the `/implement` increment once more, with no budget check before it.

There is no pre-`/review` budget *check* (no `[BUDGET SKIP]` path for review) — only `/implement` gates a story from starting on daily cap. Once a story has begun, its `/review` step attributes cost but does not itself skip.

Under `--dry-run`, budget check and increment calls are logged but not executed:

```
[DRY RUN] Would check: dispatcher-budget.sh check <executor_type>
[DRY RUN] Would call: dispatcher-budget.sh increment <executor_type> <tokens>
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
- starts no `/review` session and adds no review budget increment;
- logs `[HANDOFF] #<n>: handed to the Codex review loop (Codex Cloud) on PR #<pr>; no /review session started` and goes on to the next ready story.

### Resume at `In merge`

A story in `In merge` is taken up at `/merge`, whatever its executor. The board status is not trusted on its own: the merge gates inside `/merge` decide whether the PR merges.

| Facts | What the dispatcher does |
|---|---|
| A linked open PR that is not a draft | `/merge`, then `Done` and `/cleanup`, then the feature and epic closure check. Log: `[RESUME] #<n>: /merge PR #<pr>` |
| No open PR, and a merged PR that closes the story | `Done` and `/cleanup`, no `/merge`. This finishes a run that stopped between merge and cleanup. |
| Neither (including a PR that is still a draft) | One `Type: external` blocker on the story, posted once. |

**One merge attempt per head.** When `/merge` fails, the mid-cycle blocker records the PR head it was tried on, and the story stays in `In merge`. Later runs start no session for it and log `[RESUME SKIP] #<n>: /merge was already tried on PR #<pr> at head <sha>`. Another attempt needs a new PR head, or a standalone `/unblock` line from the PO posted after the blocker (the resolving-comment rule of issue #262). The same applies when `/merge` fails in the same run as `/review` for an `executor:codex` story.

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

**Maximum fix rounds.** The dispatcher counts the PR heads for which the bookkeeping job recorded findings, one per head. When the count is above `CODEX_FIX_ROUND_MAX`, it starts no session, logs `[RESUME SKIP] #<n>: fix round <k> on PR #<pr> is above the maximum of <max>` and posts one `Type: resource` blocker on the story, once.

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

The workflow uses `--resume` when no story is ready for implementation. A run in which nothing is due starts no session and exits `0`.

The board must have the `In review` and `In merge` Status options. A missing option stops the run with an error that names it, before any session starts. It is not skipped with a warning.

### Exit codes and `--dry-run`

| Exit | Meaning |
|---|---|
| `0` | The story was handed to the review loop, a resume entry completed, a story was skipped by a bound, or there was nothing to do. |
| `1` | A session failed and a blocker was posted. |
| non-zero, no blocker | A missing board option or an invalid `CODEX_FIX_ROUND_MAX`. |

Under `--dry-run` each of the lines above is logged with a `[DRY RUN]` prefix, the hand-off is logged as `[DRY RUN] Would hand #<n> to the Codex review loop …`, and no session starts and nothing on GitHub changes.

### Known limits

- A session that ends without an error counts as a success. After `/merge` the dispatcher writes `Done` without reading the PR's merged state itself, as it did before this change.
- The bookkeeping job resolves Codex's own finding threads from older heads when Codex returns a clean result on the current head, and does not move a story to `In merge` while an unresolved thread it may not resolve remains (a human's thread, a Codex thread on the current head, or one whose review commit cannot be read): it posts a `Type: external` blocker instead. A thread the PO or another reviewer opened is still resolved by a person, and a defect named in an auto-resolved thread that Codex did not re-check can reach `main` (accepted risk, ADR-006 revisit rule). See [HEADLESS-PR-WORKFLOW.md → Codex review loop](HEADLESS-PR-WORKFLOW.md#codex-review-loop).
- Stale recovery does not roll back a story that waits for a fix round. While the allowance is below the reserve, the run ends at the fix round, before the stale check. A story held by one of the two fix-round blockers above is skipped by the stale check: it waits for the PO.
- The merged-PR lookup for a story in `In merge` reads the 100 most recently updated closed PRs.

---

## Token Estimate Constants

Token estimates per invocation are derived from the story's size label via `scripts/dispatcher-budget.sh estimate <size_label>`. Constants are defined in `dispatcher-budget.sh` and must not be redefined elsewhere:

| Story size label | Estimated tokens |
|---|---|
| `size:small` | 25 000 |
| `size:medium` | 75 000 |
| `size:large` | 150 000 |
| unknown / default | 75 000 |

These are conservative estimates (Option A, decided 2026-06-07). Values can be raised once actual usage data is available.

---

## Daily Reset Mechanism

Counter files are date-scoped: each file stores the UTC date alongside the cumulative usage. When a new UTC day begins, the counter file's date no longer matches `TODAY`, so `dispatcher-budget.sh check` treats usage as 0 (full budget available). The workflow's cache key (`budget-YYYY-MM-DD-{executor_type}`) also changes on a new UTC day, ensuring a fresh cache entry is created automatically.

No manual reset, cron job, or scheduled cleanup is required.

---

## Subscription allowance and reserve

Before every model session start (`/implement`, `/review`, `/merge` and `/cleanup`), the dispatcher runs `scripts/dispatcher-allowance.sh` (issue #317, ADR-012). The script reads how much of the PO's five-hour and weekly Claude subscription allowance is left. A session starts only when **both** windows are at or above the reserve the PO has set. When the allowance cannot be read, nothing starts.

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
| `1` | `refuse` | `[RESERVE SKIP] weekly 15% left, below reserve 20%; resets 2026-10-14T08:00Z` or `[RESERVE HOLD] below reserve until 2026-10-14T08:00Z; not probing` | Before `/implement`: no session, no comment, the story goes back to "Ready for implementation" for a later poll, and the run exits 0. Before `/review`, `/merge` or `/cleanup`: no session, one comment on the story that names the step, the window, the share left, the reserve and the reset time, and the run exits 0. The comment is not posted again while it is already the story's newest comment for that step. |
| `2` | `error` | `[ALLOWANCE ERROR] <reason> — starting nothing` | No session. The job fails, without a `dispatcher_error` Slack message. After `/implement`, the story gets the same comment with the reason. |

A probe that is itself stopped by a usage limit (result error `rate_limit`) with no readable `rate_limit_event` is a `refuse`, not an `error`. An `overageStatus` other than `"rejected"` is an `error`: usage credits look turned on, so a session could be billed past the limit.

A refusal partway through a cycle leaves the story at its current step. A story in `In merge` is tried again by the resume entry on a later run (issue #293). A refusal or an error before a fix round changes nothing on the board: the story keeps its place in `In implementation`, no comment is posted, and the fix round is tried again on a later run, before new work. For `/review` and `/cleanup` nothing restarts the step automatically until #316; the PO can run it by hand after the reset time. Under `--dry-run`, the dispatcher logs `[DRY RUN] Would check subscription allowance before /<step>` and does not run the script.

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

The hold lives in the same `actions/cache` entry as the budget counters, whose key changes every UTC day. A hold therefore lasts at most until the next UTC day; after that, one probe re-creates it.

### A repeated `[ALLOWANCE ERROR]`

An `[ALLOWANCE ERROR]` on every poll with work most likely means the undocumented `rate_limit_event` shape has changed, or the token has expired. Each such run fails visibly in Actions (and in GitHub's failed-run email) but sends no Slack message. The remedy is to set `DISPATCHER_ENABLED=false`, read the reason in the log, and either renew the token or revisit ADR-012.
