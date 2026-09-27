# ADR-007: Approval Gate Means Independent Review; Solo Maintenance Is the Default

**Status:** Proposed
**Date:** 2026-09-27
**Related:** ADR-006, `docs/MERGE-POLICY.md`

## Context

`docs/MERGE-POLICY.md` expects a formal GitHub approval from someone other than the PR owner, and treats single-maintainer repositories as an exception with a "solo-maintainer override". The PO will normally be the only human with write access, so the exception is the normal case and the expectation of a second approver is dead weight. Codex (ADR-006) never submits a formal approval.

## Decision

The approval gate is satisfied when **a reviewer independent of the implementer found no blockers on the exact current head SHA**. Any one of the following counts, checked by script and failing closed:

1. **Clean Codex review (default for Claude-implemented PRs):** a PR comment by `chatgpt-codex-connector[bot]` stating no major issues were found, whose reviewed-commit identifier is a prefix of the current head SHA and of no other commit on the PR, posted after the current head became the PR head, and not followed by a newer Codex review with findings. It never counts for a PR implemented by Codex (story labelled `executor:codex`); those follow the pairing in `docs/ADAPTERS.md` → Cross-Provider Review Pairing and need path 2 or 3.
2. **SHA-bound separate-session review:** today's solo-maintainer override evidence, without its "no independent approver available" precondition.
3. **Formal GitHub approval**, when present.

Single-maintainer operation is no longer an exception. All other merge gates are unchanged.

## Consequences

- A clean Codex review alone can clear the approval gate, so deterministic tests on merge-critical logic matter more.
- `docs/MERGE-POLICY.md`, `review_policy.py` and the pre-merge and merge-gate summaries change in a follow-up implementation.
