# ADR-001: Runner Integration for Status-Triggered Automation

**Status:** Accepted  
**Date:** 2026-06-01  
**Story:** #154 — Evaluate GitHub Actions vs external runner  
**Feature:** #111 — Prototype project status automation  
**Epic:** #64 — Project-driven workflow orchestration

---

## Context

Epic #64 commits this project to automated, status-driven executor routing: when a story reaches `Ready for implementation`, the correct executor should be triggered without manual intervention. Before any automation can be built, the runner architecture must be decided.

Two prototypes were built as empirical grounding for this decision:

- **#152** — `scripts/project-status-sync.sh`: a shell prototype that reads GitHub Projects v2 state and detects workflow status transitions from observable repository facts (branch existence, PR state, merge status).
- **#153** — `scripts/workflow-intent-detect.sh`: a shell prototype that reads GitHub Projects v2 state and identifies items whose status is `Ready for implementation`.

Both prototypes confirmed that the GitHub Projects v2 GraphQL API is reliably accessible from any environment with a token carrying `repo` and `project` scopes. Neither prototype requires GitHub Actions infrastructure to function.

The decision to make is: **where should the automation run — inside GitHub Actions, or in an external runner?** This decision must be made now because it is the last prerequisite before Epic #64 can advance beyond manual status changes, and because the wrong choice here would couple the routing model to a specific execution platform in ways that are hard to reverse.

---

## Decision

**Option B: External runner** — automation runs outside GitHub Actions, in an executor-managed session triggered by webhook or poll against the GitHub Projects v2 API.

---

## Options Considered

### Option A: GitHub Actions

Automation runs inside GitHub Actions workflows, triggered by project status webhooks (`project_v2_item` events) or `workflow_dispatch`.

**Pros:**

- Native GitHub integration: GITHUB_TOKEN is automatically provisioned for each run; no external secret management.
- Version-controlled workflows: automation logic lives in `.github/workflows/` alongside the rest of the repository.
- Built-in runner infrastructure: no external hosting required.
- GitHub Projects v2 GraphQL API is accessible from Actions runners, as confirmed by prototypes #152 and #153.
- Parallel execution via job matrix is straightforward.
- Audit trail via Actions run logs is well-integrated with GitHub UI.

**Cons:**

- **OSS compatibility is broken by design.** GitHub Actions runners use GitHub-provisioned infrastructure. Plugging in an OSS model (Llama, Mistral, Qwen) requires self-hosted runners, which introduces external infrastructure — negating the "no external hosting" advantage and adding complexity that varies per model.
- **Executor routing must become logic-driven, not data-driven.** The data-driven routing model defined in `docs/ADAPTERS.md` requires routing to read declared capability profiles. Routing inside a GitHub Actions workflow would require explicit `if: executor == "X"` branches or matrix job names tied to executor identities — a direct violation of the OSS compatibility invariant stated in `docs/PROJECT-STATUS.md` ("Executor Routing Is Data-Driven, Not Logic-Driven").
- **Project status webhooks are unreliable as a trigger.** The `project_v2_item` webhook event for status field changes is available but is a beta-tier event without the same delivery guarantees as core GitHub events. Prototype #152 found that detecting transitions requires polling both Projects v2 state and repository state (branches, PRs, reviews) — a webhook that fires only on project field changes is insufficient; the sync must also observe PR and branch state.
- **Cold-start latency.** Runner startup adds 30–90 seconds before any execution begins. For a workflow designed around near-real-time intent-signal detection, this latency is a material constraint.
- **Long-running agentic sessions are constrained.** GitHub Actions jobs time out after 6 hours (default) and are designed for discrete CI tasks. Executor sessions for story implementation can run longer and require interactive state that Actions does not support well.
- **Token budget is GitHub Actions minutes, not workflow-native capacity.** The capacity abstraction in `docs/PROJECT-STATUS.md` requires expressing executor capacity in abstract scheduling terms, not provider-specific infrastructure units. Actions minutes and token limits are not interchangeable across executor types.

### Option B: External Runner

Automation runs in an executor-managed session (Claude Code web session webhook, Codex API call, or equivalent) triggered by an external process that polls or subscribes to GitHub Projects v2 state.

**Pros:**

