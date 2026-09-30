# ADR-011: Decision Documents Are Approved by the PO's Merge

**Status:** Accepted
**Date:** 2026-09-28
**Related:** ADR-007, ADR-008, `docs/MERGE-POLICY.md`, `docs/ROLES.md`
**Effective when:** `docs/MERGE-POLICY.md` and the merge-gate recognizer implement the rule below. Until then, decision-document PRs use the existing solo-maintainer path.

## Context

There is no documented way to approve an ADR. ADR-006 and ADR-007 were merged with `Status: Proposed`, and #160 warns that an executor may read that as undecided. ADR-008 adds specs, which are product decisions of the same kind.

ADR-007's approval gate requires an independent reviewer for code. That fits decision documents badly. The decision is the PO's to make, not a reviewer's. GitHub also does not let the PO approve a PR opened under the PO's own account, which is how spec sessions open spec PRs.

## Decision

1. **Scope.** A decision-document PR changes only Epic specs (`specs/<epic-number>-<slug>/`) or ADRs (`docs/decisions/ADR-*.md`). The READMEs and the spec template in those folders are working rules, not decision documents.
2. **Approval.** For such a PR, the PO's merge is the approval. An independent review under ADR-007 is not required. A reviewer such as Codex may still comment, and its findings are input for the PO, not a gate.
3. **Status on main.** An ADR is merged with `Status: Accepted`. An ADR that is not accepted is not merged. `Proposed` exists only on unmerged branches.
4. **Other gates still apply.** Required checks, resolved blocking threads and a fresh refresh before merge are unchanged.

## Consequences

- `docs/MERGE-POLICY.md` and the merge-gate recognizer gain the decision-document path.
- ADR-006 and ADR-007 change to `Accepted` in #291, as already planned.
- A PR that mixes decision documents with code or working rules is not a decision-document PR and follows the normal gates.
- Revisit if a decision-document PR is merged by anyone other than the PO.
