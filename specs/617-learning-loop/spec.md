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

### Epic pre-mortem happens in `/spec` — 2026-11-20

**Chosen:** The pre-mortem is done in `/spec`, before the PO approves the spec PR.
**Rejected:** At refinement — a risk can change scope, and scope is the PO's call.

### Template and `spec-check` — 2026-11-20

**Chosen:** `## Pre-mortem` goes into `specs/_template/spec.md` with a new Definition of Ready item. `spec-check` requires the heading only for new specs. `/refine` runs `spec-check <spec> --ref <main commit>`, which reads the template at that commit.
**Rejected:** Requiring it for all specs — it would break the 8 merged specs (#160, #306, #418, #504, #518, #519, #530, #566).

### One lessons folder with tags — 2026-11-20

**Chosen:** `docs/lessons/` holds an index and one file per lesson. Tags separate workflow lessons from product lessons. A lens reads only the files whose tags match.
**Rejected:** Separate folders for workflow and product lessons — a lens would need two reading paths.

### Who owns lessons — 2026-11-20

**Chosen:** The Tech Lead owns the folder. The PO approves by merging the PR (ADR-011).
**Rejected:** The PO writes each lesson herself.

### Workflow lessons may change rules — 2026-11-20

**Chosen:** A lesson about the workflow may change `AGENTS.md` and command files through the same PR-merge flow.
**Rejected:** A separate ADR for every rule change.

### Post-mortem by level — 2026-11-20

**Chosen:** Epic and Feature at closure, every Bug always, Story and Task only after a deviation (review blocker, CI loop, rework or residual risk).
**Rejected:** All levels always — it costs tokens for little signal.

### Private notes move into the repo — 2026-11-20

**Chosen:** The status-from-live-source note and the local-tests rule move into `docs/lessons/` once it exists. The Bash and `gh` command notes stay private. The executor-label, verify-closure-CI and follow-up notes are already covered by the refine commands, `AGENTS.md` and the catch-all mechanism.
**Rejected:** Moving the Bash and `gh` notes — they depend on the PO's permission setup.

### Local tests rule — 2026-11-20

**Chosen:** Run only the changed module's own test file locally; CI runs the rest. This matches `AGENTS.md` and `/implement` step 16.
**Rejected:** Never run tests locally — it would change `AGENTS.md` and `/implement` step 16.

### Post-mortem facts and `retro.md` — 2026-11-20

**Chosen:** `/verify-closure` phase 1 may post only its one evidence comment, so the facts go there. `retro.md`, next to `spec.md`, is written later through a PR.
**Rejected:** `/verify-closure` writing `retro.md` itself — phase 1 may not change repository contents.

### Feature pre-mortem is a lens — 2026-11-20

**Chosen:** A new lens in `.claude/commands/refinement-lenses/`.
**Rejected:** More questions in `/refine-feature` — a lens is found automatically and needs no hardcoded list.

### Promotion of recurring lessons — 2026-11-20

**Chosen:** A lesson that hits two Epics is promoted to a test, a lens question or a rule, and its lesson file is deleted.
**Rejected:** Keeping every lesson forever — the folder would grow until agents stop reading it.

## Checked Against

- ADR-008, ADR-009 — no conflict. The spec is the PO's decision document; refinement and the Tech Lead's lessons follow it.
- ADR-010 — no conflict. Post-mortems and lessons record facts and set no board status.
- ADR-011 — no conflict. The lessons PR and the spec PR are approved by the PO's merge. A PR that mixes lessons with command or `AGENTS.md` changes follows the normal gates.
- `specs/README.md` and `specs/_template/spec.md` — change: the template gains `## Pre-mortem` and the Definition of Ready gains an item. The Feature that does so updates both.
- `.claude/commands/implement.md` step 15 — no conflict. It stays the only pre-mortem for code changes.
- `.claude/commands/verify-closure.md` — constraint: phase 1 posts only one evidence comment, so `retro.md` is written later.
- `.claude/commands/refinement-lenses/_template.md` — no conflict. A new lens is found automatically.
- `AGENTS.md` → Running Tests — no conflict. The local-tests lesson restates it.
- `docs/ROLES.md` — no conflict. The Analyst runs the pre-mortem and the Tech Lead owns the lessons.

## Delegated to the Tech Lead

- Slicing into Features and Stories, and their order.
- The lesson file format and the tag names.
- The wording of the pre-mortem and post-mortem lens questions.
- How commands and lenses find the lessons whose tags match.
- The list of facts a post-mortem records, and the exact deviation test for a Story or Task.

## Dependencies

- Hard: none.
- Soft: Epic #306 and Epic #600 also change the refine commands. The scheduler's file-overlap rule keeps them from colliding.

## Pre-mortem

This spec predates the template section, so it is a trial of it.

- Lessons grow until agents skip them. Mitigation: tag-matched reading (E4) and promotion (E5).
- A post-mortem on every deviation adds token cost to small work. Mitigation: Story and Task only after a deviation.
- A lesson rewrites a rule on thin evidence. Mitigation: the PO approves each rule change by merge.

## Open Questions

None.
