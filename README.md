# Headless PR Workflow

Reusable, assistant-agnostic workflow policy and automation patterns for GitHub-centered pull request work.

This repository treats GitHub as the system of record for issues, pull requests, reviews, approvals, CI status, blockers, and merge state. Local branches, worktrees, and assistant sessions are temporary execution contexts.

## Core Principles

- GitHub is the source of truth.
- Review approval is bound to a specific PR head SHA.
- Implementation and review must not happen in the same session for the same reviewed head SHA.
- New commits after approval require approval to be re-evaluated.
- Merge requires a fresh GitHub refresh immediately before merge.
- Merge ownership belongs to the session that last implemented the PR head that was approved.
- Takeover between assistants and sessions is a first-class workflow path.
- Deterministic checks belong in scripts.
- Repo-specific rules belong in repo adapters.
- Assistant-specific behavior belongs in optional assistant adapters.

## Repository Layout

```text
docs/                       Normative policy and reusable workflow docs
src/headless_pr_workflow/    Python core CLI and command catalog
scripts/                    Thin convenience wrappers
examples/                   Optional assistant and repo adapter examples
tests/                      Deterministic tests for workflow metadata and code
```

## First Documents

- `docs/HEADLESS-PR-WORKFLOW.md`
- `docs/ROLES.md`
- `docs/MERGE-POLICY.md`
- `docs/TAKEOVER-RULES.md`
- `docs/WORKTREE-MODEL.md`
- `docs/PR-AUTOMATION-MAP.md`
- `docs/ADAPTERS.md`

## CLI Direction

The stable command surface is `hpw <command>`. The implementation baseline is Python, with optional shell wrappers for convenience. Commands should expose deterministic GitHub and local state checks without encoding product-specific rules.

## Current Status

This is an initial scaffold for Phase 1 and Phase 2:

- Normative documentation structure.
- Normalized automation map.
- Python command catalog.
- Thin wrapper placeholders.

MVP command implementations should be added after the policy docs and command contracts are reviewed.
