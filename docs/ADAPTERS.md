# Adapters

Adapters extend the reusable workflow without changing core policy.

## Core Workflow Boundary

Core owns:

- GitHub source-of-truth rules.
- SHA-bound approval semantics.
- Review and implementation separation.
- Takeover protocol.
- Merge policy.
- Generic worktree model.
- Generic command contracts.

Core must not own:

- Product-specific domain checks.
- Repository-specific documentation requirements.
- Release train rules.
- Assistant-specific prompts.
- Local team preferences that are not workflow invariants.

## Repo Adapters

Repo adapters encode project-specific deterministic rules.

Examples:

- Protected files.
- Mandatory docs based on changed paths.
- Domain contract drift checks.
- Required test commands.
- Release gates.
- Required labels.
- Required reviewers by path.

Repo adapters may add gates to pre-review or pre-merge flows. They must not weaken core gates.

## Assistant Adapters

Assistant adapters provide optional session guidance for tools such as Claude, Codex, Gemini, Copilot, or future assistants.

Examples:

- Session-start prompt.
- Implementation-only prompt.
- Review-only prompt.
- Takeover prompt.
- Pre-merge checklist prompt.
- Tool-specific command recipes.

Assistant adapters must remain non-normative. The workflow must still function if an assistant adapter is absent.

## Adapter Contract

An adapter should declare:

- Adapter name.
- Adapter type: `repo` or `assistant`.
- Inputs it requires.
- Commands or prompts it provides.
- Core gates it depends on.
- Additional gates it adds.
- Whether failure is blocking or advisory.

Adapter output should be structured enough for another assistant or script to consume.

## Executor Routing

Executor routing is data-driven. Capability profiles are declared per executor; no routing logic is hardcoded anywhere. This is an OSS compatibility invariant: any open-source model or future executor can participate by declaring a capability profile — routing must never depend on executor-specific logic embedded in commands or prompts.

### Executor Roles

| Executor | Role | Rationale |
|---|---|---|
| Claude Code | Implementation | Faster output, better context awareness, stronger on UI and code style. |
| Codex | Code review, CI/CD, algorithmically complex tasks | Stronger bug identification, structured reasoning, 2–3× more token-efficient. |

The Claude Code / Codex split is a declared capability boundary, not a policy preference. Any executor that declares equivalent capabilities may take either role.

### Claude Model Tiers (Claude Code)

> Advisory only, current as of 2026-08-09 — for human calibration when choosing an `executor:` label. Routing does not depend on these values: `scripts/dispatcher-invoke.sh` routes purely off `executor:` label suffixes, never off the model names or versions in this table.

| Label | Model | Appropriate task types |
|---|---|---|
| `executor:claude-code-haiku` | Haiku 4.5 (`claude-haiku-4-5-20251001`) | Setup, boilerplate, quick edits, scaffolding |
| `executor:claude-code-sonnet` | Sonnet 5 (`claude-sonnet-5`) | Standard implementation, agentic workflows |
| `executor:claude-code-opus` | Opus 5 (`claude-opus-5`) | Deep code review, complex reasoning, long-horizon tasks |

### `executor:` Label Format

Four label values are defined:

- `executor:claude-code-haiku`
- `executor:claude-code-sonnet`
- `executor:claude-code-opus`
- `executor:codex`

The `executor:` label is applied to a story when its status moves to **Ready for implementation**. It signals which executor profile should pick up the story. Executors pull only from "Ready for implementation" — never from "Refined" or any other status.

### Capability Profile Structure

Each executor declares its own capability profile. The profile is the source of truth for routing decisions. Profiles are not inferred from executor names, tool versions, or runtime behavior.

A capability profile declares:

- Executor identifier (matches the `executor:` label value).
- Roles the executor can fulfil.
- Task types the executor is optimised for.
- Any capacity or token constraints relevant to story sizing.

Routing logic must read declared profiles, not branch on executor names. This is the OSS invariant: the workflow remains portable to any executor that can declare a conforming profile.

## Decisions

### Fable-tier executor label — 2026-08-09

**Chosen:** Do not add `executor:claude-code-fable` now. The Claude Model Tiers table above stays at three tiers (Haiku, Sonnet, Opus). Revisit only if a concrete story needs Fable-level capability that Opus can't handle.

**Rejected:** Adding Fable as a fifth executor tier now (new label, four `scripts/dispatcher-invoke.sh` table rows, new `BUDGET_DAILY_FABLE` budget variable). Rejected because Fable costs roughly 2× Opus per unit of work (`claude-fable-5` at $10/$50 per MTok vs. `claude-opus-5` at $5/$25 per MTok) and responds more slowly, while Opus already covers the "deep code review, complex reasoning, long-horizon tasks" niche Fable would also serve. Building and maintaining a costly, unused fifth tier speculatively isn't worth it. Matches the precedent set in Bug #244, which deferred an analogous automated label-existence guard until a fifth tier is actually proposed.

## Cross-Reference

The canonical executor routing entry points are split by responsibility:

- Status semantics, pull eligibility, WIP pre-flight checks, handoff format, blocker protocol, closure protocol, and OSS compatibility invariants are defined in `docs/PROJECT-STATUS.md`.
- Adapter boundaries, executor roles, `executor:` labels, and capability profile structure are defined in this file.
- Lifecycle and source-of-truth rules are defined in `docs/HEADLESS-PR-WORKFLOW.md`.

When adding or changing executor routing, update this file for adapter-facing capability details and update `docs/PROJECT-STATUS.md` only when the workflow contract itself changes. Do not duplicate canonical status or protocol definitions in adapter-specific prompts or commands.
