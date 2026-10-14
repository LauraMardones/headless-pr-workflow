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

### Cross-Provider Review Pairing

The Executor Roles table above describes the intended split, but a story's `executor:` label only ever governs `/implement` — it says nothing about who reviews it. `scripts/dispatcher-invoke.sh` closes that gap from two lookup tables, keyed by the executor that performed `/implement` (issues #263 and #293, ADR-006). No new `executor:` label value is introduced or stored — the review path is derived, not assigned.

| `/implement` executor | Review | Table in `scripts/dispatcher-invoke.sh` |
|---|---|---|
| `executor:claude-code-haiku` | Codex Cloud, through the Codex review loop — no dispatcher session | `CODEX_REVIEW_LOOP_ROUTING` |
| `executor:claude-code-sonnet` | Codex Cloud, through the Codex review loop — no dispatcher session | `CODEX_REVIEW_LOOP_ROUTING` |
| `executor:claude-code-opus` | Codex Cloud, through the Codex review loop — no dispatcher session | `CODEX_REVIEW_LOOP_ROUTING` |
| `executor:codex` | A `/review` session under Claude Opus (the "deep code review, complex reasoning" tier — see Claude Model Tiers below) | `REVIEW_EXECUTOR_ROUTING` |

An executor with a row in neither table stops with a mid-cycle blocker after `/implement`, so a typo or a new tier never skips review silently.

**Claude-implemented stories (Codex review loop).** Each Claude-implemented head is reviewed once, by Codex Cloud (ADR-006). `/implement` posts the review request, and the `codex-review-bookkeeping` workflow reads the answer and moves the story. The dispatcher's part is:

1. **Hand-off.** After `/implement` succeeds, the dispatcher starts no `/review` session and adds nothing to a review budget. It checks that the story has a linked open PR that is not a draft, writes `In review` only when the story is still `In implementation` and no fix round is due, and ends that story's run.
2. **Resume at `In merge`.** On a later run, a story in `In merge` gets `/merge`, then `Done` and `/cleanup`, both as scripts (see Script-only phases below). This applies to every story, whatever its executor. There is one `/merge` attempt per PR head.
3. **Fix round.** A story back in `In implementation`, for which the bookkeeping job recorded findings on the PR's current head, gets `/implement` again on the same PR. After that the hand-off applies again. The number of fix rounds per PR is limited by the repository variable `CODEX_FIX_ROUND_MAX` (default `3`).

Each run handles resumable work before new work: stories in `In merge`, then fix rounds, then **Ready for implementation**. The rules, bounds and log lines are in [DISPATCHER-CONFIG.md](DISPATCHER-CONFIG.md#codex-review-loop-hand-off-and-resume).

**Codex-implemented stories (paired session).** `/review` runs in the same dispatcher run, under Claude Opus, followed by the script-only `/merge` and `/cleanup`.

**Script-only phases: `/merge` and `/cleanup` (issue #264).** No executor tier applies to these two phases. The dispatcher starts no model session for them: no Claude Code session, no OpenAI call, no subscription-allowance check and no executor budget increment. `run_merge_or_block()` and `finish_story()` in `scripts/dispatcher-invoke.sh` run `scripts/dispatcher-merge.sh` and `scripts/dispatcher-cleanup.sh`, which reuse `scripts/merge-gate-summary` and the `hpw` helpers. This holds in every path: the Claude-tier cycle, the paired `executor:codex` run and the resume pass at `In merge`. A step the scripts cannot judge (for example a dirty file that is neither clearly a stale copy of the merged PR nor clearly unrelated) is kept and reported, never forced. The step-by-step map from `merge.md` and `cleanup.md` to the scripts and their tests is in [DISPATCHER-MERGE-CLEANUP-MAP.md](DISPATCHER-MERGE-CLEANUP-MAP.md). A manually run `/merge` or `/cleanup` still follows `.claude/commands/merge.md` and `cleanup.md`, which are unchanged; a later edit to either file needs the matching script change. ADR-012's session-start mechanism does not cover these two phases.

This applies only to the dispatcher's own invocation path in `scripts/dispatcher-invoke.sh`. A human or interactive session running `/review` manually (`.claude/commands/review.md`) chooses its own reviewer and is unaffected.

### Executor Invocation

The dispatcher starts every model session (`/implement` and `/review`) through `invoke_executor_command()` in `scripts/dispatcher-invoke.sh`. The provider comes from the `EXECUTOR_PROVIDER` table, keyed by the executor label, so no executor name is branched on elsewhere. The task prompt is always `.claude/commands/<command>.md` with `$ARGUMENTS` substituted.

| Executor labels | Provider | How the session starts |
|---|---|---|
| `executor:claude-code-haiku`, `-sonnet`, `-opus` | `claude-code` | Claude Code (`claude -p`) on the GitHub Actions runner, signed in with the PO's subscription token `CLAUDE_CODE_OAUTH_TOKEN`, with the model alias `haiku`, `sonnet` or `opus` (ADR-012, issue #314) |
| `executor:codex` | `openai` | A bounded tool-use loop against the OpenAI API with a single `bash` tool (issue #254), until #315 |

Every Claude session runs this way: `/implement` and its fix rounds, and `/review` of a Codex-implemented story. `/merge` and `/cleanup` are script-only phases and start no session. No metered Anthropic API key is used. The command line, bounds, result handling and log lines are in [DISPATCHER-CONFIG.md](DISPATCHER-CONFIG.md#executor-invocation-mechanism).

### Claude Model Tiers (Claude Code)

> Advisory only, current as of 2026-08-09 — for human calibration when choosing an `executor:` label. Routing does not depend on these values: `scripts/dispatcher-invoke.sh` routes purely off `executor:` label suffixes, never off the model names or versions in this table. Since issue #314 the dispatcher starts each tier with the Claude Code alias (`haiku`, `sonnet`, `opus`); the Claude Code version pinned in `.github/workflows/dispatcher.yml` decides which model each alias means.

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
