# ADR-013: An External Scheduler Starts the Dispatcher Every Five Minutes

**Status:** Accepted
**Date:** 2026-10-16
**Related:** #392, #391 (F1.1), #339, #160, #293, #270, ADR-011, ADR-012
**Amends:** ADR-002 (trigger mechanism): the `*/5 * * * *` cron is no longer the only thing that starts `dispatcher.yml`; it stays as a backup
**Effective when:** #339 has set up the scheduler, added the stop check and documented both in `docs/DISPATCHER-CONFIG.md`. Until then the GitHub cron is the only trigger.

## Context

ADR-002 runs the dispatcher on a GitHub Actions cron every five minutes. Its Conditions for Revisiting item 2 is now met. GitHub runs scheduled workflows on a best-effort basis. On 2026-10-12 to 2026-10-14, scheduled runs were 2h44 to 7h27 apart, and on 2026-08-07 to 2026-08-09 the cron did not fire at all (gap table in #339, note at the top of `dispatcher.yml`). A ready story waits for hours, and a story waiting after the Codex review loop answered (#293) is not resumed. Epic #160 still requires a five-minute poll (spec → Decisions → "Dispatch trigger mechanism") and rules out "an owned or persistent runner host" (spec → Out).

`dispatcher.yml` already accepts `workflow_dispatch`, and its `concurrency` group (`cancel-in-progress: false`) runs one dispatcher at a time and keeps at most one pending run. A `workflow_dispatch` run on `main` writes notification state exactly like a scheduled run (`Resolve notification state scope` step).

## Decision

1. **Trigger.** A hosted HTTP cron service calls the REST endpoint [`POST /repos/LauraMardones/headless-pr-workflow/actions/workflows/dispatcher.yml/dispatches`](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) with body `{"ref":"main"}` every five minutes. This confirms the PO's draft preference in #392 → Decisions. #339 picks the service and names it in `docs/DISPATCHER-CONFIG.md`. The service must offer a schedule of five minutes or less, a POST request with an `Authorization` header, a run history, and an e-mail to its account owner when a call fails. A free plan is enough. A serverless cron trigger is not chosen, because it adds code that would have to be deployed and maintained outside the repository.
2. **Credential.** A fine-grained personal access token owned by the PO. Repository access is limited to `LauraMardones/headless-pr-workflow`, with repository permission **Actions: Read and write** and nothing else (GitHub adds read-only Metadata to every fine-grained token). It has an expiry date. It is stored only in the scheduler service's request configuration, under the PO's account. It is never stored in the repository, an issue, a PR, an Actions secret or a log. It is separate from `PROJECT_TOKEN`.
3. **PO setup.** Only the PO can do these steps, and #339 writes them as numbered steps: create the scheduler account, create the token, paste the token into the service, and rotate the token before it expires. The implementer never sees a token value.
4. **Stop check.** The alert is part of #339. Each dispatcher run, whatever started it, reads when the newest `workflow_dispatch` run on `main` was created. If that is more than 15 minutes ago (three missed calls), it sends one Slack alert to the PO for that gap, de-duplicated through the existing notification state. Run creation time is used rather than finish time, so a long executor session does not look like a stopped trigger. The service's failure e-mail is the earlier signal when a call is rejected, for example after the token expired. `gh run list --workflow dispatcher.yml --branch main --event workflow_dispatch --limit 5` is the documented manual check.
5. **Backup.** The GitHub cron stays in `dispatcher.yml`. When the scheduler stops, the cron still runs the dispatcher now and then, and a cron-started run is what sends the stop alert. Runs from both triggers share the `concurrency` group, so they never run in parallel. A newer pending run replaces an older one.

The dispatcher logic, the runner and the executor sessions stay in GitHub Actions. The scheduler only starts the workflow.

## Options Considered

- **A. Hosted external scheduler calling `workflow_dispatch` — chosen.** It keeps the five-minute cadence without the PO's computer and needs no change to the workflow. Its weak points are a third-party dependency with its own outages and terms, and a personal token held outside GitHub. The backup cron and the stop check cover the first. The second is limited by the token's narrow scope and its expiry.
- **B. Self-dispatching chain inside GitHub Actions — rejected.** A `GITHUB_TOKEN` with `actions: write` can start a `workflow_dispatch` run, one of the two events a job token may trigger ([docs](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication#using-the-github_token-in-a-workflow)). But the next run joins the same `concurrency` group and starts as soon as the current run ends. Keeping a five-minute spacing therefore means a job that waits on a runner between calls, or a keep-alive workflow that holds a runner around the clock. That is a persistent runner in all but name (spec #160 → Out). It is also the kind of load out of proportion to its benefit that GitHub's [Actions terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions) forbid. A run that is cancelled, times out or loses its runner before it dispatches the next run breaks the chain, and only the unreliable cron restarts it. The cost of that break is the gap this ADR exists to remove.
- **C. Scheduled task on the PO's computer — rejected.** It runs only while the computer is on, which defeats an autonomous dispatcher (#392 → Decisions). It is also PO-owned infrastructure, which Epic #160 excludes.
- **D. GitHub cron alone (status quo) — rejected.** GitHub runs schedules on a best-effort basis ([docs](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule)). The evidence in #339 shows gaps of hours and a two-day stop, and GitHub also disables a public repository's schedule after 60 days without activity. It stays only as the backup.

## Checks

- **Epic #160 → Out ("An owned or persistent runner host").** Met. The scheduler hosts no runner, runs no dispatcher logic and holds no repository state. It sends one HTTP request every five minutes, and GitHub Actions does the work.
- **ADR-002's OSS separability invariant.** Met. The trigger sits outside the dispatcher logic, which is unchanged. Any scheduler that can send the same request can replace the service.
- **Credential limit (#339 AC1).** Met. The token has at most `actions: write` on this repository only, and it is not stored in the repository.

## Consequences

- #339 sets up the scheduler, writes the PO steps and token rotation in `docs/DISPATCHER-CONFIG.md`, builds the stop alert with a deterministic test, and provides the 24-hour run evidence. `dispatcher.yml` needs no new trigger or `permissions:`.
- Run history holds about 288 dispatched runs a day plus the cron runs. Pending runs replaced in the `concurrency` group show as cancelled. The repository is public, so the minutes are not billed.
- A leaked token can start, cancel or re-run workflows in this repository and delete Actions caches, including `.dispatcher-state/`. Losing that state means a refinement notification may be sent again. The token cannot read secrets or change code. Rotating it ends the exposure.
- If the scheduler and the cron both stop, no alert is sent. The PO then relies on the manual check.
- Revisit if the chosen service changes its free plan or terms, or stops for more than a day; if #339's 24-hour evidence shows a gap over 10 minutes; if GitHub offers a reliable native schedule; or if the project gains persistent hosting (ADR-002 → Conditions for Revisiting item 1).
