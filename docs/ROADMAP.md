# Roadmap

GitHub should be the operational source of truth for roadmap state, epic status, stories, sequencing, and delivery progress.

This document does not own the backlog. It defines only the durable roadmap structure that GitHub planning artifacts should follow.

## Source of Truth

Track the following in GitHub, not in repository markdown:

- epics
- stories
- status
- delivery order
- target milestone
- blockers
- roadmap progress

Repository markdown should define workflow policy, command contracts, and adapter boundaries, but should not become a second planning system.

## Planning Model

Use GitHub planning artifacts with this structure:

- one GitHub issue per epic
- one GitHub issue per story
- labels for type, priority, area, and status
- milestones for delivery increments
- issue links or task lists to connect stories to epics
- optional GitHub Project views for board and timeline reporting

## Milestone Structure

### Milestone 0: Foundation Complete

Goal: define the rules before scaling automation.

Examples of work that belongs here:

- normative workflow documentation
- command catalog normalization
- initial implemented core commands
- deterministic tests for implemented commands

### Milestone 1: Session and Review Safety

Goal: make session start, takeover, review state, and blocker detection reliable from GitHub state.

Examples of work that belongs here:

- `worktree-status`
- `pr-takeover`
- `re-review-needed`
- `unresolved-review-threads`
- `ci-summary`
- shared GitHub scenario fixtures

### Milestone 2: Orchestration and Merge Decisioning

Goal: turn workflow facts into explicit session guidance and guarded merge decisioning.

Examples of work that belongs here:

- `next-action`
- `workflow-status`
- `review-delta`
- `target-branch-check`
- `merge-owner`
- `merge-pr` dry-run

### Milestone 3: Merge Execution and Post-Merge Hygiene

Goal: safely execute the merge path and leave local state clean.

Examples of work that belongs here:

- `merge-pr` execution
- `post-merge-sync`
- `branch-cleanup`
- `blocking-comments`

### Milestone 4: Adoption Layer

Goal: make the workflow easy to adopt across tools and repositories without changing core policy.

Examples of work that belongs here:

- assistant adapter contract and examples
- repo adapter examples
- usage-flow documentation
- top-level documentation alignment

### Milestone 5: Multi-Executor Orchestration

Goal: coordinate project-driven workflow status, executor routing, and automation intent across Codex, Claude Code, future executors, and human operators while preserving GitHub as the operational source of truth.

Feature list:

- #109 Feature: Define and document project-driven workflow model.
- #110 Feature: Project status sync command contract.
- #111 Feature: Prototype project status automation.

High-level acceptance criteria:

- `docs/PROJECT-STATUS.md` is the canonical workflow contract for project status semantics, handoff, recovery, blocked status, closure, rolling refinement, WIP pre-flight, and OSS compatibility invariants.
- `docs/ADAPTERS.md` documents executor routing, capability profiles, and adapter boundaries without hardcoding executor-specific routing logic.
- Project-status sync has a dry-run command contract before any live mutation behavior.
- Workflow-intent detection is prototyped without automatic hands-off execution.
- GitHub Actions vs external runner tradeoffs are recorded in a formal ADR before runner automation is adopted.
- Open-source executor compatibility remains protected by explicit invariants for GitHub wrappers, routing, capacity, pre-flight policy, and handoff format.

## Sequencing Rules

- Implement factual commands before advisory/orchestration commands.
- Implement dry-run modes before live mutation commands.
- Prefer mocked GitHub fixtures over deep reliance on live `gh` behavior.
- Keep repo-specific and assistant-specific logic out of core commands.

## Out of Scope for MVP

- cost and telemetry reporting
- session registry features
- aggressive cleanup automation
- repo-specific gates encoded in core
- assistant-specific workflow authority
