# ADR-004: Executor Invocation Model — Subscription-Authenticated CLI Sessions, Not Metered API Calls

**Status:** Proposed
**Date:** 2026-08-12
**Amends scope of:** #259 — Task: migrate dispatcher and executor sessions to persistent, owned infrastructure (RPi4)
**Related:** ADR-002, ADR-003
**Epic:** #160 — Autonomous story-level execution dispatcher

---

## Context

ADR-002 chose GitHub Actions as the dispatcher's runner and said "the dispatcher workflow invokes executors (Claude Code, Codex) via API calls." That sentence has always carried two separable meanings that were never disambiguated:

1. **Transport** — the executor is reached over a network call, not a locally-installed interactive tool.
2. **Billing model** — the executor's inference is metered and paid for per token, via `ANTHROPIC_API_KEY` / `OPENAI_API_KEY_CODEX` against the Anthropic Messages API and OpenAI Chat Completions API.

Issue #254 and PR #255 resolved a real defect (the dispatcher shelled out to a `claude`/`codex` CLI binary that didn't exist on the Actions runner) by reading ADR-002's sentence under meaning 2: they replaced the missing CLI with a bounded tool-use loop calling the provider APIs directly. ADR-003 accepted that as an interim, bounded state.

That reading was never corrected downstream. #259 — the tracked task to move the dispatcher and executor sessions onto a PO-owned Raspberry Pi 4, explicitly framed as "the tracked path back to ADR-002's original architecture" — still lists its provisioning scope as: *"Securely provision `ANTHROPIC_API_KEY`, `OPENAI_API_KEY_CODEX`, and `GH_TOKEN`/`PROJECT_TOKEN` on the Pi."* Migrating to the Pi under that scope solves ADR-002's session-boundary concern (executor sessions run outside GitHub Actions) but does **not** solve the billing-model concern: it is still metered, per-token API usage, just relocated to different hardware.

The PO's actual intent for this project, stated directly (2026-08-12), is different from both readings above: run the executor's actual coding work through the `claude` and `codex` CLIs authenticated as the PO — i.e. logged in with the Claude Pro/Max and ChatGPT Plus/Pro subscriptions the PO already pays for — so that dispatcher-driven implementation work draws on subscription-included usage instead of incurring separate, metered API cost. This is a genuinely different invocation model from both ADR-003's embedded API loop and #259's current provisioning plan, and no existing ADR records it.

---

## Decision

**Amend #259's scope**: when the dispatcher and executor sessions move to persistent, PO-owned infrastructure, the executor invocation must use the `claude` and `codex` CLIs authenticated via the PO's existing subscription login (`claude login` / `codex login` equivalent, session-token based), not `ANTHROPIC_API_KEY` / `OPENAI_API_KEY_CODEX`. Metered provider API keys are dropped from that provisioning step in favor of a logged-in CLI session maintained on the host machine.

This ADR does not itself change anything on GitHub Actions or in `scripts/dispatcher-invoke.sh` — ADR-003's embedded, metered-API loop remains the accepted interim state until #259 lands, exactly as ADR-003 already says. What this ADR changes is the **target** #259 is building toward, before that migration is built.

This ADR is filed as **Proposed**, not **Accepted**, because three open questions block it from being safely actionable (see Consequences and Conditions for Revisiting). It should not be treated as authorizing implementation on its own.

---

## Options Considered

### Option A: Subscription-authenticated CLI on persistent host (proposed)

