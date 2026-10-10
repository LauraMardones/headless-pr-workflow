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

## Executor Secrets — `ANTHROPIC_API_KEY` and `OPENAI_API_KEY_CODEX`

The dispatcher needs credentials to invoke `/implement` (and the rest of the story cycle) via direct calls to the Anthropic and OpenAI APIs — no CLI binary is installed on the runner, and none is required (issue #254). There are exactly **two** required secrets, not one per `executor:` tier:

| Secret | Type | Covers |
|---|---|---|
| `ANTHROPIC_API_KEY` | Repository secret | All three Claude tiers: `executor:claude-code-haiku`, `executor:claude-code-sonnet`, `executor:claude-code-opus` |
| `OPENAI_API_KEY_CODEX` | Repository secret | `executor:codex` |

A single Anthropic API key authenticates calls to any Claude model — Haiku, Sonnet, and Opus are model selections made per request, not separate credential scopes. There is no functional reason to provision three separate Anthropic secrets, and doing so previously blocked every dispatcher-driven invocation regardless of tier (issue #252). Per-tier **budget** enforcement (`BUDGET_DAILY_HAIKU`/`_SONNET`/`_OPUS`/`_CODEX`, see above) is a separate, already-correct mechanism — it stays per-tier and is unaffected by this consolidation.

Provision both secrets at:

`https://github.com/<owner>/<repo>/settings/secrets/actions`

Both secrets must also be explicitly wired into `.github/workflows/dispatcher.yml`'s "Run dispatcher invoke" step `env:` block as `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}` and `OPENAI_API_KEY_CODEX: ${{ secrets.OPENAI_API_KEY_CODEX }}`. GitHub Actions never auto-injects a secret into a step's environment — it only becomes visible if the workflow file explicitly references it via `${{ secrets.NAME }}`. This wiring was missing entirely prior to issue #252, so no executor secret of any name ever reached the dispatcher regardless of what was configured in Settings.

**If a secret is absent**, `invoke_executor_command()` in `scripts/dispatcher-invoke.sh` fails closed with `Error: Secret '<name>' is not set in the environment.` before making any API call, and the dispatcher posts a mid-cycle blocker comment on the story issue.

### Executor invocation mechanism

`invoke_executor_command()` calls the Anthropic Messages API (Claude tiers) or the OpenAI Chat Completions API (Codex tier) directly, per ADR-002's documented architecture ("invokes executors ... via API calls"). No `claude` or `codex` CLI binary is installed on the `ubuntu-latest` runner, and `.github/workflows/dispatcher.yml` has no step that installs one — issue #254 replaced the prior CLI shell-out (which failed on every run with `env: 'claude': No such file or directory`, since neither binary was ever installed) with this direct-API path.

Each call drives a bounded tool-use loop against the resolved provider (`run_anthropic_agent()` / `run_openai_agent()`) that gives the model a single `bash` tool scoped to the repository checkout. The task prompt is the corresponding `.claude/commands/<slash_command>.md` file with `$ARGUMENTS` substituted — the same instructions a manually-run command follows. The loop ends when the model responds without requesting a further tool call, or fails closed (non-zero return, no board mutation) on an HTTP error, a malformed API response, exceeding the per-invocation turn cap, or exceeding the total wall-clock budget. The model, per `executor:` tier, is declared in the `EXECUTOR_MODEL` table in `scripts/dispatcher-invoke.sh`'s header comment (data-driven, same pattern as `EXECUTOR_ROUTING`).

See [ADR-003](decisions/ADR-003-interim-executor-session-boundary.md) for why this loop runs inside the GitHub Actions job at all (an explicit, temporary exception to ADR-002, pending migration to owned infrastructure — #259) and why `AGENT_MAX_WALLCLOCK_SECONDS` specifically — not the per-turn caps alone — is what keeps a run from ever approaching GitHub Actions' 6-hour job ceiling.

### Review-time provider resolution (issue #263)

`invoke_executor_command()` normally resolves the provider/model/secret for a `/implement`, `/merge`, or `/cleanup` call from the story's own `executor:` label (`$EXECUTOR_LABEL`). Its Step E2 `/review` call is the one exception: the dispatcher passes a third argument that overrides which executor label is resolved, so `/review` runs under a different provider than `/implement` — enforcing the cross-provider adversarial-review split documented in [ADAPTERS.md](ADAPTERS.md#cross-provider-review-pairing).

The review executor is looked up in the `REVIEW_EXECUTOR_ROUTING` table (`scripts/dispatcher-invoke.sh`, alongside `EXECUTOR_ROUTING`):

| `/implement` executor label | `/review` executor label |
|---|---|
| `claude-code-haiku` | `codex` |
| `claude-code-sonnet` | `codex` |
| `claude-code-opus` | `codex` |
| `codex` | `claude-code-opus` |

This is computed fresh at review time from the story's existing `executor:` label — it is never written back to the issue as a label or field. The story's `executor:` label continues to mean "the `/implement` executor" only. `/review`'s token cost is attributed to the *reviewing* executor's budget counter (see Budget Check in the Invoke Loop, below), which may differ from the story's own `EXECUTOR_BUDGET_TYPE`.

This resolution is specific to the dispatcher's own invocation path. The manually-invoked `/review` command (`.claude/commands/review.md`, run by a human or an interactive session) is unaffected — it does not read `REVIEW_EXECUTOR_ROUTING` and its reviewer choice is unchanged.

Loop tuning is overridable via environment variables (defaults shown), primarily for tests:

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_MAX_TURNS` | `60` | Tool-use round-trips before failing closed |
| `AGENT_MAX_TOKENS` | `16384` | `max_tokens` requested per API turn |
| `AGENT_TOOL_TIMEOUT` | `300` | Seconds a single bash-tool command may run |
| `AGENT_API_TIMEOUT` | `600` | Seconds a single API call may take |
| `AGENT_MAX_WALLCLOCK_SECONDS` | `10800` (3h) | Total elapsed budget for the whole Actions job (shared across `/implement`/`/review`/`/merge`/`/cleanup` and any chained stories via `DISPATCH_JOB_START_TS`, not reset per call) — the enforced bound against GitHub Actions' 6h job ceiling (see ADR-003) |

---

## GitHub Token — `PROJECT_TOKEN`

The dispatcher workflow requires a **repository secret named `PROJECT_TOKEN`** — not the built-in `GITHUB_TOKEN`. The built-in `GITHUB_TOKEN` does not receive the `project` permission for user-owned GitHub Projects v2 boards, which the dispatcher needs to both read board state and write story status (via `updateProjectV2ItemFieldValue`).

Provision a personal access token (PAT) with the following scopes and store it as a repository secret named `PROJECT_TOKEN`:

`https://github.com/<owner>/<repo>/settings/secrets/actions`

| Secret | Type | Required scopes |
|---|---|---|
| `PROJECT_TOKEN` | Classic PAT | `repo` (full), `project` (full — required for write access to update board status) |

The workflow maps this secret to the `GH_TOKEN` environment variable, which `scripts/dispatcher-invoke.sh` and `scripts/dispatcher-poll.sh` read via `GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"`. No other changes to the scripts are needed.

**If the secret is absent**, the `gh` CLI will fail to authenticate and the workflow will exit with an error on the first API call.

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

---

## GitHub Authentication Token

The dispatcher workflow uses `GH_TOKEN` to authenticate all GitHub API and GraphQL calls, including the Projects v2 board query in `scripts/dispatcher-poll.sh`.

`GITHUB_TOKEN` — the default token available in GitHub Actions — does not carry the `read:project` scope for user-owned GitHub Projects v2 boards. Without this scope, the `repository.projectsV2` GraphQL query returns an empty list and `dispatcher-poll.sh` exits with `Error: No GitHub Projects (v2) board linked to <repo>`, making the entire dispatcher loop non-functional.

To fix this, `GH_TOKEN` must be set to a personal access token (PAT) stored as the repository secret `PROJECT_TOKEN`.

### Required PAT scopes

| Scope | Reason |
|---|---|
| `repo` | Read and write access to repository contents, issues, and pull requests |
| `read:project` | Read access to user-owned GitHub Projects v2 boards |
| `workflow` | Permission to trigger and manage GitHub Actions workflow runs |

### Adding the secret

Add `PROJECT_TOKEN` as a **repository secret** (not a variable) at:

`https://github.com/LauraMardones/headless-pr-workflow/settings/secrets/actions`

The dispatcher workflow reads it automatically via `GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}` in the `dispatch` job `env` block — no other changes are needed.

**If the secret is absent**, the GraphQL call will fail with an authentication error and no stories will be dispatched.

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

After each successful `/review` invocation, a second, independent increment attributes cost to the *reviewing* executor (issue #263) — which, per the cross-provider pairing above, is typically a different `<executor_type>` than the `/implement` increment logged just above it:

```
[BUDGET] Increment: <executor_type> +<tokens> after #<N> (review)
```

There is no pre-`/review` budget *check* (no `[BUDGET SKIP]` path for review) — only `/implement` gates a story from starting on daily cap. Once a story has begun, its `/review` step attributes cost but does not itself skip.

Under `--dry-run`, budget check and increment calls are logged but not executed:

```
[DRY RUN] Would check: dispatcher-budget.sh check <executor_type>
[DRY RUN] Would call: dispatcher-budget.sh increment <executor_type> <tokens>
```

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

- Save the secret with nothing before or after the token. The script removes every space, tab, carriage return and line feed before use, but a token with stray characters inside it is rejected with `401 OAuth access token is invalid`, which does not name the cause.
- The current token was created on 2026-10-08 and lasts one year. Renew it before 2027-10-08 by running `claude setup-token` again and replacing the secret.
- The script and the dispatcher log only the token's presence and its length after whitespace removal, never its value.

### The probe

One Claude Code turn on Haiku, with no tools, a fixed short prompt, and the repository's instructions left out (it runs from an empty temporary directory):

```
claude -p "Reply with the single word OK." --model haiku --tools "" --strict-mcp-config \
  --no-session-persistence --output-format stream-json --verbose
```

- `--bare` is never used: bare mode ignores the OAuth token (ADR-012 → Candidate B).
- `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are removed from the probe's environment. If the init message does not report `apiKeySource: "none"`, the result is an error, so the probe can never bill a metered key.
- The probe times out after 120 seconds (override with `ALLOWANCE_PROBE_TIMEOUT`).
- The script reads `rate_limit_info.unifiedWindows.five_hour` and `.seven_day` (`utilization`, `resetsAt`) and `rate_limit_info.overageStatus` from the `rate_limit_event` line. Anthropic does not document `unifiedWindows`.

### Pinned Claude Code version

`.github/workflows/dispatcher.yml` installs Claude Code in the "Install Claude Code" step, only when the poll found work:

```
npm install -g @anthropic-ai/claude-code@2.1.296
```

The pin keeps the undocumented event shape stable. To change the version, edit that step, then run the dispatcher once with `workflow_dispatch` and check that the `[RESERVE]` line still shows both windows. `tests/test_dispatcher_workflow_allowance.py` checks that the version here matches the workflow.

### Outcomes and log lines

| Exit | Decision | Log line (stderr of the script) | Dispatcher behavior |
|---|---|---|---|
| `0` | `start` | `[RESERVE] five-hour 79% left (reserve 10%), weekly 71% left (reserve 20%) — start` | The session starts. |
| `1` | `refuse` | `[RESERVE SKIP] weekly 15% left, below reserve 20%; resets 2026-10-14T08:00Z` or `[RESERVE HOLD] below reserve until 2026-10-14T08:00Z; not probing` | Before `/implement`: no session, no comment, the story goes back to "Ready for implementation" for a later poll, and the run exits 0. Before `/review`, `/merge` or `/cleanup`: no session, one comment on the story that names the step, the window, the share left, the reserve and the reset time, and the run exits 0. |
| `2` | `error` | `[ALLOWANCE ERROR] <reason> — starting nothing` | No session. The job fails, without a `dispatcher_error` Slack message. After `/implement`, the story gets the same comment with the reason. |

A probe that is itself stopped by a usage limit (result error `rate_limit`) with no readable `rate_limit_event` is a `refuse`, not an `error`. An `overageStatus` other than `"rejected"` is an `error`: usage credits look turned on, so a session could be billed past the limit.

A refusal partway through a cycle leaves the story at its current step. Nothing restarts it automatically until #316; the PO can run the step by hand after the reset time. Under `--dry-run`, the dispatcher logs `[DRY RUN] Would check subscription allowance before /<step>` and does not run the script.

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
