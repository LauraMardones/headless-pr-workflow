# ADR-012: How Model Sessions Are Started Without the PO

**Status:** Proposed (draft: provider-terms check done, trial not started, no decision yet)
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

**Not yet made.** This draft records only the provider-terms check, which had to come first.

Terms check result (2026-10-08): **neither candidate is excluded.** Anthropic's own documentation describes both as supported uses of a Pro or Max subscription. The reading, the quotes it rests on and the remaining risk are in [Provider terms check](#provider-terms-check). The PO reads the quotes before any trial session starts.

The decision is written after the trial. If no script can read the remaining five-hour and weekly allowance, no mechanism is chosen and the question goes to the PO as a decision blocker (#289 acceptance criteria).

## Consequences

To be written with the decision. Already known from the documentation, before any trial:

- Candidate A cannot run `scripts/merge-gate-summary`, `scripts/ac-summary.sh` or board writes as they are. The cloud GitHub proxy rejects GraphQL even when the session supplies its own token, so installing an authenticated `gh` in a setup script does not help. Choosing A means those scripts must read GitHub through REST.
- A routine's schedule cannot run more often than once an hour. Keeping ADR-002's five-minute poll with candidate A means the dispatcher fires the routine through its API trigger, which adds one routine-scoped token as a repository secret. That token starts runs; it is not an API key and carries no billing.
- Routines are a research preview, so candidate A's behavior and limits may change.

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

The probe output is not in the repository. It contains nothing secret, but the acceptance criteria ask for linked evidence, and a local run has no link; the candidate B run on Actions is to supply one.

### Sessions

None started on either candidate yet.
