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

Loop tuning is overridable via environment variables (defaults shown), primarily for tests:

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_MAX_TURNS` | `60` | Tool-use round-trips before failing closed |
| `AGENT_MAX_TOKENS` | `8192` | `max_tokens` requested per API turn |
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
