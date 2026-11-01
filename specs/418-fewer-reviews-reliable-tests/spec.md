# Epic #418: Fewer review rounds and reliable tests

**Epic issue:** #418
**Milestone:** M6 Autonomous Execution

## Goal

A story costs less to deliver. Today the PO's subscription allowance and time go to work that ships nothing:

- review and fix rounds that find the same kinds of failure one at a time (about 67 Codex P1 findings in 10 of the 20 PRs #309–#355, analysis of 2026-10-15);
- implementation sessions that end at the turn limit (#315: about 90 % of its cost);
- a test result that cannot be trusted: local Windows runs fail for reasons unrelated to the change, and the shell tests in `tests/*.sh` never run in CI, so a broken test merged green after #390.

For whom: the PO, whose allowance is the binding resource. Why now: Epic #160 closes on its proof, and this work left it on 2026-11-01. It is not pulled until it has an Epic.

## Scope

In:

- A repeatable measure of review and fix rounds per PR, from GitHub data (#369).
- A rule that gives orchestration and critical-path stories a stronger implementer, with its cost trade-off recorded (#367).
- Shared, tested dispatcher building blocks for verified GitHub writes, markers and saved state, and an ADR on where dispatcher logic lives (#368).
- The cause of implementation sessions that end at the turn limit, and a fix for the main cause (#401).
- A trustworthy test suite: the local Windows run is green, and every `tests/*.sh` file runs in CI under a required check (#415 with #384, #398, #412).

Out:

- WSJF ordering and allowance cost per issue (#400, #399): Epic #306.
- The refinement and `/implement` contract changes from the same analysis (#364, #365, #366, #416): Epic #272.
- Changing the reviewer or the review policy.
- New dispatcher behaviour.
- Changing the session guards (reserve, allowance probe, sessions per day).
- WSL support, and changing `-n auto` in CI or `-n 4` locally.
- Dashboards or scheduled reports of the measure.

## Acceptance Criteria

- **E1:** The PO can see, for the last N merged PRs, how many review requests, Codex reviews, P1 findings and fix commits each PR needed, without manual work.
  - **Verified by:** the report for PRs #309–#355 matches the manual analysis of 2026-10-15.
- **E2:** Orchestration and critical-path stories need fewer review and fix rounds: for the first 5 such stories delivered after #367 and #368, the median number of fix rounds per PR is at most half the median for the same kind of stories in PRs #309–#355.
  - **Verified by:** the E1 report on those 5 PRs, compared with the E1 report on the baseline PRs.
- **E3:** Fewer implementation sessions end at the turn limit: in the 4 weeks after the fix for the main cause is merged, no story has more than one implementation session that ends with `error_max_turns`.
  - **Verified by:** the `[SESSION]` lines in the dispatcher runs over those 4 weeks.
- **E4:** On the PO's Windows machine, `python -m pytest` and `python -m pytest -n 4` finish with no failures and no setup errors. Each test that is skipped on Windows states why. Two runs started at the same time never delete each other's temp files.
  - **Verified by:** the PO runs both commands at closure, and the regression test from #384 passes.
- **E5:** Every test file, including each `tests/*.sh`, runs in CI on every PR under a check that blocks merge. A new `tests/*.sh` file runs with no workflow change.
  - **Verified by:** a deliberately failing shell test turns the required check red.

## Decisions

### One Epic for all five issues — 2026-11-01 (PO)

**Chosen:** One Epic for #368, #367, #369, #415 and #401, with the shared goal "a story costs less to deliver".
**Rejected:** Two Epics, one for review rounds and one for tests — two spec sessions and two closures for a small amount of work.
**Rejected:** One Epic without #401 — the turn-limit waste is the biggest single cost seen.

### #401 covers the cause and the fix — 2026-11-01 (PO)

**Chosen:** The Epic closes only when the main cause of sessions ending at the turn limit is fixed.
**Rejected:** The cause only — an investigation alone saves no allowance.

### E2's bar is half the baseline — 2026-11-01 (PO)

**Chosen:** The median number of fix rounds per PR for the first 5 orchestration and critical-path stories after #367 and #368 is at most half the baseline median.
**Rejected:** A fixed cap of 2 fix rounds per PR.
**Rejected:** No bar, only before and after figures.

### E3's bar is at most one turn-limit session per story over 4 weeks — 2026-11-01 (PO)

**Chosen:** In the 4 weeks after the fix is merged, no story has more than one implementation session that ends with `error_max_turns`.
**Rejected:** Fewer than 10 % of implementation sessions end at the turn limit.
**Rejected:** "The main cause no longer occurs", with no figure.

## Checked Against

- #160 → Decisions → "Work that no Epic criterion needs gets its own Epic" (2026-11-01) — no conflict. This spec carries out its Option A.
- #415 → Decisions → "Parent Epic" (2026-11-01, chose #160) — replaced by the PO's later decision in #160 on the same day. #415's F1–F3 now serve E4, and F4–F5 serve E5. Refinement re-traces them.
- ADR-008, ADR-010, ADR-011 — no conflict. The spec is in the repo, no board status is set, and the PO merges the spec PR.
- ADR-009 — the existing criteria in #367, #368, #369, #401 and #415 were written before this spec. Refinement traces each one to E1–E5 or drops it.
- ADR-006, ADR-007 — no conflict. The reviewer and the approval gate stay the same (Scope → Out).
- ADR-012 and `docs/ADAPTERS.md` — a stronger implementer (#367) uses more of the subscription allowance; #367 records that trade-off. The dispatcher already supports `executor:claude-code-opus`. Adding a new tier, such as Fable, would be a new decision.
- `docs/MERGE-POLICY.md` and `docs/required-check-policy.json` — `pytest` is the only required check on `main`. If E5 changes the required checks, the same story updates both files.
- AGENTS.md → Running Tests — no conflict. E4 removes the list of known Windows failures.
- Epic #272 — holds #364, #365, #366 and #416 from the same analysis. No conflict. The E2 measure shows the combined effect of both Epics.
- Epic #306 — holds #399 and #400. Out of scope here.

## Delegated to the Tech Lead

- How to split the work into Features and Stories, and whether #367, #369 and #401 get a Feature.
- The executor rule in #367: which existing tier, and for which labels.
- The technical approach in #368, including the ADR on bash or Python.
- How the shell tests run in CI (#412).
- The exact definition of a "fix round" in the E1 report, as long as the baseline and the later measure use the same definition.
- Which fix #401 chooses for the main cause, within Scope → Out (the guards stay the same).
- The order of the work, within Dependencies.

## Dependencies

- Hard: #368 starts only after the Lane 1 work in Epic #160 (#339, #409, #406) is merged, because all of them change `scripts/dispatcher-invoke.sh`.
- Nothing else. #384's PR #388 is already in merge.

## Open Questions

None.
