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

| Label | Model | Appropriate task types |
|---|---|---|
| `executor:claude-code-haiku` | Haiku 4.5 | Setup, boilerplate, quick edits, scaffolding |
| `executor:claude-code-sonnet` | Sonnet 4.6 | Standard implementation, agentic workflows |
| `executor:claude-code-opus` | Opus 4.8 | Deep code review, complex reasoning, long-horizon tasks |

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
