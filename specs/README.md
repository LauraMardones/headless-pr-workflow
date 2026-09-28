# Epic Specs

Each Epic's design lives here as `specs/<epic-number>-<slug>/spec.md`, optionally with `plan.md` (ADR-008). The PO writes it with an LLM in the design phase. Merging the spec PR approves the design (ADR-011) and starts work on the Epic. Refinement breaks it down (ADR-009), so no `tasks.md` is committed.

Start from [`_template/spec.md`](_template/spec.md).

## Definition of Ready for Refinement

A spec PR is merged only when every item below holds. The aim is that refinement raises decision blockers only for real product questions, not for things the spec could have settled.

- [ ] **Goal and problem** state what changes for whom, and why now.
- [ ] **Scope** lists what is in and what is out.
- [ ] **Epic acceptance criteria** have ids (`E1`, `E2`, …). Each is an outcome the PO can verify at Epic closure, not an implementation step.
- [ ] **Decisions** record every product choice made in design, with the rejected alternatives.
- [ ] **Checked against** names the ADRs, `docs/*.md` and parent-issue decisions the design was checked against, and states any conflict found, resolved or intentionally superseded (a supersession comes with its own ADR PR).
- [ ] **Delegated to the tech lead** lists what refinement may decide on its own, such as slicing, technical approach and ordering. Everything not listed and not already decided is a product question.
- [ ] **Dependencies** on other Epics or external work are listed.
- [ ] **Open questions: none.** Every question is answered under Decisions or delegated.

The section headings and an empty Open Questions section are checkable by script. The rest is checked by the PO and the LLM in the design session before the PR is opened.

## Acceptance Criteria Levels

| Level | Written by | Content | Traces to |
|---|---|---|---|
| Epic (`E1`) | PO, in this spec | Outcome | — |
| Feature (`F1.1`) | Refinement | Capability | At least one Epic criterion |
| Story | Refinement, just-in-time | Testable behavior | At least one Feature criterion |

Every Epic criterion is covered by at least one Feature criterion. A criterion with no parent adds scope and is not allowed (ADR-009).

## Changing a Spec

Any scope change is a new spec PR. A decision-blocker answer that only clarifies, without changing scope, is recorded under the issue's `## Decisions` instead (ADR-009).
