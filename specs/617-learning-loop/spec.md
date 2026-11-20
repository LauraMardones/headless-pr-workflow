# Epic #617: Learning loop: pre-mortem, post-mortem and lessons

**Epic issue:** #617

## Goal

Agents keep no memory between sessions, so the same mistakes repeat. HPW has a pre-mortem only inside `/implement` step 15 and no post-mortem. After this Epic, risks are named before work starts and facts are recorded at closure. Lessons live in `docs/lessons/`, where later sessions read them. The PO and every agent gain from it now, because Epics run mostly unattended and nobody carries the lessons in their head.

## Scope

In:

- A `## Pre-mortem` section in the spec template, a new Definition of Ready item, and `spec-check` requiring the heading only for new specs.
- A Feature pre-mortem lens in `.claude/commands/refinement-lenses/`.
- A post-mortem at Epic and Feature closure, on every Bug, and on a Story or Task only after a deviation (review blocker, CI loop, rework or residual risk).
- `/verify-closure` posts the facts in its one evidence comment. `retro.md`, next to `spec.md`, is written later through a PR.
- `docs/lessons/`: an index and one file per lesson, with tags that separate workflow lessons from product lessons. A lens or command reads only the files whose tags match.
- The Tech Lead owns the lessons folder. The PO approves by merging the PR. A workflow lesson may change `AGENTS.md` and command files through that same PR.
- A lesson that hits two Epics is promoted to a test, a lens question or a rule, and its lesson file is deleted.
- Two private working-practice notes move into lessons: status from a live source, and the focused-file-only rule for local tests.

Out:

- Changing the `/implement` step 15 pre-mortem.
- The PO's private Bash and `gh` permission notes. They depend on her permission setup and stay private.
- Any board status change (ADR-010).

## Acceptance Criteria

- **E1:** `/spec` cannot reach a merged spec without a `## Pre-mortem`.
  - **Verified by:** `spec-check` fails a new spec without the heading, while the 8 merged specs still pass.
- **E2:** Every Feature breakdown applies a pre-mortem lens.
  - **Verified by:** the next refined Feature shows the lens answers under `## Decisions`.
- **E3:** Closing an Epic or Feature, and every Bug, produces recorded post-mortem facts. A Story or Task produces them only after a deviation.
  - **Verified by:** one real closure of each kind.
- **E4:** A session reads only the lessons whose tags match its work.
  - **Verified by:** a lens or command run that lists the lesson files it read.
- **E5:** A lesson that recurs in a second Epic is promoted and its file is deleted.
  - **Verified by:** one real promotion, with the test, lens question or rule that replaced the file.

## Decisions

### <Decision title>

**Chosen:** <…>
**Rejected:** <alternative> — <why>

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
