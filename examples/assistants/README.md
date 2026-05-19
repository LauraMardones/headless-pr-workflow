# Assistant Adapter Examples

Assistant adapters are optional and non-normative.

Future examples should provide:

- Implementation-only prompt.
- Review-only prompt.
- Takeover prompt.
- Pre-merge prompt.
- Tool-specific command recipe.

The core workflow must remain valid without these examples.

## Generic Adapters

- `generic/IMPLEMENT-PR.md`: assistant-agnostic implementation prompt for GitHub PRs.
- `generic/REVIEW-PR.md`: assistant-agnostic review prompt for GitHub PRs.
- `generic/TAKEOVER-PR.md`: assistant-agnostic takeover prompt for post-review, post-conflict, and merge-owner handoff sessions.

## Codex Adapters

Role-specific adapter prompts for operators running Codex sessions in the headless PR workflow. Each adapter is a pre-built, policy-aligned session-start prompt for a specific workflow role. All four adapters include `gh` CLI fallback instructions for Codex environments where `hpw` is not available.

- `codex/IMPLEMENT-PR.md`: Codex session prompt for implementing changes on a PR branch. Explicitly forbids reviewing or approving the head SHA produced in the same session.
- `codex/REVIEW-PR.md`: Codex session prompt for reviewing a PR head SHA without implementing fixes. Explicitly forbids implementing changes or merging.
- `codex/FIX-REVIEW.md`: Codex session prompt for a reimplementation session triggered by review blockers (phase G). Explicitly forbids reviewing or approving the new head SHA produced, and explicitly invalidates any prior approval after new commits are pushed.
- `codex/MERGE-OWNER.md`: Codex session prompt for the merge-owner role. Requires a fresh GitHub refresh and `hpw pre-merge` (or equivalent `gh` fallback) immediately before merging. Explicitly forbids implementing new changes or reviewing in the same session.
