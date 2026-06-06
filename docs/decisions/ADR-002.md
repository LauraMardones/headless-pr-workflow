# ADR-002: Dispatcher Runner Selection — GitHub Actions

**Status:** Accepted  
**Date:** 2026-06-02  
**Story:** #168 — Write ADR-002 — GitHub Actions runner decision, superseding ADR-001  
**Feature:** #161 — Runner ADR and GitHub Actions dispatcher skeleton  
**Epic:** #160 — Autonomous story-level execution dispatcher

---

## Context

ADR-001 evaluated two options for the status-triggered automation runner and chose **Option B: External runner**, primarily to preserve OSS compatibility and data-driven executor routing. That decision has been reversed by the PO decision recorded in Epic #160 (2026-06-02).

This ADR supersedes ADR-001 for the dispatcher runner choice. ADR-001 remains as a historical record of the prior evaluation; its reasoning is not amended here, but the decision is overridden.

The Epic #160 PO decision (2026-06-02) established the following:

- **GitHub Actions is the chosen runner** for the autonomous dispatcher. It is event-driven, GitHub-native, and requires no external infrastructure.
- **OSS model integration is deferred.** The OSS compatibility concern that drove ADR-001's external-runner choice is addressed at the logic layer, not the runner layer, via a new OSS separability invariant (see Decision).
- A **5-minute scheduled poll** is the dispatch trigger mechanism: a GitHub Actions workflow runs on a cron schedule, queries the project board, and acts on items in `Ready for refinement` or `Ready for implementation`.

The options considered are the same two as ADR-001. No third option is introduced.

---

## Decision

**Option A: GitHub Actions** — the dispatcher runs inside GitHub Actions workflows on a scheduled cron trigger, polling the GitHub Projects v2 board every 5 minutes.

**New OSS separability invariant (verbatim):**  
> Runner is a separable wrapper; dispatcher logic must be expressible independently of the runner platform.

This invariant replaces ADR-001's approach of using a different runner to achieve OSS compatibility. The dispatcher's core logic (board query, WIP check, executor routing, handoff) must be written so that it can be extracted from GitHub Actions and run in any environment — the runner is a wrapper, not a coupling point.

---

## Options Considered

### Option A: GitHub Actions

Automation runs inside GitHub Actions workflows, triggered by scheduled cron (`*/5 * * * *`) or `workflow_dispatch`.

**Pros:**

- Native GitHub integration: `GITHUB_TOKEN` is automatically provisioned for each run; no external secret management.
- Version-controlled workflows: automation logic lives in `.github/workflows/` alongside the rest of the repository.
- Built-in runner infrastructure: no external hosting required.
- GitHub Projects v2 GraphQL API is accessible from Actions runners, as confirmed by prototypes #152 and #153.
- Parallel execution via job matrix is straightforward.
- Audit trail via Actions run logs is well-integrated with GitHub UI.
- The 5-minute scheduled poll fits naturally in a GitHub Actions cron workflow.

**Cons:**

