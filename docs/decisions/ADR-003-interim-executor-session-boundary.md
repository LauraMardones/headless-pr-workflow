# ADR-003: Interim Executor Session Boundary — Embedded Loop, Pending Runner Migration

**Status:** Accepted (interim — see Conditions for Revisiting)
**Date:** 2026-08-10
**Issue:** #254 — Bug: dispatcher invokes executors via a locally-installed CLI that doesn't exist on the GitHub Actions runner
**PR:** #255 — fix: invoke executors via direct Anthropic/OpenAI API calls, not a CLI
**Feature:** #162 — Executor invocation and WIP enforcement
**Epic:** #160 — Autonomous story-level execution dispatcher

---

## Context

ADR-002 chose GitHub Actions as the dispatcher's runner and stated, as part of that decision, that "the dispatcher invokes executors via API calls, not embedded sessions" and that "executor sessions run outside GitHub Actions." That language describes a specific division of labor: the dispatcher's job is to trigger and check on executor work; the executor's actual work — reading a story, editing files, running tests, committing, opening a pull request — happens somewhere not bound by an Actions job's lifetime or billing model.

Issue #254 found that the dispatcher's prior implementation shelled out to a `claude`/`codex` CLI binary that was never installed on the runner, so every dispatcher-driven `/implement` invocation failed outright. PR #255 fixed the immediate defect by replacing the CLI shell-out with direct calls to the Anthropic Messages API and the OpenAI Chat Completions API, via a bounded tool-use loop (`run_anthropic_agent()` / `run_openai_agent()`) that gives the model a single `bash` tool scoped to the repository checkout.

PR #255's review caught that this fix, while satisfying "no CLI binary, no install step, direct API calls," does not satisfy ADR-002's "executor sessions run outside GitHub Actions." The tool-use loop and every command it executes still run inside the "Run dispatcher invoke" Actions job — only the model's inference calls leave the runner. This is an embedded session with a different transport, not an external one. Issue #254's own Decisions section ("the dispatcher calls the Anthropic and OpenAI APIs directly") did not separately address where the resulting loop's process boundary should sit, and the implementation did not catch the gap either.

A genuinely external session — matching ADR-002's line as written — requires either standing up external runner infrastructure (ADR-001's Option B, rejected in ADR-001/Epic #160 specifically because no persistent hosting environment existed for this project) or checkpointing the loop across many short, independent dispatcher runs (real additional engineering, not yet built). Neither is available today. The PO has since identified a concrete path to genuinely external sessions — a dedicated Raspberry Pi 4, tracked in #259 — but provisioning it is a separate, PO-driven effort with no fixed timeline.

PR #255 should not stay blocked for the duration of that effort. This ADR records the interim decision that unblocks it.

---

## Decision

**Accept the embedded tool-use loop built in PR #255 as an explicit, temporary interim state**, bounded by hard caps, until #259 (migration to owned infrastructure) lands.

For this interim period, ADR-002's "executor sessions run outside GitHub Actions" is narrowed as follows: the model's *reasoning* — every inference call — runs outside GitHub Actions, on the Anthropic/OpenAI provider's own infrastructure, exactly as ADR-002 intended. The *orchestration loop and tool execution* that drive that reasoning are accepted as running inside the Actions job, subject to hard bounds that keep any single run short and finite:

| Bound | Env var | Default |
|---|---|---|
| Tool-use round-trips before failing closed | `AGENT_MAX_TURNS` | 60 |
| `max_tokens` requested per API turn | `AGENT_MAX_TOKENS` | 8192 |
| Seconds a single bash-tool command may run | `AGENT_TOOL_TIMEOUT` | 300 |
| Seconds a single API call may take | `AGENT_API_TIMEOUT` | 600 |

GitHub Actions' own 6-hour job ceiling remains a hard backstop beneath these caps.

This is a real, explicit narrowing of ADR-002's original language — not a reinterpretation that was already latent in it. It is adopted only because it is temporary and bounded, with a concrete migration already tracked (#259) to restore the originally-intended architecture.

---

## Options Considered

### Option A: Accept the embedded loop, interim (chosen)

Keep PR #255's implementation as built. Formally narrow ADR-002's session-boundary language for the interim, with hard turn/time caps as the bound.

