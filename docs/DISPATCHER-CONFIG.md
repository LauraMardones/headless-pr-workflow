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

## Dispatcher Toggle

| Variable | Values | Default |
|---|---|---|
| `DISPATCHER_ENABLED` | `true` / `false` | `true` |

Set this repository variable to `false` to pause all autonomous dispatcher runs without disabling the workflow file. The dispatcher checks this value at startup and exits cleanly when it is `false`.

To update this variable:

`https://github.com/<owner>/<repo>/settings/variables/actions`

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
