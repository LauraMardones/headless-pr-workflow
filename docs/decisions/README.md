# Architecture Decision Records

One decision per ADR. Keep each to about one page:

- **Context:** why a decision is needed now, in a few sentences. Link evidence (PRs, issues) instead of copying it.
- **Decision:** what was decided, stated so it can be checked.
- **Consequences:** what changes as a result, and when to revisit.

If a draft needs several independent decisions, write several ADRs. Working rules and conventions belong in `docs/` or `AGENTS.md`, not in an ADR.

## Approving an ADR

An ADR is approved by the PO's merge (ADR-011). It is merged with `**Status:** Accepted`; `Proposed` exists only on unmerged branches. When an ADR changes behavior that `docs/*.md` or the commands still describe the old way, it states an **Effective when:** line naming the follow-up work, and the current rules apply until then.

A PR that adds an ADR or changes a status line updates the [Index](#index) in the same PR.

## Replacing an ADR

Never delete or rewrite an accepted ADR. When a new ADR replaces it, change the old status line to `Superseded by ADR-00X`. When a new ADR changes only part of it, change the status line to `Accepted; amended by ADR-00X (<what>)`. The new ADR names the old one in a `**Supersedes:**` or `**Amends:**` line, so the chain can be followed both ways.

## Changing working rules

Working rules live in `docs/*.md`, `specs/README.md` and `AGENTS.md`. Change them through an ordinary PR from a refined issue. Such a PR goes through the normal review and merge gates, because agents follow these files as instructions. A rule change that contradicts an accepted ADR needs a new ADR first.

## Index

| ADR | Title | Status |
|---|---|---|
| 001 | [Runner Integration for Status-Triggered Automation](ADR-001-runner-integration.md) | Superseded by ADR-002 |
| 002 | [Dispatcher Runner Selection — GitHub Actions](ADR-002.md) | Accepted; amended by ADR-010 (dispatch trigger) |
| 003 | [Interim Executor Session Boundary — Embedded Loop, Pending Runner Migration](ADR-003-interim-executor-session-boundary.md) | Accepted (interim — see Conditions for Revisiting) |
| 006 | [Codex Cloud Review as the Cross-Provider Reviewer](ADR-006-codex-review.md) | Proposed |
| 007 | [Approval Gate Means Independent Review; Solo Maintenance Is the Default](ADR-007-approval-gate-independent-review.md) | Proposed |
| 008 | [An Epic's Specification Lives as a Spec in the Repository; Its Merge Is the Start Signal](ADR-008-epic-spec-in-repo.md) | Accepted |
| 009 | [Refinement Is Tech Lead Work and Makes No Product Decisions](ADR-009-tech-lead-driven-refinement.md) | Accepted |
| 010 | [The Project Board Shows Current State; It Never Triggers Work](ADR-010-board-shows-current-state.md) | Accepted |
| 011 | [Decision Documents Are Approved by the PO's Merge](ADR-011-decision-documents-approved-by-po-merge.md) | Accepted |