- **OSS compatibility preserved by design.** Any executor — cloud-hosted or local/OSS — connects to the runner model by declaring a capability profile and receiving webhook triggers or polling the detection script output. No GitHub-specific infrastructure is required on the executor side. This directly satisfies the "GitHub Tool Use Is A Separable Wrapper" invariant in `docs/PROJECT-STATUS.md`.
- **Executor routing remains purely data-driven.** The `workflow-intent-detect.sh` prototype (#153) confirms that intent signals are detectable via GraphQL from any external process. Routing logic reads the `executor:` label from the detected intent signal and dispatches accordingly — no hardcoded executor branches required.
- **Trigger model matches the fact-vs-intent architecture.** The intent-detect prototype (#153) detects `Ready for implementation` signals reliably. An external runner that polls this signal (or receives a webhook forwarded from it) acts only when the intent condition is genuinely satisfied, not in response to every project field change.
- **No cold-start constraint.** External executor sessions connect directly to the GitHub API without runner provisioning overhead.
- **Long-running agentic sessions are natively supported.** External executors manage their own session lifecycle; they are not constrained by Actions job timeouts or GitHub's runner queue.
- **Capacity is abstracted.** Each executor manages its own token or compute budget. The orchestrator's capacity model is expressed in abstract scheduling terms (WIP limit, story sizing) rather than Actions minutes.
- **Prototype evidence is directly reusable.** Both prototypes (#152 and #153) are already external-runner scripts that can serve as the detection layer with no architectural change.

**Cons:**

- **External infrastructure required for the trigger layer.** A polling process or webhook relay must run somewhere external to GitHub. This is a real operational cost not present in Option A.
- **Secret management is external.** Tokens must be managed outside GitHub Actions' automatic provisioning. This is mitigated by the fact that the scripts already require `GH_TOKEN` or `GITHUB_TOKEN` as an environment variable, and all major executor platforms support secret injection.
- **No built-in audit trail from GitHub Actions.** Execution logs do not appear in the GitHub Actions UI. Durable state must be recorded in GitHub (PR comments, issue comments) — which the workflow already requires via the handoff protocol.
- **Initial setup is more complex.** A webhook endpoint or polling service must be deployed or configured. This is a one-time infrastructure cost.

---

## Rationale

Option B is preferred for three reasons that are architectural, not preference-based:

**1. OSS compatibility requires it.**  
`docs/PROJECT-STATUS.md` declares two HIGH-risk invariants: "GitHub Tool Use Is A Separable Wrapper" and "Executor Routing Is Data-Driven, Not Logic-Driven." Option A cannot satisfy both simultaneously: running routing inside GitHub Actions requires either hardcoding executor branches (violates data-driven routing) or deploying self-hosted runners per OSS model (makes GitHub the execution host, not a separable wrapper). Option B satisfies both invariants without compromise.

**2. The prototype findings confirm Option B is already working.**  
Both `project-status-sync.sh` (#152) and `workflow-intent-detect.sh` (#153) are functional external-runner scripts that access the GitHub Projects v2 GraphQL API. The detection mechanism required by Option B is proven. The transition detection in #152 confirmed that reliable status sync requires observing both Projects v2 state and repository state (branches, PRs) — a combined query that fits naturally in an external script but is awkward to orchestrate across GitHub Actions steps.

**3. Prototype #152 surfaced a structural finding that disfavours Actions triggers.**  
The sync script (D4) found that PR–issue linking via GitHub's Development sidebar is not detectable via the API — only body-text linking is reliable. This means transition detection cannot be fully driven by project field change webhooks alone; the sync script must re-evaluate all observable state on each run. An external runner that calls the sync script on demand is architecturally simpler than an Actions workflow that must re-fetch all this state inside a triggered job.

The operational cost of Option B (external trigger infrastructure) is real but bounded and one-time. The architectural costs of Option A (OSS incompatibility, logic-driven routing) are unbounded and compound as more executors are added.

---

## Consequences

- A follow-on feature must define the external trigger layer: how the runner is invoked (polling interval, webhook relay, or manual dispatch) and where it runs. This story does not specify tooling for that layer.
- The prototype scripts (`scripts/project-status-sync.sh`, `scripts/workflow-intent-detect.sh`) serve as the detection layer of the external runner model. They are production-ready prototypes, not throwaway code.
- GitHub Actions is not ruled out for CI-type tasks (linting, tests, contract validation). This ADR applies only to the status-triggered automation that drives executor routing and story lifecycle management.
- The `executor:` label and capability profile model defined in `docs/ADAPTERS.md` remains the routing mechanism. No changes to that model are required by this decision.
- OSS compatibility invariants in `docs/PROJECT-STATUS.md` remain intact: future executors may be added by declaring a capability profile and connecting to the external runner's dispatch mechanism, without changes to core workflow logic.

---

## Conditions for Revisiting

This decision should be revisited if any of the following occur:

1. GitHub ships a stable, guaranteed-delivery `project_v2_item` webhook that includes both status field changes and linked PR/branch state in a single event payload — removing the need for the combined-query approach found in prototype #152.
2. GitHub Actions introduces native support for long-running agentic executor sessions (e.g., persistent job contexts that survive beyond the current job-timeout model).
3. The project's OSS compatibility invariants are formally relaxed by a PO decision — specifically, if single-executor (non-OSS) operation becomes acceptable policy.
4. The external trigger infrastructure cost proves prohibitive in practice (e.g., no reliable hosting environment is available for the trigger layer), and Option A's self-hosted runner approach is evaluated as lower-cost by the PO.

---

## References

- #152 — Story: Prototype hpw project-status sync command (prototype findings, divergences D1–D4)
- #153 — Story: Prototype workflow-intent detection without automatic execution (prototype findings, divergences D1–D3)
- `docs/PROJECT-STATUS.md` — OSS compatibility invariants, WIP pre-flight, status model
- `docs/ADAPTERS.md` — Executor routing model, capability profiles, `executor:` label format
- `docs/commands/project-status-sync.md` — Sync command contract
- `scripts/project-status-sync.sh` — Sync prototype (#152)
- `scripts/workflow-intent-detect.sh` — Intent-detection prototype (#153)
