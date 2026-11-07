Act as the technical verifier for Feature and Epic closure in
`LauraMardones/headless-pr-workflow`.

Verify or continue closure for issue #$ARGUMENTS. The first invocation performs
the technical-evidence phase and posts at most one authoritative summary. A
later invocation may close that same Feature or Epic only after the exact PO
confirmation and fresh-state gates below pass. The command never makes the
product judgment, confirms on behalf of the PO, reopens, relabels, or changes
the Project status of an issue.

## GitHub operation fallback

- Prefer the GitHub plugin/MCP integration for GitHub reads and mutations when
  it is available.
- Otherwise use the authenticated `gh` CLI. Use direct API requests only when
  neither supports the required operation.
- Never expose, print, log, persist, or commit credentials.

## 1. Validate the invocation without mutation

- Treat the expanded argument text as whitespace-separated tokens.
- Require exactly one token matching the positive-integer pattern
  `[1-9][0-9]*`.
- Missing, extra, zero, negative, flag-like, or non-numeric input is invalid.
- On invalid input, stop before any GitHub comment and report the validation
  error in the session response.

After validation, fetch the target and all comments from GitHub. If at least
one comment contains `<!-- verify-closure:issue=$ARGUMENTS;main=` then do not
repeat technical verification: follow **Continuation after PO confirmation**
below. Otherwise follow the technical-evidence phase in sections 2–9.

## 2. Verify identity, target, and evidence baseline

Complete every check before reading local repository contents as evidence:

1. Fetch repository metadata from GitHub and require the exact repository
   identity `LauraMardones/headless-pr-workflow`.
2. Fetch issue `#$ARGUMENTS` fresh. Require it to exist and be open.
3. Inspect its current labels. Require exactly one supported label:
   `type:feature` or `type:epic`. Zero or both supported labels is a blocker.
4. Fetch the full current remote `main` commit SHA from GitHub.
5. Run `git rev-parse HEAD` and require the full local SHA to equal the full
   remote `main` SHA. If local Git evidence cannot be read, or the SHAs differ,
   stop. Do not use local files and do not mark any criterion PASS.

Refresh the target issue and remote `main` SHA after all verification work and
immediately before the permitted comment mutation. If the target closed, its
supported type changed, or `main` changed, discard the result and stop with a
stale-state blocker.

## 3. Extract the verification contract

- A Feature: read the target's `## Goal` and every item under
  `## Success Criteria` or `## Acceptance Criteria` from the fresh GitHub body.
- An Epic: look for `specs/<n>-*/spec.md` (`<n>` = the Epic number) in the
  checkout. Section 2 pinned it to the verified `main` SHA, so read it there,
  not from GitHub (`read_epic_spec` in the model).
  - Exactly one match: the Epic has a merged spec. The goal is the spec's
    `## Goal`. The criteria are the list items under the spec's
    `## Acceptance Criteria` that start with `**E<n>:**`, each with its nested
    **Verified by:** line. The Epic issue body is not read for criteria.
  - No match: read the goal and criteria from the Epic body as for a Feature,
    and record `Spec: none found — criteria taken from the issue body` in the
    summary (or in the blocker report, when verification is blocked).
    `PO CHECK` is not allowed on this path.
  - More than one match, a spec that cannot be read, a spec with no `## Goal`,
    or a spec with no `**E<n>:**` criterion is a blocker; post no comment.
- Preserve criterion order and wording in the evidence matrix; an Epic row
  names the spec's id and wording (`E1: ...`).
- Missing goal or missing criteria is a blocker. Verification must not invent,
  rewrite, or silently omit criteria.

## 4. Build a complete child and pull-request inventory

Use fresh GitHub data, not assistant memory or local notes.

1. Query GitHub-native sub-issue relationships for the target.
2. Search open and closed issues whose body explicitly declares the target in
   `Parent feature:`, `Parent epic:`, or `Feature group:` metadata, as
   appropriate for the target type.
3. Combine and deduplicate by issue number. If the native relationship and
   explicit metadata conflict, or a child's parent is ambiguous, stop with a
   blocker rather than choosing one source silently.
4. For every child, record number, title, type labels, and open/closed state.
5. Discover merged PRs that close, implement, or are explicitly linked from
   each child. Record PR number, merge commit, merged date, and the child link.
   An unmerged or merely mentioned PR is not delivered evidence.

### Feature branch