- **OSS compatibility risk (HIGH)** — addressed by the new OSS separability invariant rather than by choosing a different runner.
- Cold-start latency (30–90 s) per run; acceptable for a 5-minute polling cadence.
- Long-running agentic sessions cannot run inside an Actions job; the dispatcher invokes executors via API calls, not embedded sessions.
- Token budget is GitHub Actions minutes for the dispatcher wrapper, not executor capacity. Executor capacity is managed separately via the token budget system (Feature #164).

### Option B: External Runner

Automation runs in an executor-managed session triggered by an external process that polls or subscribes to GitHub Projects v2 state.

**Pros:**

- OSS compatibility preserved without a separability invariant — any executor connects by declaring a capability profile.
- No cold-start constraint.
- Long-running agentic sessions are natively supported.
- Prototype scripts (#152, #153) are directly reusable as the detection layer.

**Cons:**

- **External infrastructure required.** A polling process or webhook relay must run somewhere external to GitHub. This is a real operational cost with no clear hosting owner.
- Secret management is external; tokens must be managed outside Actions' automatic provisioning.
- No built-in audit trail in GitHub Actions UI.
- Initial setup and maintenance burden falls on the PO, who does not have a dedicated ops environment.
- The prototype scripts are functional but were not designed to run as persistent services; adapting them would require infrastructure work outside the repository.

---

## Rationale

Option A is chosen for three reasons:

**1. The infrastructure cost of Option B is prohibitive given the current project context.**  
ADR-001 treated external infrastructure as a bounded, one-time cost. In practice, no persistent external hosting environment exists for this project. Option B requires a service to run somewhere reliable; Option A provides that infrastructure automatically. The absence of a hosting environment is a concrete blocker for Option B, not a theoretical risk.

**2. OSS compatibility is addressed at the logic layer, not the runner layer.**  
ADR-001 rejected Option A because running routing inside GitHub Actions would couple executor dispatch to GitHub-specific infrastructure. Epic #160 resolves this by introducing the OSS separability invariant: dispatcher logic must be written to be runner-agnostic. The invariant shifts the OSS compatibility burden from "use a different runner" to "write portable dispatcher logic" — a weaker and more practical constraint that Option A can satisfy without architectural compromise.

**3. The 5-minute scheduled poll is a better fit for GitHub Actions than for an external runner.**  
The PO decision in Epic #160 chose scheduled polling (not event-driven webhooks) as the dispatch mechanism, because GitHub project v2 webhooks are unreliable. A cron-scheduled GitHub Actions workflow is the native, lowest-complexity implementation of a 5-minute polling loop. Replicating this in an external runner adds scheduling infrastructure (cron daemon, persistent process, monitoring) that Actions provides for free.

ADR-001's finding that combined board+repository state queries are required (prototype #152, divergence D4) remains valid. The dispatcher will perform this combined query on each poll run, inside the Actions workflow, calling the same detection logic that would otherwise run externally.

---

## Consequences

- The dispatcher is implemented as a GitHub Actions workflow (`.github/workflows/dispatcher.yml`) on a `*/5 * * * *` cron schedule.
- All dispatcher core logic (board query, WIP check, executor routing, handoff protocol) must be written in a runner-agnostic form — shell scripts or equivalent — so that it can be extracted from the Actions wrapper and run elsewhere. This is the OSS separability invariant.
- GitHub Actions is not the executor for story work. The dispatcher workflow invokes executors (Claude Code, Codex) via API calls. Executor sessions run outside GitHub Actions.
- ADR-001's external-runner scripts (`scripts/project-status-sync.sh`, `scripts/workflow-intent-detect.sh`) remain as the detection layer. They are called from within the GitHub Actions dispatcher workflow.
- The `executor:` label and capability profile model in `docs/ADAPTERS.md` remains the routing mechanism. No changes to that model are required by this decision.
- OSS compatibility invariants from Epic #64 are preserved via the new separability invariant. Future executors may be added by declaring a capability profile; the dispatcher wrapper (GitHub Actions) is not part of the executor interface.
- Secret management uses GitHub Actions secrets (`GH_TOKEN`, Slack webhook URL) stored as repository secrets.
- Token budget for dispatcher overhead (Actions minutes) is separate from executor token budgets managed via Feature #164.

---

## Conditions for Revisiting

This decision should be revisited if any of the following occur:

1. The project gains a persistent external hosting environment (e.g., a dedicated server or cloud account managed by the PO) that makes Option B's operational cost negligible — at which point the external runner may offer better session continuity for longer dispatch cycles.
2. GitHub Actions cron scheduling proves unreliable for the 5-minute polling cadence (e.g., repeated skipped runs, queue delays exceeding 10 minutes under normal load), and Option B's direct polling offers materially better latency.
3. The dispatcher's core logic cannot be expressed in a runner-agnostic form — if the GitHub Actions wrapper becomes a structural dependency rather than a separable wrapper, the OSS separability invariant is violated and the decision must be re-evaluated.
4. The project's OSS compatibility invariants are formally extended by a PO decision to require OSS model integration (not merely OSS compatibility) — at which point the runner choice must be re-evaluated alongside executor routing.

---

## References

- Epic #160 (Decisions section, 2026-06-02) — PO decision authority for this ADR: GitHub Actions chosen as runner; OSS separability invariant introduced
- `docs/decisions/ADR-001-runner-integration.md` — prior decision, superseded by this ADR for dispatcher runner; remains as historical record
- #152 — Story: Prototype hpw project-status sync command (prototype findings, divergences D1–D4)
- #153 — Story: Prototype workflow-intent detection without automatic execution (prototype findings, divergences D1–D3)
- #161 — Feature: Runner ADR and GitHub Actions dispatcher skeleton
- `docs/PROJECT-STATUS.md` — OSS compatibility invariants, WIP pre-flight, status model
- `docs/ADAPTERS.md` — Executor routing model, capability profiles, `executor:` label format
- `scripts/project-status-sync.sh` — Sync prototype (#152), reused as detection layer
- `scripts/workflow-intent-detect.sh` — Intent-detection prototype (#153), reused as detection layer
