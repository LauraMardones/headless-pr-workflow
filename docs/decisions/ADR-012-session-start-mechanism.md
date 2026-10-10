# ADR-012: How Model Sessions Are Started Without the PO

**Status:** Proposed (draft: trial recorded, decision proposed, follow-up stories not yet opened)
**Date:** 2026-10-08
**Related:** #289 (spike), #160 (E5, E6), #306, #308, #304, ADR-006, ADR-009, ADR-010
**Supersedes:** ADR-003 (when a mechanism is chosen; the ADR-003 status line changes in the same PR)
**Amends:** ADR-002's "the dispatcher invokes executors via API calls" (when a mechanism is chosen; the ADR-002 status line changes in the same PR)
**Effective when:** to be written with the decision; it names the follow-up stories under #308 and #306.

## Context

The dispatcher starts implementation sessions through an embedded loop that calls the Anthropic and OpenAI APIs with metered keys (ADR-003). Spec #160 forbids metered API use: all autonomous model work runs on the PO's subscriptions, with a reserve of the allowance kept for the PO (E5, E6). ADR-009 adds refinement sessions, and ADR-006 leaves the review of Codex-implemented stories on the API path until this ADR.

Spike #289 compares two ways to start a session without the PO:

- **A.** A Claude Code cloud session started by a routine (schedule or API trigger).
- **B.** The GitHub Actions dispatcher starting Claude Code on the runner (`claude-code-action` or `claude -p`), signed in with a subscription OAuth token from `claude setup-token`.

The spike checks the provider's terms before any trial session is started, because the check costs no allowance and a trial does.

## Decision

