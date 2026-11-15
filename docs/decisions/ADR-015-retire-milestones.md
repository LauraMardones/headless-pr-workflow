# ADR-015: Retire milestones from the HPW workflow

**Status:** Accepted
**Date:** 2026-11-15
**Related:** ADR-009, `docs/ROADMAP.md`, issue #569
**Amends:** ADR-009's milestone assignment in refinement
**Effective when:** issue #569 updates the working rules and closes the existing GitHub milestones. Until then, the current rules apply.

## Context

HPW uses a continuous Kanban flow. Epics express outcomes, Features group capabilities, and the scheduler chooses work from GitHub facts, priority, dependencies and WIP limits. Milestones add a second grouping that does not guide this flow. M1–M5 remain open with no open issues; M6 contains work from several unrelated Epics. None of the existing milestones has a due date.

## Decision

1. HPW no longer uses GitHub milestones for planning, sequencing, refinement, status or closure. No new issue or PR is assigned a milestone by an HPW command.
2. The hierarchy is Epic → Feature → Story, Task or Bug. An Epic's merged spec defines its scope; Features and their children provide progress. Priority, dependencies, the ready buffer and WIP limits govern the Kanban flow.
3. Close the existing GitHub milestones after this decision is merged. Keep their historical issue associations as a record; do not reassign open issues to another milestone. A closed milestone does not block work.
4. Remove milestone requirements from working rules, command formats and the spec template. Existing merged specs and historical issue decisions keep their original milestone text as historical records; changing those lines would require unrelated spec PRs.
5. This supersedes only ADR-009's statements that refinement sets a milestone. Its other refinement rules remain in force.

## Consequences

- Issue #569 becomes the retirement task, including the one-time closure of existing milestones after this ADR merges.
- The roadmap is expressed through Epics, Features, priorities and GitHub issue relationships. No replacement milestone or date field is introduced.
- If a future delivery deadline needs tracking, the PO makes a separate decision about that specific need.

