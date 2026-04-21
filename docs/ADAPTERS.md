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