The Pi (or whatever persistent host #259 provisions) runs `claude` and `codex` logged in as the PO. The dispatcher invokes them in their non-interactive/headless mode (e.g. `claude -p ...`, `codex exec ...`) instead of calling `run_anthropic_agent()` / `run_openai_agent()` against the raw provider APIs.

**Pros:**
- Executor usage is drawn from subscription-included usage, not billed per token — directly addresses the PO's stated cost concern.
- Closer to ADR-002's original intent ("invokes executors Claude Code, Codex") read literally, as actual CLI tools, not as a description of which HTTP API they happen to call.
- Removes `ANTHROPIC_API_KEY` / `OPENAI_API_KEY_CODEX` from the provisioning surface entirely; a compromised host leaks a session credential scoped to interactive-tool use, not a raw API key with full metered billing exposure.

**Cons:**
- Unattended, headless automation against a subscription-authenticated session may be restricted by Anthropic's and OpenAI's consumer-subscription terms of service in ways a metered API key is not — **unverified as of this writing** (see Conditions for Revisiting #1). This is a hard blocker until checked.
- Subscription plans carry their own usage caps (e.g. weekly/rolling limits on Pro/Max), which are not designed around unattended, potentially high-volume dispatcher-driven automation. Whether real dispatcher volume fits inside those caps is unknown.
- A long-lived, logged-in CLI session sitting on a machine that also executes arbitrary dispatcher-triggered commands is a real credential-security surface: anything that can execute on that host can act as the PO's subscription session, not just as a scoped API key.
- New engineering: the dispatcher needs a CLI-invocation adapter (headless-mode flags, output parsing, exit-code handling) in place of the existing HTTP tool-use loop, and needs its own turn/timeout bounds analogous to ADR-003's `AGENT_MAX_TURNS` / `AGENT_MAX_WALLCLOCK_SECONDS`, reimplemented against CLI session behavior rather than API response fields.

### Option B: Keep metered API keys on the persistent host (current #259 scope, unchanged)

Migrate to the Pi as #259 currently describes, keeping `ANTHROPIC_API_KEY` / `OPENAI_API_KEY_CODEX` as the invocation credential, just relocated off GitHub Actions.

**Rejected as the sole path** — this satisfies ADR-002's session-boundary language but not the PO's cost intent, which is the specific problem this ADR exists to record. Kept here only as the fallback if Option A turns out to be blocked by Condition for Revisiting #1.

### Option C: Hybrid — subscription CLI as primary, metered API as overflow

Use Option A for routine dispatcher-driven work; fall back to metered API keys only when subscription usage caps are hit for the day/week, so the dispatcher never fully blocks on subscription exhaustion.

**Not chosen yet, kept open** — this is the likely landing point if Condition for Revisiting #2 (subscription caps prove too tight for real volume) materializes, but it adds real complexity (dual invocation paths, cap detection, routing logic) that isn't justified before Option A is even confirmed viable. Worth revisiting once the Pi exists and real usage data is available.

---

## Rationale

Option A is proposed, not adopted outright, because it directly answers the problem this ADR was written to capture — the PO does not want dispatcher-driven implementation work billed as metered API usage when subscription-included usage is available and already paid for — while Option B, the current default trajectory of #259, silently fails to solve that even after the infrastructure migration lands. Recording this now, before #259 is built, avoids repeating ADR-003's pattern: a scope decided under an ambiguous reading of "via API calls" that had to be corrected after the fact.

Option A is not marked **Accepted** because its two hard unknowns (ToS permissibility of unattended subscription automation; real-world subscription usage-cap headroom) are facts to be verified, not judgment calls this ADR can resolve by reasoning alone.

---

## Consequences

- `#259`'s scope description should be updated to read "provision `claude`/`codex` CLI login (subscription-authenticated), not `ANTHROPIC_API_KEY`/`OPENAI_API_KEY_CODEX`" pending this ADR's acceptance — tracked as a follow-up edit to that issue, not made by this ADR directly.
- `docs/decisions/ADR-003-interim-executor-session-boundary.md` is **not edited**. Its embedded metered-API loop remains the accepted interim state until #259 (under whichever provisioning model is ultimately accepted) lands.
- No code changes yet. `scripts/dispatcher-invoke.sh`'s `run_anthropic_agent()` / `run_openai_agent()` are unaffected by this ADR alone.
- Before this ADR can move to **Accepted**, the PO (or a delegated research task) must confirm Anthropic's and OpenAI's terms of service permit unattended, dispatcher-driven automation against a subscription-authenticated CLI session. This is a policy read, not an engineering task, and should be done deliberately rather than inferred.
- If accepted, #259's engineering scope grows to include a CLI-invocation adapter and CLI-appropriate bounds, in place of the HTTP tool-use loop ADR-003 built.

---

## Conditions for Revisiting

This ADR should be superseded or amended when any of the following occur:

1. **ToS check comes back negative** — unattended automation against a subscription-authenticated `claude`/`codex` session turns out to violate Anthropic's or OpenAI's terms for that plan tier. At that point this ADR is rejected in favor of Option B (or Option C, if a hybrid is still desired for cost reasons within ToS-compliant bounds).
2. **Subscription usage caps prove insufficient** once the Pi is provisioned and real dispatcher volume is observed — Option C (hybrid) should be reconsidered as the accepted model instead of pure Option A.
3. **#259 is provisioned** and this ADR is still Proposed with open question 1 resolved favorably — this ADR should move to Accepted, and its Option A scope becomes #259's actual implementation, not just its stated intent.
4. **#259 stalls or is abandoned** — this ADR's premise (a persistent, PO-owned host to run a logged-in CLI session on) no longer applies, and it should be marked superseded rather than left open indefinitely.

---

## References

- `docs/decisions/ADR-002.md` — original runner decision; source of the ambiguous "invokes executors ... via API calls" language this ADR disambiguates.
- `docs/decisions/ADR-003-interim-executor-session-boundary.md` — accepted the metered-API embedded loop as an interim state; unaffected by this ADR.
- #259 — Task: migrate dispatcher and executor sessions to persistent, owned infrastructure (RPi4); scope this ADR proposes amending.
- #160 — Epic: Autonomous story-level execution dispatcher.
- #270 — Feature: Dispatcher infrastructure migration & notification reliability (parent of #259).
