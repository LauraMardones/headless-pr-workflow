# Architecture Decision Records

One decision per ADR. Keep each to about one page:

- **Context:** why a decision is needed now, in a few sentences. Link evidence (PRs, issues) instead of copying it.
- **Decision:** what was decided, stated so it can be checked.
- **Consequences:** what changes as a result, and when to revisit.

If a draft needs several independent decisions, write several ADRs. Working rules and conventions belong in `docs/` or `AGENTS.md`, not in an ADR.

## Approving an ADR

An ADR is approved by the PO's merge (ADR-011). It is merged with `**Status:** Accepted`; `Proposed` exists only on unmerged branches. When an ADR changes behavior that `docs/*.md` or the commands still describe the old way, it states an **Effective when:** line naming the follow-up work, and the current rules apply until then.

## Replacing an ADR

Never delete or rewrite an accepted ADR. When a new ADR replaces it, change the old status line to `Superseded by ADR-00X`. When a new ADR changes only part of it, change the status line to `Accepted; amended by ADR-00X (<what>)`. The new ADR names the old one in a `**Supersedes:**` or `**Amends:**` line, so the chain can be followed both ways.

## Changing working rules

Working rules live in `docs/*.md`, `specs/README.md` and `AGENTS.md`. Change them through an ordinary PR from a refined issue. Such a PR goes through the normal review and merge gates, because agents follow these files as instructions. A rule change that contradicts an accepted ADR needs a new ADR first.
