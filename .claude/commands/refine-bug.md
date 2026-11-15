Act as the Tech Lead (`docs/ROLES.md`) with GitHub access.

Refine the following GitHub bug issue until it meets the project's refinement standard.

Repository:
LauraMardones/headless-pr-workflow

Issue:
#$ARGUMENTS

Do not implement code.

Follow the `## Tech Lead rules` in `.claude/commands/refine.md`: spec lookup and check, no refinement without a merged spec, the spec record, decision blockers instead of questions to the PO, resume after `/unblock`, lens questions and usage findings. Follow them before any issue write. Ask the PO nothing in the session.

## Catch-all sort

Run this step only when the issue's `Feature group:` number equals `CATCH_ALL_FEATURE` in `src/headless_pr_workflow/catch_all.py` (read the constant from that file, never from a literal here). Otherwise skip it. It runs after the Tech Lead rules' spec lookup and check for the issue's current Epic and before any issue body write. Sort the issue into exactly one outcome, checked in this order:

1. **Moved:** the finding fits an existing criterion `F<k>.<m>` of an open Feature that has written `F<k>.<m>` criteria and is refined, under an open Epic with a merged spec. A closed Feature or Epic is never a target. Rewrite `Parent epic:`, `Feature group:` and the spec record to that Feature and its Epic (the spec lookup and check run again for the target Epic). Keep `Found under:` and `Origin:`. Moved adds no criterion (ADR-008 item 5, ADR-009). Refinement continues as a story or bug of that Feature.
2. **Maintenance:** allowed maintenance by the general line of spec #530 → Decisions: class M1, M2, M3, M4, M6 or M8 as part of an allowed fix (M1 is a bug that breaks a criterion of a closed Epic's merged spec). Refine the issue where it is, without the PO, with exactly one `prio:` and one `executor:` label.
3. **Decision:** anything else (M5, M7, M8 on its own, M9, M10, or any change to described behaviour). Post a `Type: decision` Blocked Declaration that names the M class, or the part of the general line the finding fails. The issue body and its criteria stay unchanged.

Record the outcome where code can read it:

- For Moved and Maintenance, add a `### Catch-all sort — YYYY-MM-DD` entry under `## Decisions` (never for Decision: the body stays unchanged and the Blocked Declaration is the mark). A Maintenance entry names exactly one class token among `M1`, `M2`, `M3`, `M4`, `M6`, `M8` and says how the fix meets the general line; an M8 refactoring needed by another allowed fix is named in prose as "refactoring" without a second class token. A Moved entry names the target Feature and its criterion `F<k>.<m>`. A re-refinement adds a new entry; old entries are not edited.
- The Session Summary carries one line: `Catch-all sort: moved to #<feature> (F<k>.<m>)`, `Catch-all sort: maintenance M<n>` or `Catch-all sort: decision M<n>` (`decision general-line` when the failed part is the general line and not an M class).
- `scripts/refine-check` reports `catch_all_sort` for an issue under the catch-all Feature: it fails without a sort entry, or when the latest entry names no class token, a class outside the allowed six, or more than one class.

## Core Instructions

- Fetch the current GitHub issue title, body, labels, linked epic, task lists, dependencies, and relevant comments.
- Inspect relevant repository docs before changing the issue.
- Update the GitHub issue body directly with the refined bug report.
- Do not only return the refined report in chat.
- GitHub must remain the source of truth.
- Preserve useful existing issue content, but reorganize it into the project bug format.
- Do not discard prior decisions, constraints, dependencies, or context unless clearly obsolete.
- If something appears obsolete, move it to Notes instead of deleting it silently.
- If you do not have permission or tooling to update GitHub, stop and say exactly what is missing.

## Outcome

Refinement writes no board status and no status line in the issue body. The outcome is a fact in the Session Summary, as `Next action:`:

- `Next action: Ready for implementation`: the contract is clear, `refine-check` passed and no hard dependency is open.
- `Next action: Refined; blocked by #X`: the contract is clear and `refine-check` passed, but hard dependencies are still open.
- `Next action: Needs PO decision`: a decision blocker is declared on the issue.

Never report `Ready for implementation` while hard dependencies are still unresolved.

## Refinement Standard

Refinement is complete when the issue has:

- clear Goal (what the fix must achieve)
- clear Defect Description with steps to reproduce, expected behavior, and actual behavior
- Severity and Impact assessment
- Environment information
- Scope of the fix
- Out of Scope
- testable Acceptance Criteria (the bug no longer reproduces + regression test exists)
- Epic and Feature Requirements Mapping (see the section of that name below)
- the spec record line and a `Feature group: #<n>` line
- Dependencies split into Hard depends on and Soft depends on
- Assumptions
- Risks / Blockers
- Open Questions, if any remain
- Technical Notes with relevant files, commands, or policy surfaces
- Test Expectations including regression test requirement
- Documentation Impact
- Definition of Done
- clear command surface if the bug affects the CLI
- clear JSON output, human-readable output, and exit-code behavior if it affects a command

