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

### <Decision title>

**Chosen:** <…>
**Rejected:** <alternative> — <why>

## Checked Against

- <ADR, `docs/*.md` section, or parent-issue decision> — <no conflict | conflict and how it is resolved>

## Delegated to the Tech Lead

- <Choices refinement may make on its own, e.g. slicing, technical approach, ordering.>

## Dependencies

- <Other Epics or external work this depends on, or "None".>

## Open Questions

None.
