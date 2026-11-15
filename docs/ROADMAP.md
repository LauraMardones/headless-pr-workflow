# Roadmap

GitHub is the operational source of truth for Epic scope, Feature progress, implementation issues, delivery order and blockers. This document describes the structure; it does not duplicate the backlog.

## Planning model

- An Epic issue indexes its merged spec, which defines the approved outcome and scope (ADR-008).
- Feature issues group independently verifiable capabilities and name the Epic criteria they serve (ADR-009).
- Story, Task and Bug issues deliver Feature criteria. GitHub parent links and the `Parent epic:` and `Feature group:` lines connect them.
- Priority labels, hard dependencies, the ready buffer and WIP limits govern the Kanban flow. The dispatcher derives status from GitHub facts; the board displays that status (ADR-010).
- The Epic issue's progress section and the Feature and implementation issue states show what remains. Closure follows `/verify-closure`.

Milestones are retired (ADR-015). Existing milestone associations are historical and do not determine scope, priority, scheduling, status or closure. No milestone is required for a new spec or issue.

## Sequencing rules

- Implement factual commands before advisory and orchestration commands.
- Implement dry-run modes before live mutation commands.
- Prefer mocked GitHub fixtures over deep reliance on live `gh` behavior.
- Keep repository-specific and assistant-specific logic out of core commands.