**Proposed, for the PO to accept or change.** The trial is recorded under [Trial record](#trial-record) and [Findings per criterion](#findings-per-criterion).

Model sessions are started by the GitHub Actions dispatcher, which runs Claude Code on the runner signed in with the PO's subscription token (`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`). No API key is used. This is candidate B, and it applies to all three kinds of session:

- **Implementation:** the dispatcher starts `/implement` for the next ready story.
- **Refinement:** the Tech Lead's scheduler (ADR-009) starts refinement sessions the same way.
- **Review of a Codex-implemented story:** the dispatcher starts `/review` the same way, replacing the API call ADR-006 left in place.

Before it starts any session, the dispatcher reads the five-hour and weekly allowance from a one-turn Claude Code run and starts nothing when either is below the PO's reserve.

Candidate A (cloud sessions started by a routine) is not chosen. It worked for implementation, but GitHub GraphQL is blocked there by design, so `/merge`'s merge gate cannot run.

Provider terms (2026-10-08): both candidates are allowed; see [Provider terms check](#provider-terms-check). No pay-per-use billing is involved: the PO confirmed on #289 on 2026-10-08 that usage credits are off and the API console balance is zero with auto-reload off, and every session that reported its allowance showed `overageStatus: "rejected"`.

## Consequences

- `scripts/merge-gate-summary` keeps using `gh` with GraphQL. The Actions runner provides it; no script change is needed for `/merge`.
- The embedded API loop in `scripts/dispatcher-invoke.sh` and the API-key secrets are removed in the follow-up work under #308. ADR-003 is superseded then.
- ADR-002's runner choice and five-minute poll stand. Its "the dispatcher invokes executors via API calls" and "executor sessions run outside GitHub Actions" are replaced: the session runs inside the Actions job, on the subscription.
- A session is bound by the Actions job limit and gets a fresh runner each time. A session stopped by a usage limit leaves its pushed commits on the branch and is started again after the reset. This has not been observed yet and is the first thing the follow-up work must prove.
- The session holds `PROJECT_TOKEN`, which can merge. Until a narrower credential exists, the merge gate rests on the command instructions and branch protection, as it does today.
- The allowance reading depends on a field (`unifiedWindows`) that Anthropic does not document. If it disappears, the dispatcher must stop starting sessions, not start them unchecked.
- "Ordinary, individual usage" is the limit Anthropic sets on subscription use and it has no number. One session at a time keeps to a fair reading of it.
- Revisit if Anthropic changes the terms or the token, if GraphQL becomes available in cloud sessions, or if the allowance can no longer be read.

## Provider terms check

Done 2026-10-08 by a Claude Opus 5.5 session, before any trial session. All sources were retrieved on 2026-10-08. The documentation pages carry no date of their own. This is a reading of published terms by an agent, not legal advice.

### The rule

Consumer Terms of Service, effective October 8, 2025, section 3 (list of things the user may not do) — <https://www.anthropic.com/legal/consumer-terms>:

> Except when you are accessing our Services via an Anthropic API Key or where we otherwise explicitly permit it, to access the Services through automated or non-human means, whether through a bot, script, or otherwise.

Section 2 of the same terms:

> You may not share your Account login information, Anthropic API key, or Account credentials with anyone else or make your Account available to anyone else.

Claude Code, Legal and compliance — <https://code.claude.com/docs/en/legal-and-compliance>:

> Your use of Claude Code is subject to: […] Consumer Terms of Service - for Free, Pro, and Max users

So unattended use of the subscription is allowed only where Anthropic explicitly permits it. The question for each candidate is whether such a permission exists.

### Candidate A: cloud sessions started by a routine

Automate work with routines — <https://code.claude.com/docs/en/routines>:

> Routines are available on Pro, Max, Team, and Enterprise plans.

> Each example pairs a trigger type with the kind of work routines are suited to: unattended, repeatable, and tied to a clear outcome.

> Routines draw down subscription usage the same way interactive sessions do.

> When a routine hits your subscription usage limit, organizations with usage credits turned on can keep running routines on metered overage. Without usage credits, additional runs are rejected until your usage window resets.

> Routines are in research preview. Behavior, limits, and the API surface may change.

**Reading: allowed.** Routines are an Anthropic product for unattended runs on a subscription. This is an explicit permission. With usage credits off (PO confirmation on #289, 2026-10-08) a run at the limit is rejected and not billed.

### Candidate B: GitHub Actions with a subscription OAuth token

Authentication, "Generate a long-lived token" — <https://code.claude.com/docs/en/authentication>:

> For CI pipelines, scripts, or other environments where interactive browser login isn't available, generate a one-year OAuth token with `claude setup-token`

> This token authenticates with your Claude subscription and requires a Pro, Max, Team, or Enterprise plan.

Claude Code GitHub Actions — <https://code.claude.com/docs/en/github-actions>:

> `CLAUDE_CODE_OAUTH_TOKEN`: an OAuth token that authenticates with your Claude subscription, available on Pro, Max, Team, and Enterprise plans. Generate one by running `claude setup-token` locally.

> With a `prompt` input, the Claude Code GitHub Action runs in automation mode on any GitHub event, including a cron schedule.

> If you authenticate with an OAuth token, runs use your Claude subscription instead of API billing.

**Reading: allowed**, for both `claude-code-action` and plain `claude -p`. Anthropic documents the token for "CI pipelines, scripts" and names the repository secret to store it in. Two conditions follow from the documentation:

- `claude -p` must not be run with `--bare`: "Bare mode does not read `CLAUDE_CODE_OAUTH_TOKEN`" (Authentication page). Bare mode needs an API key, which the spike forbids.
- The token is the PO's account credential. It may be used only by the PO's own workflows. The repository is public with the PO as sole collaborator; GitHub does not pass secrets to fork pull requests, and no workflow that uses the token may be triggerable by another person.

### What limits both candidates

Legal and compliance, "Acceptable use" and "Authentication and credential use":

> Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK.

> **OAuth authentication** is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications.

> Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users.

> Anthropic reserves the right to take measures to enforce these restrictions and may do so without prior notice.

> For questions about permitted authentication methods for your use case, please contact sales.

The third-party restriction does not apply: the PO is the purchaser, runs the unmodified Claude Code on the PO's own repository and offers nothing to other users.

**Remaining risk.** "Ordinary, individual usage" is not defined and has no number. One story at a time on one person's repository is a fair reading of it; many parallel sessions around the clock may not be. Nothing in the terms settles where the line is, and Anthropic may enforce without notice. The PO can ask Anthropic through the contact named above if a firmer answer is wanted before the trial.

The Usage Policy (effective September 15, 2025, <https://www.anthropic.com/legal/aup>) adds no restriction on unattended coding sessions; it says agentic use must comply with the policy like any other use.

## Documented behavior to confirm in the trial

Read from the documentation on 2026-10-08. None of it has been observed yet; the trial replaces each line with a finding and linked evidence.

| Criterion | What the documentation says | Source |
|---|---|---|
| 3. Reading the allowance | Claude Code passes `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage` and their `resets_at` times to a status line script. They appear "only for claude.ai Pro and Max subscribers" and "only after the first API response in the session". Whether a script can get them from a non-interactive `claude -p` run or inside a cloud session is not documented. | [statusline](https://code.claude.com/docs/en/statusline) |
| 4. Branch naming (A) | "Claude pushes its work to a branch prefixed with `claude/` unless your prompt directs it to push to another branch." | [routines](https://code.claude.com/docs/en/routines) |
| 5. At a usage limit | "Claude Code blocks further requests until the reset time shown in the message." A routine run at the limit is rejected (quote above). A non-interactive run ends with error category `rate_limit`. | [errors](https://code.claude.com/docs/en/errors), [headless](https://code.claude.com/docs/en/headless) |
| 7. `gh` with GraphQL (A) | "the proxy rejects requests to GitHub's GraphQL endpoint with a 403 […] The restriction applies to every request through the proxy regardless of the credentials you supply, so a `GH_TOKEN` you set gets the same 403." `gh` is pre-installed and REST calls through `gh api` work. | [cloud-environments](https://code.claude.com/docs/en/cloud-environments) |
| 1. Start without the PO (A) | Schedule triggers: "The minimum interval is one hour". API trigger: an HTTP POST with a routine-scoped bearer token starts a session. | [routines](https://code.claude.com/docs/en/routines) |
| 2. Shared allowance (A) | "cloud sessions share rate limits with all other Claude and Claude Code usage within your account". | [claude-code-on-the-web](https://code.claude.com/docs/en/claude-code-on-the-web) |

## Trial record

Time box: five working days from 2026-10-08 16:35 UTC (PO comment on #289), ending 2026-10-15. The PO read the quotes above and released both candidates for trial in the same comment. `CLAUDE_CODE_OAUTH_TOKEN` exists as a repository secret since 2026-10-08 16:32 UTC.

### Reading the allowance (criterion 3, E5): a script can

Observed 2026-10-08, Claude Code 2.1.293, on the PO's machine, signed in with the subscription and with no API key in the environment (`apiKeySource: "none"` in the session's init message).

A non-interactive run, `claude -p "<prompt>" --output-format stream-json --verbose`, emits one `rate_limit_event` line. Its `rate_limit_info` held:

- `unifiedWindows.five_hour.utilization` 0.19 and `unifiedWindows.seven_day.utilization` 0.29, each with a `resetsAt` time in Unix seconds;
- `status: "allowed"`;
- `overageStatus: "rejected"` with `overageDisabledReason: "org_level_disabled"`, which matches the PO's confirmation that usage credits are off.

So the remaining allowance is one minus the utilization, readable with `jq`. Limits of this finding:

- Reading costs one model request. The probe used one Sonnet turn; a cheaper model and a shorter prompt have not been tried.
- The SDK reference documents `rate_limit_event` with `status`, `resetsAt` and `utilization` ([typescript](https://code.claude.com/docs/en/agent-sdk/typescript)). `unifiedWindows`, which carries both windows at once, is not in that reference and may change without notice.
- Not yet observed on a GitHub-hosted runner with the OAuth token (candidate B) or inside a cloud session (candidate A).
- Sending `/usage` as the prompt did not work from Git Bash, which rewrote it to a file path. It has not been tried from another shell.

The local probe output is not in the repository and has no link. The candidate B run below shows the same event on a GitHub-hosted runner.

### Candidate B: minimal session on a GitHub-hosted runner

A throwaway workflow on branch `claude/spike-session-start-trial-b` (not for merge) installs Claude Code with `npm`, runs one `claude -p` turn with `CLAUDE_CODE_OAUTH_TOKEN` and no API key, and calls GitHub GraphQL through `gh`. It is triggered by a push to that branch; a schedule or `workflow_dispatch` trigger needs the file on `main`, so the five-minute poll itself is not exercised here.

| Run | Result |
|---|---|
| [37811036581](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/37811036581) | Session failed: `401 OAuth access token is invalid`. `gh` GraphQL worked. |
| [37811243108](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/37811243108) | Same 401. The stored secret has the expected `sk-ant-oat` prefix but contains one whitespace character (length 109). |
| [37811391159](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/37811391159) | With the whitespace stripped in the job (length 108): session succeeded, `apiKeySource: "none"`, one Haiku turn. `rate_limit_event` reported five-hour utilization 0.21 and weekly 0.29, with `overageStatus: "rejected"`. `gh` 2.102.0 answered a GraphQL query for PR #309's review threads with the job's `GITHUB_TOKEN`. |

Findings so far for candidate B, all from the last run:

- **Criterion 2 (runs on the subscription): pass** for a minimal session. No API key was passed to the job.
- **Criterion 3 (allowance readable): pass.** The same event as on the PO's machine, at the cost of one Haiku turn.
- **Criterion 7 (`gh` with GraphQL): pass** for a repository query with `GITHUB_TOKEN`. Projects v2 writes need `PROJECT_TOKEN`, as the dispatcher already uses; not re-tested.
- **Setup note (criterion 6):** a token pasted with a trailing space or line break is stored as given and fails with a 401 that does not name the cause. The secret should be saved again without the whitespace, or the job must strip it.

Allowance before and after, as the sessions reported it: five-hour 0.19 (local probe, Sonnet) then 0.21 (this run, Haiku); weekly 0.29 both times. The PO's other use in between is not known, so the difference is not the cost of the probes.

### Full sessions on real work (2026-10-10)

Both sessions ran `/implement` on a small task written for the trial, on Sonnet, with a WIP exception from the PO ([#289 comment](https://github.com/LauraMardones/headless-pr-workflow/issues/289#issuecomment-6064792151)). The PO created the routine and committed the workflow file, because the desktop app does not let an agent session create an unattended agent. That is setup; neither session needed a PO action after it.

| | Candidate A: routine, cloud session | Candidate B: Actions runner, OAuth token |
|---|---|---|
| Task and PR | #310, PR #312 | #311, PR #313 |
| Evidence | [session](https://claude.ai/code/session_01AESqLg4wUKr9oreJmRbdY9), [Session Summary](https://github.com/LauraMardones/headless-pr-workflow/pull/312#issuecomment-6096394547) | [run 38043641593](https://github.com/LauraMardones/headless-pr-workflow/actions/runs/38043641593), [Session Summary](https://github.com/LauraMardones/headless-pr-workflow/pull/313#issuecomment-6096398127) |
| Branch | `claude/issue-310-adr-index` | `claude/issue-311-adr-header-test` |
| Result | One file changed as scoped, CI `pytest` green, Session Summary posted, PR ready for review | Same |
| Could not do | `scripts/ac-summary.sh` and board writes (GraphQL 403); `pytest` had to be installed first | Nothing failed. It skipped the board write although `gh` with GraphQL was available |
| Allowance | Not read from inside the session; the PO reads it on the usage page | Read by the job: five-hour 0.05 before, 0.07 after; weekly 0.02 before and after |

Cost. The two sessions ran at the same time, so the 0.02 rise in the five-hour window is their sum plus two Haiku probe turns, not the cost of either one. The PO's usage page before both: session 4%, week 2%, campaign credit US$20 of US$100 left, usage credits 0 EUR. The values after, and so the split between campaign credit and plan allowance for candidate A, are still to be recorded. Session B reported 11 turns in 45 seconds.

## Findings per criterion

| # | Criterion | A: routine, cloud session | B: Actions runner, OAuth token |
|---|---|---|---|
| 1 | Starts without the PO | **Pass.** Started by the routine. A schedule cannot run more often than hourly; the five-minute poll would have to fire the routine's API trigger (not tried). | **Pass** for an Actions event. The trial used a push trigger; the dispatcher's existing cron is the same kind of event (not re-tried with the token). |
| 2 | Runs on the subscription | **Pass**, but cloud sessions draw the campaign credit first until 2026-11-05, so the cost seen now is not the steady-state cost. | **Pass.** `apiKeySource: "none"`; no API key in the job. |
| 3 | Reserve can be enforced | **Partial.** Reading the allowance from inside the session was not tried. A job outside it can read it first, which needs candidate B's token anyway. | **Pass.** The job read both windows and started the session only above the reserve (10% and 20%). |
| 4 | Branch naming | **Pass** when the prompt names the branch. | **Pass.** |
| 5 | Pauses cleanly at a limit | **Not observed.** Documented: further runs are rejected until the window resets. | **Not observed.** Documented: requests are blocked until the reset; a `claude -p` run ends with error `rate_limit`. Work already pushed stays on the branch. |
| 6 | Simple for the PO | One routine per kind of work, set up in a web form; connectors must be removed by hand; research preview. | One secret, one workflow. A token saved with stray whitespace fails with an unexplained 401. The token lasts one year. |
| 7 | `gh` with GraphQL | **Fail.** 403 by design; `scripts/merge-gate-summary` cannot run. | **Pass.** |
| 8 | All three session kinds | Implementation tried. Refinement and review not tried. `/merge` cannot run here (criterion 7). | Implementation tried. Refinement and review not tried; nothing found that would prevent them. |
| 9 | Isolation and safety | Isolated VM; GitHub credentials stay outside the session. | Fresh runner per job, but the session holds `PROJECT_TOKEN`, which can merge. Only the instructions and branch protection stop it. |

Not established within the time box so far: behavior at a real usage limit (criterion 5), a refinement session, and the review of a Codex-implemented story.
