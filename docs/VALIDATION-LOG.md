# Validation Log

This file records end-to-end validation runs of the `hpw` command suite against real PRs.

## 2026-05-19 — Smoke test round 1 and 2

**Scope:** Validated `workflow-status`, `next-action`, `worktree-status`, `approval-check`, `ci-summary` against merged PRs.

**Findings:**
- #77 `next-action` returned `escalate` for MERGED PR — fixed in PR #82.
- #78 `workflow-status` showed merge blocking reasons for MERGED PR — fixed in PR #83.
- #79 `approval status: missing` shown alongside accepted solo-maintainer override — fixed in PR #84.
- #80 74 untracked files from `.claude/worktrees/` reported as noise — fixed in PR #85.
- #81 `next-action` returned `escalate` for CLOSED (abandoned) PR — fixed in PR #86.

**Round 2 result:** All fixes verified. No new findings on merged PRs.

**Next:** Active flow validation against an open PR.
