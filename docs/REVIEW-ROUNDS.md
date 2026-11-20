# Review and Fix Rounds per PR

`hpw review-rounds` reports how many review and fix rounds each merged PR needed, so the effect of process changes on review churn can be measured (Epic #418, Feature #487). It reads GitHub data only. It writes nothing to GitHub and posts nothing.

## Run

```text
hpw review-rounds --repo <owner/repo> (--last N | --prs <numbers-or-range>) [--flow orchestration-or-critical-path] [--json]
```

- Exactly one of `--last N` (the N most recently merged PRs) and `--prs` (comma-separated numbers or ranges, for example `309-355` or `309,313`) is required; otherwise the command exits 2.
- A number in `--prs` that is not a merged PR is listed under `Skipped:`; the exit code stays 0.
- `--flow orchestration-or-critical-path` keeps the PRs whose closing issue is `type:story`, `type:task` or `type:bug` and carries `flow:orchestration` or `cp:critical-path`. Counting is identical to an unfiltered run. The report names the labels it read. The closing issue is the first issue the PR body closes (`Closes #n`); labels are read at run time, so a label added later changes the filtered set.
- A GitHub read failure exits non-zero and prints no report.

## Figures

| Figure | Definition |
|---|---|
| review requests | PR conversation comments whose first line is the Codex review trigger phrase (`CODEX_REVIEW_TRIGGER_PHRASE`, ADR-006), whoever posts them |
| Codex reviews | reviews by `CODEX_BOT_LOGIN` |
| P1 findings | top-level Codex review comments with the P1 or P0 priority badge; replies and human comments do not count |
| fix commits | commits on the final PR head made after the first Codex review (commits reachable from the head; a force-push that dropped commits is not seen) |
| fix rounds | see below |
| size | changed files, additions, deletions |
| time to merge | seconds from PR creation to merge |

Size and time to merge are reported only and take part in no check.

### Fix round

A fix round is one Codex review with at least one P1 or P0 finding that is followed by at least one new commit on the PR head before the merge. Several commits between two reviews make one round, and two P1 reviews with no commit between them also make one round (the round is the batch of commits that answers them). A review request with no new commit before it is no fix round, and a P1 finding on an older head still counts as a finding. A round is not counted by commit count or as review requests minus one.

## Read the report

The table has one row per counted PR, then a `total` and a `median` row over the counted set. `Counted PRs:` lists the PRs in the set and `Skipped:` the requested numbers that were not merged PRs. `--json` prints the same data: `repo`, `selection`, `filter`, `labels_read`, `prs`, `skipped` and `summary`.

Use the same selection and the same definition for a baseline and a later run, so the figures compare.

## Baseline run: #309–#355

`hpw review-rounds --repo LauraMardones/headless-pr-workflow --prs 309-355`, run 2026-11-20, counts 21 merged PRs (the other numbers are issues or unmerged PRs):

| Figure | Total | Median |
|---|---|---|
| review requests | 64 | 2 |
| Codex reviews | 51 | 1 |
| P1 findings | 79 | 1 |
| fix commits | 59 | 1 |
| fix rounds | 46 | 1 |

Labelled subset (`--flow orchestration-or-critical-path`): 15 PRs, #309, #321, #323, #325, #327, #328, #333–#336, #338, #340, #342, #347, #353; 56 P1 findings and 34 fix rounds.

The hand-made analysis of 2026-10-15 said "about 67" P1 findings over 10 PRs. The recorded data gives 79 over 11 PRs (#313, #327, #328, #334, #336, #338, #341, #342, #353, #354, #355). #353 (13 findings) was merged on 2026-10-15 at 14:13, after the analysis; without it the report gives 66 findings over 10 PRs and the six heaviest are #342 (13), #355 (12), #338 (11), #328 (9), #341 (7) and #327 (6), the set the analysis named. The analysis gave #355 as 13; the data has 12 top-level P1 comments on seven Codex reviews of #355. The report is the baseline from now on.