**Pros:**
- Zero additional engineering — already built and tested (23 + 31 passing regression tests in PR #255).
- Unblocks PR #255 and issue #254 immediately; Epic #160's "at least one full end-to-end cycle runs autonomously" success criterion is no longer indefinitely stalled.
- The caps bound the worst case: no single run can exceed GitHub Actions' own job ceiling regardless of turn count.

**Cons:**
- Does not satisfy ADR-002's "sessions run outside GitHub Actions" as originally written — this ADR is the acknowledgment of that, not a way around it.
- Actions-minutes cost for a real story's full implementation time is billed to the dispatcher's Actions-minutes budget, a cost category ADR-002 deliberately kept separate from executor capacity.
- Carries real risk of hitting the turn/time caps mid-task on a large story, requiring the caller to treat it as a failure and post a blocker — untested against real-world story sizes as of this writing.

### Option B: Build a true external runner now

Redesign so the Actions job only triggers and polls a session hosted elsewhere; the loop and all tool execution run outside GitHub Actions entirely, matching ADR-002 exactly.

**Rejected — for now:** This is exactly ADR-001's Option B, already rejected in ADR-001 and again in Epic #160's 2026-06-02 decision, for the same reason: no persistent hosting environment existed for this project. That constraint has a concrete resolution in progress (#259), but provisioning it is not yet done. Building throwaway infrastructure to bridge the gap, only to replace it once #259 lands, is not worth the effort relative to Option A's bounded interim risk.

### Option C: Checkpointed multi-run execution

Split the loop into short, bounded slices across separate dispatcher poll cycles, persisting conversation state in GitHub (a comment, a branch commit) between runs, so no single Actions job run is ever long-running.

**Rejected — for now:** The most technically faithful reading of ADR-002's actual concern ("long-running agentic sessions cannot run inside an Actions job" is satisfied by making no single run long, not by relocating the run). Real engineering cost (checkpoint/resume logic, mid-edit interruption handling) that duplicates work #259 will make unnecessary once genuinely external sessions exist. Worth reconsidering only if #259 stalls indefinitely.

---

## Rationale

Option A is chosen because it is temporary and because a concrete alternative is already in motion. Its cons are real and are not minimized here — this ADR exists specifically so they are not silently accepted by omission. Options B and C both remain more faithful to ADR-002 as originally written; either is a legitimate action to take instead of this one **if #259 stalls or is abandoned** — see Conditions for Revisiting.

---

## Consequences

- `docs/decisions/ADR-002.md` is **not edited**. It remains the accurate record of the target architecture (genuinely external executor sessions) and the reasoning that produced it, in keeping with this project's existing pattern of superseding decisions via a new ADR rather than rewriting the old one (ADR-001 was treated the same way when ADR-002 superseded it).
- Issue #254's Acceptance Criterion "matches ADR-002's documented architecture ... with no further ADR update needed" is **not met as originally worded** — an ADR update was needed. This ADR is that update; issue #254's Decisions section is amended to reference it.
- PR #255 may proceed to merge on the strength of this ADR, without further architectural rework, once review sign-off is otherwise satisfied.
- `AGENT_MAX_TURNS`, `AGENT_MAX_TOKENS`, `AGENT_TOOL_TIMEOUT`, and `AGENT_API_TIMEOUT` (in `scripts/dispatcher-invoke.sh`) become load-bearing safety bounds under this ADR, not incidental tuning knobs. Changes to their defaults should be treated as changes to this ADR's accepted risk, not routine tuning.
- #259 (migrate to owned infrastructure) is the tracked path back to ADR-002's original architecture. This ADR does not set a deadline for #259; it is PO-paced.

---

## Conditions for Revisiting

This ADR should be superseded when any of the following occur:

1. **#259 lands** — the dispatcher and executor sessions move to persistent, owned infrastructure. At that point this ADR is superseded in full and ADR-002's original language is satisfied without qualification.
2. **A real story's `/implement` run hits `AGENT_MAX_TURNS` or a timeout cap** before #259 lands — this is evidence the interim bound is too tight for real usage, and the caps (or this ADR's acceptance of Option A at all) need re-evaluation before it happens again silently.
3. **#259 stalls indefinitely or is abandoned** — at that point, Option B or Option C should be formally reconsidered rather than allowing this "interim" ADR to become permanent by default.

---

## References

- `docs/decisions/ADR-002.md` — target architecture; unchanged by this ADR.
- `docs/decisions/ADR-001-runner-integration.md` — original external-runner decision; Option B's rejection here restates ADR-001's own reasoning, not a new evaluation.
- #254 — Bug: dispatcher invokes executors via a locally-installed CLI (the defect this ADR's decision unblocks).
- #255 — PR implementing the embedded tool-use loop this ADR accepts as interim.
- #259 — Task: migrate dispatcher and executor sessions to persistent, owned infrastructure (the tracked path to superseding this ADR).
- #256, #257 — bugs found during the same investigation, both symptomatic of ephemeral-runner statelessness; #257 in particular is expected to resolve as a side effect of #259.