## Epic and Feature Requirements Mapping

Every refined Bug carries a `## Epic and Feature Requirements Mapping` section, so the implementer builds and the reviewer judges the same scope.

- Read the parent Epic's merged `specs/<n>-*/spec.md` (success criteria, requirements) as the approved scope (ADR-008), and the parent Feature's issue body. Use the Epic issue body only as a legacy fallback when the Epic has no spec; never take requirements from it when a spec exists, because it is only an index.
- List each Epic or Feature requirement that applies to the bug, each as exactly one of:
  - covered by an acceptance criterion: reference the criterion's number (`AC 2`);
  - an entry under `Not in this bug` with a reason: `deferred to #N` when another issue will deliver it, or `not applicable` with the reason.
- Trace both ways (ADR-009): every acceptance criterion of the bug must appear in the mapping and name the Feature criterion it serves (`F1.1`); a bug criterion without a Feature parent, or mapped directly to an Epic criterion, adds scope and is not allowed. Epic criteria are reached through the Feature criteria.
- Point to the requirement in one line; do not restate the Epic spec in the bug.
- Record a deliberately uncovered Epic requirement under `Not in this bug`, so a reviewer does not rediscover it as a blocker.
- A deferred requirement that an existing issue delivers links it (`deferred to #N`). If none exists, the follow-up is a catch-all issue made with `hpw catch-all-issue create --repo <owner/repo> --found-under <epic number of this bug> --origin <https link to this bug> --title <text> --reason <text>` (`Found under:` the Epic of the bug being refined, `Origin:` linking it; add `--kind bug` for a defect). Where `gh` is unavailable, the GitHub plugin creates the issue with the same lines and the Session Summary says the helper could not run. If the catch-all issue cannot be made, raise a `Type: decision` Blocked Declaration. The follow-up must be linked before the bug moves to `Ready for implementation`.
- Cross-cutting check: time bounds, delivery guarantees and similar statements an executor needs must be copied into the bug's Scope or Acceptance Criteria, not only referenced in the mapping.
- Split check: when the bug adds a new script, a workflow step and docs together, record a `Split decision:` line in `## Decisions` (`Split decision: split into #A, #B` or `Split decision: keep together because ...`).
- Size gate (`scripts/refine-check` checks `files_affected`, `size_split` and `haiku_limit`): write a `## Files affected` section listing individual repository-relative file paths, one per list item (no directories or globs). More than 10 distinct paths, or more than 5 distinct `tests/` paths, requires a split, or a `Split decision: keep together because <reason>` line in `## Decisions` and at least `executor:claude-code-sonnet`. `executor:claude-code-haiku` is allowed only for at most 3 listed paths, none read word for word by a test (a test that compares full file content or required literal text, or a path a test source references, counts; mark a declared verbatim read in the item, e.g. `` - `docs/X.md` (read verbatim by tests/test_x.py) ``).
- Orchestration rule (`scripts/refine-check` checks `orchestration_executor`): a story, task or bug labelled `flow:orchestration` or `cp:critical-path` (either label is enough) gets exactly the label `executor:claude-code-opus`; Epics and Features carry no `executor:` label. It combines with the size gate by taking the stronger minimum; neither rule lowers the other.

## Open Questions Format

Questions for the PO are not written here for the PO to answer in the session. A product question becomes a `Type: decision` Blocked Declaration posted on the issue (`.claude/commands/refine.md` → Tech Lead rules → Decision blockers), after the search for an existing answer in `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections. If an existing answer is found, record and follow it instead of escalating. The affected acceptance criterion stays unchanged while the blocker is open.

The `## Open Questions` section of the issue body lists the link to each blocker comment while the issue is blocked, and reads `None.` before it moves to `Ready for implementation` (`scripts/refine-check` fails `open_questions` otherwise).

## Decisions Format

When a decision blocker is answered by the PO's `/unblock` reply, or the Tech Lead settles a technical or delegated choice (label, executor, slicing, technical approach), record it under Decisions with the chosen option, rejected alternatives, date and comment link (when answered by the PO). Do not delete rejected options — they are part of the record. If a decision reverses a prior decision, add a new entry rather than editing the old one.

Each decision must be structured as:

```md
### [Decision title] — YYYY-MM-DD
**Chosen:** [Option name] — [brief reason in plain language]
**Rejected:** [Option name] — [brief reason why not]
```

## Label Handling

- Inspect existing repository labels first.
- Use only existing GitHub labels from the repository.
- Do not invent new labels.
- Apply existing labels for type (bug), priority, area, flow, and status when the correct label is clear.
- If the correct existing label is unclear, the Tech Lead chooses among the existing labels and records the choice under `## Decisions`; a choice that needs a product decision becomes a `Type: decision` Blocked Declaration.

