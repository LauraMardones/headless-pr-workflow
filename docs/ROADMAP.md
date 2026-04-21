# Roadmap

## Phase 1: Normative Documentation

Create the core documents that define workflow invariants:

- Headless PR lifecycle.
- Role separation.
- SHA-bound approval.
- Merge policy.
- Takeover rules.
- Worktree model.
- Adapter boundaries.

## Phase 2: Normalized Automation Map

Convert the inherited script catalog into generic command contracts:

- Use stable extensionless command names.
- Classify each command by phase, priority, type, layer, and status.
- Move repo-specific rules into adapter categories.
- Keep assistant behavior optional.

## Phase 3: MVP Core Scripts

Implement the first deterministic Python CLI commands:

- `pr-context`
- `review-sha`
- `approval-check`
- `re-review-needed`
- `ci-summary`
- `unresolved-review-threads`
- `pre-merge`
- `pr-takeover`
- `workflow-status`
- `merge-pr`

Use mocked GitHub JSON fixtures for tests before relying deeply on live `gh` behavior.

## Phase 4: Assistant Adapters

Add non-normative examples for:

- Claude.
- Codex.
- Gemini.
- Copilot.
- Future assistants.

Each adapter should provide prompts and session recipes, not workflow authority.

## Phase 5: Example Repo Integrations

Add example repo adapters for:

- Protected file rules.
- Mandatory docs.
- Test command configuration.
- Required reviewers.
- Release gates.

Examples must be clearly labeled as optional patterns.