- Include all open and closed direct children, regardless of child type.
- Include the merged-PR inventory for each child.
- An open child is visible in the inventory. It blocks PASS when its unfinished
  work is required to establish any Feature criterion.
- Map every Feature criterion to concrete delivered evidence.

### Epic branch

- Include every direct child Feature from both discovery surfaces.
- Require each child to have exactly the `type:feature` label and to be closed.
  Any open child Feature blocks a ready-for-PO result.
- Use each closed Feature's GitHub delivery evidence and merged PR inventory to
  map delivery to the Epic goal and every Epic criterion.
- Do not treat a closed label or narrative summary by itself as proof that an
  Epic criterion passed.

## 5. Construct the criterion evidence matrix

Create exactly one row per declared criterion, in order. For an Epic with a
merged spec the rows must match the spec's criteria exactly. A row may be `PASS` only when it
cites one or more concrete, reproducible evidence items:

- repository file paths with line ranges at the verified SHA;
- exact test commands with recorded passing outcomes;
- merged PR or issue URLs/numbers tied to delivered work; or
- durable runtime evidence with its source and observation date.

A criterion whose **Verified by:** line contains "the PO" (whole words, any
case) may get the result `PO CHECK` instead of `PASS`, only for an Epic with a
merged spec:

- Prove and cite in the row every part the verifier can prove (evidence
  comments, test runs, coverage output). A `PO CHECK` row with no evidence is a
  blocker.
- Write the PO's steps for the row (see `### PO hands-on checks` in section 9).
  A `PO CHECK` row without steps is a blocker.
- A `PO CHECK` row on a criterion whose **Verified by:** line does not contain
  "the PO", or on any path other than an Epic with a merged spec, is a blocker.

Narrative confidence, issue state alone, a planned file, an unmerged PR, stale
local content, or an environment-limited check is not passing evidence. Mark an
unproven or contradicted row `FAIL` or `BLOCKED` and explain what evidence is
missing.

## 6. Run and record checks

Read CI first. Before you choose or run any criterion-specific local check, read
the CI `pytest` check run for the exact full verified `main` SHA. Do not run the
full suite (`python -m pytest`) locally. CI runs it, including every
`tests/test_*.sh` file, on every push to `main`. Read the check run (for example
`gh api repos/LauraMardones/headless-pr-workflow/commits/<FULL_MAIN_SHA>/check-runs`
or `gh run list --workflow tests.yml --commit <FULL_MAIN_SHA>`), match by full
SHA only, never by branch or "latest run on main", and never wait, poll, or
start a run. Classify it with `ci_check_result` in
`src/headless_pr_workflow/closure_verification.py`. Pass each run's workflow run id
and attempt number; the newest run (highest run id) counts, then its newest attempt:

| Situation | Result |
|---|---|
| Newest run succeeded | `PASS` |
| No run for the SHA (or only runs for another SHA) | `BLOCKED`, name the SHA and "no CI run" |
| Queued or in progress | `BLOCKED`, name the run URL, "run again when CI finishes" |
| Failed or timed out | `FAIL`, name the run URL |
| Cancelled or skipped | `BLOCKED`, name the run URL |
| CI data cannot be read | `BLOCKED`, name the failing read |

Record the CI row as ``CI pytest on <full SHA>`` with its run URL.

Then run only these local checks from the verified checkout:

1. `python -m pytest tests/test_verify_closure_command.py`. If the focused test
   file has been renamed, identify its actual focused command from the verified
   repository and run that exact command instead.
2. For an Epic with a merged spec, `scripts/criteria-check --epic $ARGUMENTS --repo LauraMardones/headless-pr-workflow --ref <FULL_MAIN_SHA>`
   as a required check. Exit `0` is `PASS`, exit `1` is `FAIL`, exit `2` is
   `BLOCKED` (`criteria_check_result` in the model). A missing run is a blocker.
   Keep its text output for the `### Coverage check` section. A Feature, or an
   Epic with no merged spec, does not run it.
3. Only the criterion checks that CI cannot give, for example a criterion that
   names Windows, or a test that CI skips on Linux. Record each one in
   `### Checks` with the reason CI cannot give it.

A test file that a criterion names, and that the CI `pytest` run executes, is
not run locally, not even as a short extra check. Cite it from that run in the
criterion row as "passed in the CI `pytest` run on the verified SHA (Checks)".
That citation is valid only when the CI row in `### Checks` is `PASS`; otherwise
the row is not `PASS`.

