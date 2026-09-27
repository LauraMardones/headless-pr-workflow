# ADR-006: Cloud Review Pipeline — Codex Automatic Review as the Cross-Provider Reviewer

**Status:** Proposed
**Date:** 2026-09-27
**Supersedes:** ADR-005 (Proposed in PR #278, never accepted) — Parts 2, 3 and 4
**Related:** ADR-001, ADR-002, ADR-003, #263 (cross-provider review pairing)
**Epic:** #160

---

## Context

ADR-005 proposed moving the dispatcher and both executors onto a PO-owned Raspberry Pi, running `claude` and `codex` under subscription login, with rolling 5-hour-window accounting and mid-task failover between providers. Its motivation was cost (metered API billing instead of existing subscriptions) and GitHub Actions heartbeat minutes.

Since then the PO has stated the constraints that drive this decision:

- The goal is a code factory in which the PO engages only as product owner, not as tech lead or operator.
- Idle time is acceptable; the factory does not need failover to keep moving.
- Subscriptions should be used fully, but must not starve the PO's everyday chat usage.
- Operations effort must be minimal, and the PO must be able to understand and maintain the solution.
- Review must be as deterministic as possible as a *process*, even though an AI reviewer's judgment cannot be.

Two facts changed the cost argument:

- The repository is public, so GitHub Actions minutes are free here; a less frequent schedule keeps private-repo usage small.
- The ChatGPT usage panel states that Codex plan limits are "shared across Codex, Work, Workspace Agents, and ChatGPT for Excel. Chat conversations are not included." Codex work does not consume the PO's chat allowance.

### Evidence (2026-09-27)

Codex Cloud automatic review was enabled for this repository with the trigger "On every push" and evaluated on two PRs:

| Question | Result |
|---|---|
| Starts without `@codex review`, on a PR opened by a Claude session | Yes — review posted about 2 minutes after the PR opened (#286) |
| Re-reviews each new head | Yes — review of the new head about 2 minutes after a push (#286) |
| Binds its review to the head commit | Yes — every result names the reviewed commit (`Reviewed commit: <sha prefix>`) |
| Follows the `AGENTS.md` Review Guidelines | Yes — findings gave file, line, consequence, a concrete reproduction, a P1 badge, and cited `AGENTS.md` |
| Findings land where HPW already gates | Yes — findings are inline review threads; an unresolved thread already blocks `hpw pre-merge` |
| Clean result | A PR conversation comment ("Didn't find any major issues", with the reviewed commit), not a formal review |
| Formal `APPROVE` | Never — Codex only comments |
| Judgment quality | Two findings on #286, both verified correct. The planted defect's real impact was smaller than the test's answer key assumed (`pre_merge` scans every raw check run), which Codex's second finding identified. One test PR is not a quality baseline. |
| Cost | About 3–4 points of the 5-hour limit and about 1 point of the weekly limit per review (ChatGPT Plus, read from the usage panel before and after) |

Setup finding: the Codex GitHub App had been installed for months, but Codex answered "create a Codex account and connect to github" until the GitHub connection was re-established from the Codex Cloud connector settings. Earlier Codex reviews in this repository were posted through the PO's own account by local sessions, not by Codex Cloud.

---

## Decision

### 1. Codex Cloud automatic review is the cross-provider review step

For PRs implemented by Claude, the review step required by #263 is Codex Cloud automatic review, triggered on every push. No dispatcher, runner, or owned host is needed for it. `AGENTS.md` → Review Guidelines is the reviewer's rubric.

### 2. HPW interprets review results deterministically

The AI's judgment is probabilistic; everything around it is not:

- **When:** every new head, by platform trigger.
- **What:** exactly that head; Codex names the reviewed commit.
- **Findings:** inline review threads. The existing unresolved-thread gate blocks merge until each is fixed or explicitly resolved. No text parsing is needed for findings.
- **Clean result:** recognized only by a script, fail-closed (see Part 5).

### 3. Findings that can become tests become tests

When an AI review finding describes a defect a deterministic test could catch, the fix includes that test. Over time this moves weight from probabilistic review to deterministic checks.

### 4. ADR-005 Parts 2, 3 and 4 are not adopted

No Raspberry Pi or other PO-owned host, no subscription-authenticated CLIs on such a host, no rolling-window accounting, and no mid-task failover between providers. When a provider's limit is hit, work pauses and resumes with the same provider.

ADR-005 Part 1 (native CLI invocation instead of the embedded API loop) and Part 5 (dependency-based auto-promotion out of Refined) are not decided here and remain candidates, to be re-refined against this ADR.

### 5. Solo maintenance is the default; the approval gate means independent review, not a second person

This repository, and the PO's future repositories, will normally have one human with write access. The approval gate therefore no longer expects a formal GitHub approval from someone other than the PR owner, and single-maintainer operation is no longer an exception.

The invariant the gate protects is unchanged: **a reviewer independent of the implementer found no blockers on the exact current head SHA.** The gate is satisfied by any one of these, checked by script and failing closed:

1. **Clean Codex review (default path):** a PR conversation comment by `chatgpt-codex-connector[bot]` stating that no major issues were found, whose reviewed-commit identifier is a prefix of the current head SHA, posted after that head was pushed, and not followed by a newer Codex review with findings. Anything that does not match this shape exactly does not count.
2. **SHA-bound separate-session review:** the review summary path that `docs/MERGE-POLICY.md` today calls the solo-maintainer override, kept as a fallback when Codex is unavailable, without its "no independent approver available" precondition and "exception" framing.
3. **Formal GitHub approval:** still accepted when present.

All other merge gates — required checks, no unresolved review threads, mergeability, non-draft, and a fresh head-SHA refresh immediately before merge — are unchanged. A Codex finding is an unresolved thread, so it blocks under the existing thread gate regardless of which approval path is used.

`docs/MERGE-POLICY.md`, `review_policy.py` and the pre-merge and merge-gate summaries are to be changed to match in a follow-up implementation, not in this ADR.

---

## Open Questions

1. **How implementation sessions are started.** Candidates are Claude Code cloud sessions started by a schedule or by the existing GitHub Actions dispatcher. Not evaluated yet.

---

## Consequences

- `AGENTS.md` → Review Guidelines (merged in #285) is load-bearing: it is the automated reviewer's rubric.
- PR #278 (ADR-005) should be closed unmerged, or reduced to Parts 1 and 5, once this ADR is accepted.
- These issues need re-scoping against this ADR before implementation: #277, #279, #280, #281, #282, #283, #259.
- The review-quality evidence is thin (one test PR). With Part 5, a clean Codex review alone can clear the approval gate, so deterministic tests on merge-critical logic carry more weight, not less.
- The solo-maintainer override section of `docs/MERGE-POLICY.md` is rewritten as the separate-session review path (Part 5.2).

---

## Conditions for Revisiting

1. Codex review misses a defect that later reaches `main`, or produces findings that are repeatedly wrong.
2. Codex review cost makes the weekly limit a bottleneck.
3. OpenAI changes the review trigger behavior or output format; the clean-result recognizer must then fail closed and be updated.
4. The factory starts working on private or client code, which changes the data-handling assumptions.
