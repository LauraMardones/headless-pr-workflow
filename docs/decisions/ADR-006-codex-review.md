# ADR-006: Codex Cloud Review as the Cross-Provider Reviewer

**Status:** Proposed
**Date:** 2026-09-27
**Related:** #263 (cross-provider review pairing), ADR-007

## Context

#263 requires that a PR implemented by one provider is reviewed by the other. Running a Codex review ourselves needs a runner, credentials and a dispatcher step. Codex Cloud offers automatic PR review on the PO's ChatGPT Plus plan; its usage does not count against the PO's chat allowance.

A trial on #285, #286 and #287 showed that it reviews PRs opened by Claude sessions, names the reviewed commit, follows `AGENTS.md` → Review Guidelines, and posts findings as inline review threads. All four findings were correct. A review cost about 1 point of the weekly limit. It never submits a formal approval.

How a clean result is reported depends on the trigger. An automatic review (on PR open or push) with no findings only adds a 👍 reaction to the PR description, which names no commit and stays in place across pushes. A review requested with an `@codex review` comment, including one posted by a Claude session, answers with a comment naming the reviewed commit (#287).

## Decision

For Claude-implemented PRs, the cross-provider review is Codex Cloud review, requested explicitly: when the PR is marked ready, and after each round of fixes for Codex findings, the implementing session comments `@codex review` on the PR. Codex automatic review is turned off for the repository, so each head is reviewed exactly once and every result names its commit. `AGENTS.md` → Review Guidelines is the rubric. No runner or dispatcher step is used for this review.

## Consequences

- Findings are inline review threads, so the existing unresolved-thread merge gate blocks on them without new logic.
- How a clean result satisfies the approval gate is decided in ADR-007.
- The dispatcher's paired `/review` invocation for Claude-implemented stories (`scripts/dispatcher-invoke.sh`, documented as canonical in `docs/ADAPTERS.md` → Cross-Provider Review Pairing) is retired in follow-up work; until then it would review the same head a second time. The paired Claude Opus review of Codex-implemented stories is unaffected.
- Review quality rests on one trial. Revisit if Codex misses a defect that reaches `main`, its findings are repeatedly wrong, its cost limits throughput, or OpenAI changes how it is triggered or how it reports results.