Record every exact command and classify its result as `PASS`, `FAIL`, or
`BLOCKED`. A non-zero test result is `FAIL`. A command that cannot run because
of missing tools, credentials, network access, permissions, or other
environment limits is `BLOCKED`, never `PASS`.

## 7. Decide without weakening the gate

The deterministic safety model for these gates lives in
`src/headless_pr_workflow/closure_verification.py`. Use its validation semantics
when normalizing the freshly collected GitHub, checkout, evidence-row, check,
and existing-comment state. Do not bypass a modeled blocker based on narrative
judgment.

Verification passes only when all of the following are true:

- preflight and final freshness checks pass;
- child and PR inventory is complete and unambiguous;
- the Epic branch has no open child Feature;
- every declared criterion row is `PASS`, or `PO CHECK` where the rules of
  section 5 allow it; and
- the CI `pytest` run for the verified SHA and every required focused and criterion-specific check is `PASS`.

If any condition fails or is blocked:

- do not post the authoritative technical delivery summary;
- do not request PO product confirmation;
- do not mutate repository or issue lifecycle state; and
- return a human-readable blocker summary listing each failed or unproven
  criterion/check and the evidence needed to proceed.

## 8. Enforce SHA-bound idempotency

Use this literal idempotency marker in the successful comment:

`<!-- verify-closure:issue=$ARGUMENTS;main=<FULL_MAIN_SHA> -->`

Before posting, fetch all current issue comments and search for an exact marker
for the target number and full verified SHA.

- If one exists, return its immutable comment URL and stop without posting.
- If more than one exists, stop with a conflicting-authoritative-evidence
  blocker and do not add another.
- A marker for an older SHA does not satisfy the current run. Fully reverify the
  current SHA before posting one new SHA-bound summary.

## 9. Post the single successful summary, then stop

Only after every gate passes and the final freshness refresh still matches,
post exactly one issue comment in this structure:

```md
## Technical Closure Verification
<!-- verify-closure:issue=<ISSUE_NUMBER>;main=<FULL_MAIN_SHA> -->

- Repository: `LauraMardones/headless-pr-workflow`
- Target: #<number> — <title>
- Type: `type:feature` or `type:epic`
- Verified `main`: `<full SHA>`
- Spec: <path> @ <FULL_MAIN_SHA> (Epic with a merged spec), or
  Spec: none found — criteria taken from the issue body (Epic with no merged spec)

### Child and merged PR inventory
| Child | Type | State | Merged PR evidence |
|---|---|---|---|
| ... | ... | ... | ... |

### Criterion evidence matrix
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | PASS | ... |
| 2 | ... | PASS | `tests/test_example.py` passed in the CI `pytest` run on the verified SHA (Checks) |

For an Epic with a merged spec, `#` is the spec's id (`E1`) and Result is
`PASS` or `PO CHECK`.

### Coverage check
(Epic with a merged spec only)
```
<text output of scripts/criteria-check>
```

### Checks
| Check | Result |
|---|---|
| `python -m pytest tests/test_verify_closure_command.py` | PASS |
| `scripts/criteria-check --epic <n> --repo LauraMardones/headless-pr-workflow --ref <FULL_MAIN_SHA>` (Epic with a merged spec only) | PASS |
| CI `pytest` on `<full SHA>` — `<run URL>` | PASS |

### PO hands-on checks
(only when a row is `PO CHECK`; one numbered block per `PO CHECK` row)
1. `E<n>`
   1. What to do and where.
   2. ...
   - Post: the exact text to post, or "nothing".
   - Expected: the expected result.
   - Pass if / fail if: how the PO tells pass from fail.

### Blockers
none

### Residual risk
<risk, or "none">

### Next action
Reply with exactly "yes" to confirm and close, or "no" to send it back.
```

When a row is `PO CHECK`, the next action reads instead: "Do the PO checks
below; reply `yes` only if every check gave the expected result, otherwise
`no`."

After posting, return only a concise human-readable result containing the issue
URL, full verified SHA, `PASS`, the new comment URL, and the exact reply
required ("yes" to confirm and close, "no" to send it back). Produce no JSON.
Stop without detecting confirmation or performing a close or lifecycle-state
mutation.

## Continuation after PO confirmation

The continuation consumes the latest authoritative successful technical
summary; it must never manufacture or reinterpret one. Use the deterministic
safety model in `continue_closure` from
`src/headless_pr_workflow/closure_verification.py`. Perform the following in
order, using fresh GitHub responses rather than chat context or cached objects.

### A. Locate and validate the authoritative summary

1. Fetch repository metadata and require the exact identity
   `LauraMardones/headless-pr-workflow`.
2. Fetch the target, its full comments, labels, and lifecycle state.
3. Require exactly one of `type:feature` or `type:epic`.
4. Select the newest successful `## Technical Closure Verification` comment
   with the target's exact `verify-closure` marker. Reject an unreadable marker,
   wrong target/type, missing full SHA, or conflicting newest summaries.
