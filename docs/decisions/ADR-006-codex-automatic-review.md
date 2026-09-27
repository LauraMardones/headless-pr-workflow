# ADR-006: Codex Cloud Automatic Review as the Cross-Provider Reviewer

**Status:** Proposed
**Date:** 2026-09-27
**Related:** #263 (cross-provider review pairing), ADR-007

## Context

#263 requires that a PR implemented by one provider is reviewed by the other. Running a Codex review ourselves needs a runner, credentials and a dispatcher step. Codex Cloud offers automatic PR review on the PO's ChatGPT Plus plan; its usage does not count against the PO's chat allowance.

A trial on #285 and #286 showed that it starts on PRs opened by Claude sessions, re-reviews every push, names the reviewed commit, follows `AGENTS.md` → Review Guidelines, and posts findings as inline review threads. Both findings on #286 were correct. A review cost about 1 point of the weekly limit. It never submits a formal approval; a clean result is a PR conversation comment.

## Decision

For Claude-implemented PRs, the cross-provider review is Codex Cloud automatic review with the trigger "On every push". `AGENTS.md` → Review Guidelines is its rubric. No runner or dispatcher step is used for this review.

## Consequences

- Findings are inline review threads, so the existing unresolved-thread merge gate blocks on them without new logic.
- How a clean result satisfies the approval gate is decided in ADR-007.
- Review quality rests on one trial. Revisit if Codex misses a defect that reaches `main`, its findings are repeatedly wrong, its cost limits throughput, or OpenAI changes its trigger or output format.
