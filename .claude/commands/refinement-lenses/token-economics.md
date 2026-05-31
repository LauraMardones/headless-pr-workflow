# Lens: Token Economics

## Trigger

Default lens — always applied, regardless of labels.

## Perspective

Every refinement session has a fixed overhead cost (context loading, planning, GitHub reads/writes). This overhead is paid per story, per session. Unnecessarily granular breakdowns multiply this cost without delivering additional value. This lens optimises for token efficiency: merge where possible, assign the cheapest model tier that can do the job correctly, and eliminate documentation clusters.

## Questions to answer before breakdown

Answer each question before creating any issues. If you cannot answer, add it to Open Questions.

1. **Documentation cluster check**: Do any proposed stories all write to the same file(s) with no hard dependency between them? If yes, merge them into one story. One story = one session = one overhead payment.

2. **Model tier check**: For each proposed story, assign the correct tier:
   - `executor:claude-code-haiku` — documentation, config files, boilerplate, CRUD, markdown writing, simple cross-references
   - `executor:claude-code-sonnet` — business logic, integration, cross-cutting concerns, new abstractions, command implementations
   - `executor:claude-code-opus` — architecture decisions, security review, complex algorithm design, multi-system coordination
   - `executor:codex` — code review, CI/CD pipeline, algorithmically complex tasks
   - Ask: "Is every story assigned to the cheapest tier that can do the work correctly?"

3. **Per-session overhead estimate**: Count the total number of stories. Multiply by the average overhead per session (~15% of a typical session's tokens for context loading alone). Is this overhead proportionate to the value delivered? If total story count > 6, apply the documentation cluster check again more aggressively.

4. **Sequential coupling check**: Are there stories that must run sequentially and write to the same file? If so, they are forced to be separate. If not, they are merge candidates regardless of content similarity.

5. **Parallelism value check**: Would splitting a story into two actually enable parallel execution (different executors, no file overlap)? If parallel execution is not realistic (same files, same executor), the split buys nothing.

## Red flags

Raise these as Open Questions rather than proceeding silently:

- More than 3 stories writing to `docs/PROJECT-STATUS.md` — almost always a documentation cluster.
- Any documentation-only story assigned `executor:claude-code-sonnet` or higher — likely over-specified.
- Story count > 8 for a feature that is purely additive documentation — strong signal of over-granularity.
- Two stories with identical `## Files affected` sections and no hard dependency between them — must merge.

## Notes

- This lens should be applied after the Existing Issues Inventory and before the Breakdown step.
- Model tier assignments must appear as `executor:` labels on every story at `status:ready-for-implementation`.
- OSS invariant: executor routing is data-driven. Model tier assignments live in GitHub labels, not in logic branches.