5. Fetch the full current remote `main` SHA. It must exactly equal the summary's
   verified SHA. If it differs, stop with: rerun technical verification. Never
   carry an earlier PO confirmation onto a replacement summary.

### B. Require exact, fresh PO confirmation or decline

The only authorized PO is `LauraMardones`. A qualifying reply is exactly one
unedited comment, created strictly after the selected technical summary, whose
trimmed body case-insensitively equals the whole word below (no target number
or sentence needed — the fresh, on-issue, unedited scoping already binds the
reply to this target):

- `yes` confirms closure. Continue to section C.
- `no` declines closure. Report a `DECLINED` outcome (target, technical
  summary URL, the declining comment's author/timestamp/URL) and stop without
  mutating anything or requiring further gates.

Missing or multiple matches (of either word, or one of each), another author,
an edited comment, a comment at or before the summary, `yes`/`no` embedded in a
longer sentence, reactions, issue-body text, and out-of-band messages satisfy
neither match and block with the generic missing-confirmation blocker. Report
the exact required replies ("yes" to confirm, "no" to decline) as the next
action; do not mutate anything.

### C. Refresh every mutation-sensitive gate

Immediately before any close request, refetch and revalidate all of the
following together:

- repository identity and target number;
- target lifecycle state and exactly one supported type label;
- latest authoritative summary, its immutable URL, target/type, and SHA;
- exact PO confirmation, author, creation time, edit state, and immutable URL;
- full current remote `main` SHA; and
- for an Epic, the complete fresh direct-child inventory from native and
  metadata relationships, requiring every child Feature to be closed and to
  have exactly `type:feature`.

Any API/read failure, mismatch, ambiguity, changed SHA, changed label/state,
open or invalid Epic child, stale summary, or changed confirmation blocks the
close. List the blocker and exact next action. Do not silently rerun phase one.

### D. Enforce close idempotency and partial-failure repair

Use this literal closing-evidence marker:

`<!-- verify-closure-close:issue=$ARGUMENTS;main=<FULL_MAIN_SHA> -->`

Search all comments for the exact marker before mutation:

- Open target with no closing marker: close only this target, then post the
  formal evidence in section E.
- Closed target with exactly one marker: return its URL and `NOOP`; post and
  mutate nothing.
- Closed target with no marker: treat this only as the repair state for a prior
  close-success/comment-failure result. Revalidate sections A–C, post the one
  missing formal evidence comment, and do not call close again.
- More than one exact closing marker: stop with a conflicting-authoritative-
  evidence blocker.

If the close API fails, report failure and do not post success evidence. If the
close succeeds but comment creation fails, report the partial failure and tell
the operator to rerun the same command; that retry must take only the repair
path above.

### E. Post exactly one formal closing-evidence comment

Only after GitHub confirms the close succeeded, or during the validated repair
path, post:

```md
## Closure Evidence
<!-- verify-closure-close:issue=<ISSUE_NUMBER>;main=<FULL_MAIN_SHA> -->

- Repository: `LauraMardones/headless-pr-workflow`
- Target: #<number> — <title>
- Type: `type:feature` or `type:epic`
- Verified `main`: `<full SHA>`
- Technical summary: <immutable comment URL>
- PO confirmation: `<author>` at `<created timestamp>` — <immutable comment URL>
- Checks: <checks recorded in the technical summary>
- PO hands-on checks: E<n>, … confirmed by the PO's reply
- Blockers: none
- Close result: closed
```

Include the `- PO hands-on checks:` line only when the technical summary had
`PO CHECK` rows; it names those criteria, which the PO's `yes` confirms. The
reply rules are unchanged.

The evidence records the PO's comment; it must not claim that the verifier made
the product judgment. Return a concise human-readable result with target URL,
full SHA, action (`CLOSED`, `REPAIRED`, or `NOOP`), summary URL, confirmation
URL, evidence URL, blockers, and next action. Produce no JSON.