## Dependency Handling

- Refinement may happen before hard dependencies are resolved.
- Hard dependencies block implementation, not necessarily refinement.
- If a dependency may change the command surface, output shape, or policy decision, state the assumption explicitly:
  `Dependency contract assumed: ...`
- If the story must be revalidated after a dependency closes, write:
  `Revalidate after dependency closes: yes`
- If a label, epic link or dependency is missing, the Tech Lead settles it and records it under `## Decisions`; a missing business decision becomes a `Type: decision` Blocked Declaration instead of a guess.

## Issue Body Format

Update the issue body to this structure:

```md
Parent epic: #{EPIC_NUMBER} {EPIC_TITLE}
Feature group: #{FEATURE_NUMBER} {FEATURE_TITLE}
Parallel lane: {LANE}
Critical path: yes/no
Severity: critical | high | medium | low
Spec: [`specs/{EPIC_NUMBER}-{slug}/spec.md`]({blob link at the commit}) at `{commit}`

## Goal

What the fix must achieve.

## Defect Description

**Steps to reproduce:**
1. ...
2. ...
3. ...

**Expected behavior:**
...

**Actual behavior:**
...

**Environment:**
- OS / platform: ...
- Version / commit: ...
- Relevant config or flags: ...

## Impact

Who and what is affected. Data loss, incorrect output, blocked workflow, etc.

## Scope

- What the fix covers.

## Out of Scope

- What should not change as part of this fix.

## Acceptance Criteria

- The defect no longer reproduces following the steps above.
- A regression test exists that would have caught this bug.
- ...

## Epic and Feature Requirements Mapping

- {Feature criterion or requirement, one line} → AC {n}
- AC {n} serves {Feature criterion, e.g. F1.1} (every AC is listed)

Not in this bug:

- {requirement} — deferred to #N: {reason}
- {requirement} — not applicable: {reason}

## Command Surface

If relevant:

- `hpw <command> <target> --repo <owner/repo>`
- flags:
  - `--json`
  - ...
- exit codes:
  - `0` when ...
  - non-zero when ...

## Output Contract

If relevant:

JSON output includes:

- ...

Human-readable output includes:

- ...

## Test Expectations

- Regression test that reproduces the original defect before the fix.
- ...

## Documentation Impact

- ...

## Dependencies

Hard depends on:

- #X ...

Soft depends on:

- #Y ...

## Dependency Contract Assumed

If relevant:

- ...

Revalidate after dependency closes: yes/no

## Assumptions

- ...

## Risks / Blockers

- ...

## Open Questions

- <link to each decision blocker comment, or `None.`>

## Decisions

...

## Files affected

- `path/to/file` (one individual repository-relative path per item)

## Technical Notes

- Relevant files:
  - `src/...`
  - `tests/...`
- Reuse existing helpers:
  - ...
- Avoid:
  - ...

## Definition of Done

- Bug no longer reproduces following the steps above.
- Regression test added and passing.
- No unintended side effects on related commands or output contracts.
- PR reviewed and merged.
- ...
```

## Refinement Completeness Gate (mandatory)

The last step of every refinement, is the deterministic check; the model is not the judge of its own completeness.

- Run `python3 scripts/refine-check --issue <n> --repo LauraMardones/headless-pr-workflow`. It re-reads the issue and checks the `type:`, `executor:` and `prio:` labels, open hard dependencies, the required body sections, open questions and parent references. It reads no board.
- Run it on every refinement, including a re-refinement of an issue that is already `Ready for implementation`.
- Only exit `0` permits reporting `Refined` or `Ready for implementation`. Exit `1` (a named check failed) or exit `2` (the issue, labels or dependencies could not be read) forbids it.
- On a non-zero exit: fix what can be fixed and re-run the check; if a failure remains, list the failing checks as blockers and do not report `Next action: Ready for implementation` or `Refined`.
- `executor:` label rule: a `type:story`, `type:task` or `type:bug` issue carries exactly one existing `executor:` label (`docs/PROJECT-STATUS.md`); a `type:epic` or `type:feature` issue needs none. Apply it before the check, choosing among the repository's existing labels.
- Any statement about labels in the reply or the Session Summary comes from the check output, not from memory or from the issue text.
- The Session Summary `Checks run:` line carries the result: `refine-check — pass`, or `refine-check — fail: <check-ids>` / `refine-check — unreadable`. `none` is not valid for a refinement that reports a status.

## Final Reply Format
After updating GitHub, reply only with:

- Issue URL
- Next action: Ready for implementation, Refined; blocked by #X, or Needs PO decision
- Summary of what changed
- Labels changed
- Remaining hard dependencies
- Remaining Open Questions

