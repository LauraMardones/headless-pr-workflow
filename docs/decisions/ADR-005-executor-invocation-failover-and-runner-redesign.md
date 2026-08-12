# ADR-005: Executor Invocation, Failover, and Runner Redesign

**Status:** Proposed
**Date:** 2026-08-12
**Supersedes:** ADR-003 (interim embedded API loop)
**Related:** ADR-001, ADR-002
**Amends scope of:** #259
**Epic:** #160

---

## Context

ADR-002's "invokes executors (Claude Code, Codex) via API calls" conflated two separable things: *transport* (a network call) and *billing model* (metered per-token). ADR-003 read it as the latter and accepted, as an interim measure, a hand-built bounded tool-use loop (`run_anthropic_agent()`/`run_openai_agent()`) calling the raw Anthropic/OpenAI APIs directly through a single crude `bash` tool. Lacking real exploratory tools, that loop must manually stuff full context — related issues, ADRs, whole files — into every turn (ADR-003 Correction 3: live run `31426452350` burned 14 turns before writing any code). This compounds two costs: metered billing entirely separate from the PO's existing Claude Pro/Max and ChatGPT Plus/Pro subscriptions, and architectural waste independent of billing, since a real `claude`/`codex` CLI session explores incrementally and only pulls in what's relevant.

Research on 2026-08-12 established:
- **Claude:** OAuth/subscription auth for scripted, autonomous use is officially supported (`claude setup-token`, `claude-code-action`).
- **Codex:** OpenAI's official `openai/codex-action` requires an API key, not ChatGPT-plan login (openai/codex issue #2543, open) — but `codex exec` run directly via CLI, logged in with `codex login`, works fine outside that specific GitHub Action.
- Neither CLI exposes a clean, scriptable "remaining window" query in headless mode (`/usage`, `/status` are interactive-only); both surface a parseable rate-limit-exceeded error, with reset time, when the cap is actually hit.

Across the conversation that produced this ADR, the PO further established:
- The dispatcher's existing implement/review/merge/cleanup routing (whichever executor is assigned to a story does `/implement` + `/merge` + `/cleanup`; only `/review` flips to the other provider, per issue #263) is correct as designed and stays untouched by this ADR.
- Automatic failover between Claude and Codex as either's 5-hour subscription window nears exhaustion is wanted, so the dispatcher keeps moving instead of stalling.
- A mid-task interruption (the window closes *during* `/implement`) must not silently restart from scratch under the other model — this repo already has a first-class mechanism for exactly this class of problem: `docs/TAKEOVER-RULES.md` / `hpw pr-takeover`, built on the principle that GitHub state, not session memory, is authoritative. `docs/WORKTREE-MODEL.md` already states "unpushed local commits are not durable workflow state," which becomes load-bearing here, not just informative.
- `size:small`/`size:medium`/`size:large` labels and token-estimate constants already exist (`dispatcher-budget.sh`, used today for UTC-daily budget gating) — reusable for window-aware preemptive routing at near-zero marginal cost, not a new system.
- `docs/PROJECT-STATUS.md` already documents that Refined → Ready for implementation should happen "only when every hard dependency listed in its `## Dependencies` section is closed or confirmed resolved... by the PO or **a dependency-tracking process**" — but no such process exists; `dispatcher-poll.sh` only reads Ready-for-implementation and Ready-for-refinement, never Refined.
- The 5-minute GitHub Actions cron poll is a real, already-documented cost: issue #259 itself states a 5-minute-forever schedule "likely already exceeds a private repo's free-tier minutes from the heartbeat alone," given GitHub Actions' 30–90s cold start (ADR-002's own cited con). Adding dependency-monitoring to every cycle only grows this. ADR-001's original **Option B: External Runner** was rejected in 2026-06 solely because no persistent hosting existed for this project; #259 removes that blocker — for the dispatcher as a whole, not only for Codex's invocation step.

---

## Decision

Five parts, adopted together:

### 1. Native CLI invocation, not a hand-rolled API loop

Replace ADR-003's embedded loop with genuine headless invocation — `claude -p ... --output-format json`, `codex exec ...` — driven by each tool's own native agentic loop and tool set. The task prompt (`.claude/commands/<slash_command>.md`) is unchanged; only the execution mechanism changes.

### 2. Subscription authentication, on one shared host

Both `claude` and `codex` run as subscription-authenticated CLI sessions (`claude setup-token`; `codex login`) on the same persistent, PO-owned host — #259's RPi4 — rather than split between GitHub Actions and the Pi. The asymmetry a prior draft proposed (Claude on Actions via `claude-code-action`, Codex on the Pi) existed only because of a GitHub-Actions-specific constraint; it becomes moot once dispatch no longer runs in GitHub Actions at all (Part 3).

### 3. The whole dispatcher moves off GitHub Actions cron, onto the persistent host

Poll, dependency-check (Part 5), routing, and invocation for both providers become a persistent or tightly-looped process on the Pi — a full return to ADR-001's original Option B, now that its blocking condition no longer holds. GitHub Actions is retained only for CI-type tasks (ADR-001's own carve-out), not dispatch. **Interim:** until the Pi is provisioned, today's GitHub Actions cron dispatcher remains the accepted interim state — the same pattern ADR-003 used.

### 4. Rolling-window-aware routing and failover — three checkpoints, one shared state

State: a rolling 5-hour usage window per executor, replacing `dispatcher-budget.sh`'s current UTC-daily counters.

- **Checkpoint A — preemptive, before a story starts:** use the story's existing size label and existing token-estimate constants to check whether the assigned executor's remaining window headroom plausibly covers the story; if not, route directly to the other provider instead of starting.
- **Checkpoint B — proactive threshold, for ongoing routing:** when a window's estimated usage crosses a configurable threshold (e.g. 80–90%), route new stories to the other provider until the window resets.
- **Checkpoint C — reactive safety net, mid-task:** if a headless invocation still returns a rate-limit-exceeded error, treat it as a new **capacity blocker** category — distinct from `decision_blocker` (no PO judgment needed) and from silent staleness (no 2h wait to notice). Post a status comment (work done, current head SHA, reset time) and hand off via the existing takeover protocol (`hpw pr-takeover`). The takeover session reads GitHub state, not the interrupted session's memory, and **continues rather than restarts from scratch**; the existing cross-provider review step is the consistency safety net for whatever a handoff produces.
- Requirement this implies: the CLI adapter must commit and push work durably and incrementally during implementation, not leave it as an uncommitted worktree diff — applying `docs/WORKTREE-MODEL.md`'s existing durability principle to a new trigger, not inventing a new rule.
- A and B reduce how often C fires; C stays necessary because size-based estimates are approximate (ADR-003 Correction 3 is a real incident of an estimate being wrong).

### 5. Dependency-based auto-promotion out of Refined

Each dispatcher cycle, scan items in Refined status, evaluate their `## Dependencies` section against actual GitHub issue state, and promote an item to Ready for implementation (applying the `executor:` label per existing policy) once every listed hard dependency is closed or confirmed resolved. This implements policy `docs/PROJECT-STATUS.md` already states but that has no automation today. Deterministic, non-LLM check — cheap at whatever cadence Part 3's migrated loop runs.

---

## Options Considered

- **Option A (chosen):** all five parts together.
- **Option B — keep the Actions/Pi split from the earlier draft:** rejected; reintroduces an asymmetry that Part 3 makes unnecessary.
- **Option C — keep GitHub Actions cron permanently, add failover and dependency-automation on top without moving the runner:** rejected; does not address the heartbeat cost that motivated reopening this decision, and duplicates ADR-001's already-settled reasoning for why polling belongs on persistent infrastructure once available.

---

## Consequences

- ADR-003's loop and its turn/wallclock bounds are retired, replaced by native CLI controls.
- `scripts/dispatcher-invoke.sh` needs a CLI-invocation adapter, capacity-blocker handling, and takeover-based resume logic.
- `scripts/dispatcher-poll.sh` needs Refined-dependency scanning and auto-promotion logic, and (once Part 3 lands) needs to run as a persistent/tight-loop process rather than a cron-triggered script.
- `scripts/dispatcher-budget.sh` needs rolling-window counters, the threshold-routing override, and the size-based preemptive check.
- `docs/DISPATCHER-CONFIG.md`'s "Executor Secrets" section is rewritten: `ANTHROPIC_API_KEY`/`OPENAI_API_KEY_CODEX` → `CLAUDE_CODE_OAUTH_TOKEN` + a `codex login` session, both on the Pi.
- `docs/PROJECT-STATUS.md` needs no policy change — Part 5 implements what it already states.
- `.github/workflows/dispatcher.yml` stays as the interim runner until the Pi is provisioned, then is retired from dispatch duty (CI-only use, if any, is unaffected).
- #259's scope is finalized: CLI + subscription login for both providers, plus the full poll/dispatch loop, not just Codex invocation.
- Substantial new engineering — should be broken into its own Feature/stories before implementation, not built directly off this ADR text.

---

## Conditions for Revisiting

1. If OpenAI's official `codex-action` adds ChatGPT-plan support, re-evaluate whether GitHub Actions becomes viable again for dispatch — likely still inferior to Part 3's near-continuous polling, but worth a check.
2. If real-world failover data shows the window threshold or size estimates are mistuned, adjust them — routine tuning, not an ADR-level change.
3. If dependency auto-promotion produces incorrect promotions (a hard dependency treated as resolved when it isn't), the detection logic needs revisiting before it's trusted further.
4. If #259's Pi provisioning stalls indefinitely, Parts 2 and 3 fall back to the interim state (GitHub Actions cron, split or metered as necessary) indefinitely rather than as a bounded interim.

---

## References

ADR-001 (Option B reinstated), ADR-002, ADR-003 (superseded) · #259, #160, #270, #263 · `docs/PROJECT-STATUS.md`, `docs/TAKEOVER-RULES.md`, `docs/WORKTREE-MODEL.md`, `docs/DISPATCHER-CONFIG.md` · `anthropics/claude-code-action` · `openai/codex-action`, openai/codex issue #2543
