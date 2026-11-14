# Project Status Model

This document is the canonical reference for the project status model used in this repository. It states what each board status shows, the GitHub fact behind it, and the order in which the facts are checked.

Downstream stories (#117, #119, #120) depend on this document. Do not add executor routing, handoff templates, blocked-status protocol detail, feature/epic closure protocol, or OSS invariant content here — those belong in their respective stories.

---

## The Board Is a Display

The board's Status field shows a fact about each issue. It never authorizes or triggers work.

- **One writer.** The scheduler's writer, `hpw dispatcher status sync` (issue #476), is the only writer of the Status field. The dispatcher runs it once per run, in the "Write the board status" step (`docs/DISPATCHER-CONFIG.md` → Board status — the only writer). It derives each item's status from GitHub facts and writes the items that differ.
- **No manual moves.** No command, executor or person moves a card. A card moved by hand is corrected on the next dispatcher run.
- **No status authorizes work.** The scheduler chooses new work from GitHub facts (ADR-010), not from a status. `derive_status` in `src/headless_pr_workflow/dispatcher/status.py` derives the statuses, and this document states the same rules.
- **Epics and Features.** They never show a story-only status. A refined, open Epic or Feature shows In progress. An Epic with no merged spec shows No spec.

---

## Statuses

The statuses are listed in the order of `STATUSES` in `status.py`. Each one names the issue types it applies to and the GitHub fact behind it. Each status is a fact; none authorizes work.

### No spec

**Applies to:** all.

**Fact:** the issue is not refined and has no open linked PR, and either it has no open parent Epic, or its Epic has no merged spec and no open PR that adds one.

### In specification

**Applies to:** Epic. A story, task or bug that is not refined under an Epic whose spec is not merged also shows it while a spec PR is open.

**Fact:** an open PR adds or changes the Epic's `specs/<epic>-*/spec.md`.

### Refinement failed

**Applies to:** all.

**Fact:** the latest refine Session Summary after the current spec commit reports `refine-check` fail or unreadable, with no `/unblock` after it. The scheduler skips the issue until a new spec commit or an `/unblock`, so the PO can tell a stuck issue from one that waits in the queue.

### Not refined

**Applies to:** all.

**Fact:** the issue has no refinement for the current spec commit: it was never refined, it is a Feature not yet broken down, or it is stale after a scope change. This is the refinement queue, the issues the scheduler may refine next. A stale issue shows Not refined even with an open linked PR (see Precedence).

### Blocked

**Applies to:** all.

**Fact:** the issue has an open Blocked Declaration with no resolution (see [Blocked Status Protocol](#blocked-status-protocol)). An issue waiting for the PO's answer to a decision blocker shows Blocked.

### Refined

**Applies to:** Story, Task, Bug.

**Fact:** the issue is refined for the current spec, and at least one hard dependency is open, unresolved or unreadable. Refined is a waiting room, not an executor trigger.

### Ready to implement

**Applies to:** Story, Task, Bug.

**Fact:** the issue is refined for the current spec, every hard dependency is closed, and it has no open linked PR. The fact says the work could start; it authorizes nothing.

### In implementation

**Applies to:** Story, Task, Bug.

**Fact:** an open linked PR in draft. The draft PR is the record that the story is in implementation.

### In review

**Applies to:** Story, Task, Bug.

**Fact:** an open linked PR that is not draft, whose merge gates (`docs/MERGE-POLICY.md` and ADR-007) do not all pass on the current head. Fix rounds stay In review.

### Mergeable

**Applies to:** Story, Task, Bug.

**Fact:** the merge gates in `docs/MERGE-POLICY.md` and ADR-007 pass on the current head of an open, non-draft linked PR. The status reports the gates; it does not merge.

### In progress

**Applies to:** Epic, Feature.

**Fact:** the issue is refined and not closed.

### Done

**Applies to:** all.

**Fact:** the issue is closed.

### Removed statuses

These names are no longer statuses. Each one is replaced as follows: "Backlog" and "In refinement" by No spec or Not refined; "Ready for refinement" by none (it was an intent signal; the refinement queue is Not refined); "Ready for implementation" by Ready to implement; "Ready to merge" and "In merge" by Mergeable; "Needs rework" by In review.

---

## Precedence

The status of an issue is the first match in this order. A later step is not reached once an earlier one matches.

1. **Done**: the issue is closed.
2. **Blocked**: the issue has an open Blocked Declaration with no resolution.
3. **In specification**: an open PR adds or changes the Epic's spec (see the status above for which issues it applies to).
4. **No spec**: the issue is not refined and has no open linked PR, and it has no open parent Epic, or its Epic has no merged spec and no open spec PR.
5. **Refinement failed**: the latest refinement for the current spec failed, with no `/unblock` after it.
6. **Not refined**: the issue is not refined for the current spec and has no open linked PR.
7. For an Epic or Feature that is refined: **In progress**.
8. For a story, task or bug with an open linked PR: **Mergeable** if a non-draft PR passes its merge gates on the current head; otherwise **In review** if a PR is not draft; otherwise **In implementation** (an open draft PR).
9. **Ready to implement**: refined for the current spec, every hard dependency closed, no open linked PR.
10. **Refined**: a story, task or bug that is refined and has a hard dependency still open, unresolved or unreadable.

Notes on the order:

- An issue outside any open Epic that has an open linked PR shows the PR's status, not No spec.
- A refined story under an Epic with no merged spec keeps its implementation statuses, because it can still be implemented.
- An issue with a stale spec record shows Not refined even with an open PR, because no further implementation session starts for it (#460).

### Hand-off to review

Hand-off to review is the implementing session's rule, not a board rule. The `/implement` session marks its PR ready for review only after the required `pytest` check is green for the current PR head SHA; a failed, pending, absent, unreadable, or older-head result keeps the PR a draft, which keeps the item In implementation. This binds the implementing session only: the board derives In review from the draft flag and the merge gates in `docs/MERGE-POLICY.md`, and `/review` does not gate on the check. Merge stays gated by the required checks in `docs/MERGE-POLICY.md`, unchanged.

---

## Refined vs Ready to Implement

These two statuses are both derived facts. They differ only in whether every hard dependency listed in the issue's `## Dependencies` section is closed.

| | Refined | Ready to implement |
|---|---|---|
| Refined for the current spec | Yes | Yes |
| Every hard dependency closed | **No** | **Yes** |
| Shows on the board | Dependency waiting room | Refined and unblocked |

Neither status authorizes work. The scheduler chooses new work from GitHub facts (`docs/DISPATCHER-CONFIG.md`), and no executor pulls from a status.

---

## Status Summary Table

| Status | Issue types | Fact |
|---|---|---|
| No spec | all | Not refined, no open linked PR, and no open Epic with a merged spec (or no open parent Epic) |
| In specification | Epic (and unrefined issues under an Epic whose spec PR is open) | An open PR adds or changes the Epic's spec |
| Refinement failed | all | Latest refinement for the current spec failed, with no `/unblock` after it |
| Not refined | all | No refinement for the current spec: the refinement queue |
| Blocked | all | An open Blocked Declaration with no resolution |
| Refined | Story, Task, Bug | Refined, and a hard dependency is open, unresolved or unreadable |
| Ready to implement | Story, Task, Bug | Refined, every hard dependency closed, no open linked PR |
| In implementation | Story, Task, Bug | An open linked PR in draft |
| In review | Story, Task, Bug | An open non-draft linked PR whose merge gates do not all pass |
| Mergeable | Story, Task, Bug | The merge gates pass on the current head of the open non-draft linked PR |
| In progress | Epic, Feature | Refined and not closed |
| Done | all | The issue is closed |

---

## Handoff Protocol

When an executor must stop mid-story, it commits all in-progress work to the feature branch and writes a structured handoff note to GitHub before stopping. The handoff note is the durable record that enables any subsequent executor to resume without relying on session memory or terminal history.

See also: [State Model](HEADLESS-PR-WORKFLOW.md#state-model) — handoff notes are durable state; they must be stored in GitHub or committed workflow metadata, not in local execution context.

### Handoff note format

Handoff notes are plain structured markdown. No model-specific conventions, annotations, or front matter are used (OSS compatibility invariant).

Post the handoff note as a comment on the PR or issue. Use this template exactly — all six fields are required:

```
## Handoff Note
Story ref: #<issue-number>
Last action: <what was done in this session>
Next action: <what the next executor should do first>
Open questions: <unresolved questions, or "none">
Files touched: <list of files changed, or "none">
Token budget consumed: <estimated tokens used, or "unknown">
```

**Field definitions:**

| Field | Required content |
|---|---|
| Story ref | GitHub issue number for the story being implemented |
| Last action | The last concrete action taken — a completed step, a pushed commit, a decision made |
| Next action | The first concrete action the next executor must take to resume work correctly |
| Open questions | Any unresolved questions the next executor must answer before or during work; "none" if there are none |
| Files touched | All files modified or created so far in this story's branch; "none" if no files were changed |
| Token budget consumed | Approximate token count used in this session; helps the next executor plan scope |

### When to write a handoff note

Write a handoff note whenever:

- An executor stops mid-story for any reason (token limit, session end, external interrupt).
- The story shows Blocked and the executor is disengaging.
- The story is being deliberately handed off to a different executor type (e.g., Claude Code → Codex).

A handoff note is not required when the story completes in a single session and its PR is marked ready for review.

---

## Recovery Protocol

The recovery protocol applies when a story shows **In implementation** (an open draft linked PR) but shows signs of abandonment: no recent activity and no handoff note.

See also: [Lifecycle](HEADLESS-PR-WORKFLOW.md#lifecycle) — takeover between assistants, machines, and sessions must be recoverable from GitHub state plus explicit handoff notes.

### Stale detection

A story that shows **In implementation** is considered stale when **both** of the following are true:

1. No activity (commits, comments, PR updates) has occurred in **more than 2 hours**.
2. No handoff note is present on the PR or issue.

Both conditions must be present. Inactivity alone does not trigger recovery — a handoff note explaining the pause is sufficient. No other time-based heuristics apply beyond this stale trigger.

### Recovery action

When a stale story is detected on executor pre-flight:

1. The executor posts a **recovery comment** on the issue or PR explaining the stale detection. The comment is the record that an abandoned branch exists.
2. No status is changed by hand. The next dispatcher run derives the status from the facts; an open draft PR keeps the story In implementation.
3. No in-progress work is discarded — the branch and any commits remain intact.
4. The next executor may resume from the existing branch or start fresh, based on the state of the branch.

### Recovery comment format

```
## Recovery Comment
Detected: story in "In implementation" with no activity for >2h and no handoff note.
Action: no status change; the board shows the status derived from the GitHub facts.
Branch: <branch-name> — existing commits intact.
Next executor: review branch state before pulling.
```

### What recovery does not do

- Recovery does not change or roll back any board status.
- Recovery does not delete commits or branches.
- Recovery does not reassign the executor label.
- Recovery does not trigger automatic re-implementation.

---

## Blocked Status Protocol

A story, task or bug shows **Blocked** while it has an open Blocked Declaration with no resolution. Progress has stopped due to a condition that cannot be resolved by the current executor alone.

See also: [Lifecycle](HEADLESS-PR-WORKFLOW.md#lifecycle) — blockers are recorded in GitHub review threads, comments, checks, or issue/PR metadata.

### Blocker types

Exactly five blocker types are recognised:

| Type | Definition |
|---|---|
| **dependency** | A hard dependency has not been completed or resolved. |
| **decision** | A decision required to proceed has not been made after checking existing repository documentation and parent decisions. Only the PO resolves genuine decision blockers. |
| **external** | Progress depends on an action or output from outside the repository (e.g., a third-party API, an infra change, an external review). |
| **conflict** | A file conflict or WIP overlap with another active story prevents progress. |
| **resource** | A required resource (token budget, execution environment, tool access) is unavailable. |

The scheduler reads an open `Type: dependency` declaration on a Story, Task or Bug (`dependency_blocker` in `hpw dispatcher facts`): the story is not ready and is not started until the PO's `/unblock` line or a later passing refine Session Summary lifts it.

### Declaration format

When declaring a blocker, post a structured GitHub comment on the issue or PR using this format exactly:

```
## Blocked Declaration
Type: <dependency | decision | external | conflict | resource>
Checked: <required when Type is decision: repository documentation and parent Epic/Feature Decisions sections searched, followed by "no existing decision found"; omit for all other blocker types>
Question: <required when Type is decision: the decision itself, one sentence; omit for all other blocker types>
Options: <required when Type is decision: 2-4 options, one per line, each as "<letter>) <option> — <consequence>"; omit for all other blocker types>
Recommendation: <required when Type is decision: the letter of one listed option plus a one-sentence reason; omit for all other blocker types>
Declared by: <executor name or GitHub handle>
Blocks: #<issue-number> — <story title>
Unblocked when: <specific, testable condition — not a general description>
Owner: <who is responsible for resolving this blocker>
State of in-progress work: <branch name and summary of work completed so far, or "none">
Resume instruction: <required when Type is decision: "To resume: reply on this issue with your decision, including a line that is exactly `/unblock`." — write "n/a" for all other blocker types>
```

All fields shown for the selected blocker type are required. The **"Checked"** field is mandatory for `Type: decision` and must name what was searched so the pre-declaration check is auditable; omit it for other types. The **"Unblocked when"** field must state a specific, testable condition — not a vague description like "when the dependency is done". The **"Resume instruction"** field is what makes the `/unblock` convention discoverable at the point the PO is asked to respond — never omit it from a `Type: decision` declaration. The **"Question"**, **"Options"** and **"Recommendation"** fields are required for `Type: decision` and omitted for the other four types: `Question` is one sentence; `Options` is 2–4 lines, each `<letter>) <option> — <consequence>`; `Recommendation` names one listed option letter and a one-sentence reason. The `decision_blocker` Slack notification carries them so the PO can decide without opening the issue.

### Decision blockers

Decision blockers require immediate PO involvement:

- Before declaring one, search `docs/PROJECT-STATUS.md`, `docs/decisions/ADR-*.md`, `docs/HEADLESS-PR-WORKFLOW.md`, all other relevant `docs/*.md`, and the parent Epic's and Feature's `## Decisions` sections for an existing answer.
- If an existing answer is found, record and follow it directly. This is a lookup task: do not declare a decision blocker or escalate it to the PO.
- If no answer is found, include a **Checked** line naming the documentation and parent decision sections searched and ending with `no existing decision found`.
- State the decision itself with the **Question**, **Options** and **Recommendation** fields, written after **Checked**.
- Post the declaration comment — including the **Resume instruction** field above, filled in verbatim — and **@mention the PO** in the same comment.
- Only the PO may resolve a decision blocker.
- The executor must not attempt to unblock a decision blocker unilaterally.

### Monitoring and escalation

- The PO reviews the board weekly for blocked items.
- Executors scan for blocked stories on every pre-flight.
- **No story may remain Blocked for more than 1 week without an owner update.** If the owner has not posted an update within 1 week, the PO escalates.

### Resolution

When a blocker is resolved:

1. **Decision blockers:** the PO replies with their decision in a comment that includes a standalone line — trimmed of surrounding whitespace — that is exactly `/unblock`. The word "unblock"/"unblocked" appearing in ordinary prose does not count; only a full line equal to `/unblock` qualifies. The dispatcher detects this automatically on its next poll. The resolution ends the Blocked fact, and the next dispatcher run derives the status from the facts: **Ready to implement** when the story is refined and every hard dependency is closed, **Refined** otherwise. No manual board-status change is required.
   A decision blocker raised by a refinement is not resumed this way: a new refinement session (`dispatcher-invoke.sh --refine <n>`, `docs/DISPATCHER-CONFIG.md` → Refinement sessions) reads the answer, and the refinement's result decides the next status. The resume alone never makes the issue Ready to implement.
2. **All other blocker types** (dependency, external, conflict, resource): the owner posts an **"Unblocked"** comment on the issue or PR, referencing the original declaration. The next dispatcher run derives the status again: **Refined** while hard dependencies remain, **Ready to implement** once all of them are closed. Nobody moves the status by hand.
3. The `executor:` label is set in refinement and is not changed by a resolution.

### Cascading blocked rules

The Blocked fact belongs to the issue that declares it. A downstream story is not shown Blocked solely because an upstream story is blocked; its own status is derived from its own facts, for example Refined while its hard dependency is open. Routing reads those facts, so no label is changed to route work around an upstream blocker.

---

## Closure Protocol

The closure protocol defines the roles and responsibilities at feature and epic closure. Two distinct types of success criteria require different judges.

See also: [Review Separation](HEADLESS-PR-WORKFLOW.md#review-separation) — the same separation-of-roles principle that governs PR review applies at feature and epic closure.

### Role split

| Criteria type | Definition | Judge |
|---|---|---|
| **Technical** | Tests pass, contract implemented correctly, files match the story declaration | **Codex** |
| **Product** | Is this the right thing? Does it match the vision? Is documentation clear? | **PO** |

**PO role at closure:** product judge only. A one-line confirmation is sufficient. The PO never writes the closing comment and is not expected to assess technical correctness.

**Codex role at closure:** technical verifier and closing comment author. Codex verifies technical criteria, writes the structured closing comment, and performs the close action.

### Feature closure flow

1. The implementing executor closes the last story in the feature and posts a signal comment on the feature issue.
2. Codex runs technical verification against all feature success criteria.
3. Codex writes a structured closing comment on the feature issue, summarising what was delivered vs. what was scoped.
4. The PO reads the summary and answers one question: *"Is this what I wanted?"*
5. The PO gives a brief one-line product confirmation.
6. Codex closes the feature issue.

### Epic closure flow

1. The last feature closes. Both executors (Claude Code and Codex) produce a joint delivery summary against the epic goal.
2. The PO reads the product-level summary and gives a brief approval.
3. Codex closes the epic issue with a formal closing comment.

### What the PO must not do

- Write the technical closing comment.
- Assess whether tests pass or contracts are correctly implemented.
- Perform the GitHub close action (Codex does this).

### What Codex must not do

- Make the product judgment ("Is this the right thing?").
- Require the PO to write a detailed technical summary.
- Wait for the PO before posting the structured closing comment — post it, then wait for the one-line product confirmation.

### Starting deterministic closure verification

Run `/verify-closure <issue-number>` for an open issue labeled with exactly one
of `type:feature` or `type:epic`. The command's first phase is read-only with
respect to repository contents and issue lifecycle state. Its only permitted
mutation is one successful technical-evidence comment; it never closes,
relabels, or changes the Project status of the target.

The verifier first binds all local file and test evidence to a fresh remote
`main` SHA. Feature verification inventories open and closed children and their
merged pull requests. Epic verification requires all direct child Features to
be closed. Both paths map each declared criterion to concrete evidence and run
the focused and full test suites. Missing, failed, stale, ambiguous, or
environment-limited evidence blocks a passing result and prevents a request for
PO confirmation.

An Epic with a merged spec (`specs/<n>-*/spec.md` at the verified `main` SHA)
is verified against the spec, not its issue body: the goal is the spec's
`## Goal` and the criteria are its `**E<n>:**` items in spec order. The
summary header names `Spec: <path> @ <SHA>`. Several spec folders, an unreadable
spec, or a spec with no goal or no Epic criterion block verification. An Epic
with no merged spec is verified from its issue body as before, and the summary
says `Spec: none found — criteria taken from the issue body`.

For an Epic with a merged spec, `scripts/criteria-check --epic <n> --ref <SHA>`
is a required check; its output goes under `### Coverage check`. A criterion
whose **Verified by:** line names "the PO" may get the result `PO CHECK`: the
verifier cites what it can prove, and the summary's `### PO hands-on checks`
section gives the PO numbered steps, the exact text to post, the expected
result and how to tell pass from fail. The PO does these checks, then replies
`yes` only if every one gave the expected result.

A successful comment records the target and type, full verified SHA, child and
merged-PR inventory, criterion evidence matrix, exact checks and outcomes,
blockers, residual risk, and next action. It also includes a stable marker made
from the target number and full `main` SHA. A repeated run for that same key
returns the existing comment instead of duplicating authoritative evidence; a
new `main` SHA requires complete re-verification.

After the evidence comment is posted, the command asks the PO for product
confirmation and stops. Confirmation detection and safe close mutation happen
only on a later invocation.

### Continuing closure after PO confirmation

After reading the successful technical summary, the PO posts exactly one brief
comment on the same issue: a trimmed, case-insensitive `yes` to confirm and
close, or `no` to decline.

Run `/verify-closure <issue-number>` a second time. The command recognizes its
authoritative technical-summary marker and enters the continuation instead of
repeating verification. It accepts only an unedited comment by
`LauraMardones`, created after the current summary, whose trimmed body is
exactly `yes` (case-insensitive) to close, or exactly `no` to decline and
report a `DECLINED` outcome without mutating anything. A `yes` also confirms
the PO hands-on checks of the summary, and the closing-evidence comment names
those criteria. Free-form approval,
reactions, edited comments, other authors, `yes`/`no` embedded in a longer
sentence, and out-of-band messages do not authorize closure or a decline.

Immediately before closing, the continuation refreshes repository and target
identity, lifecycle state, type, summary, confirmation, and remote `main` SHA.
For an Epic it also refreshes all direct child Features and requires every one
to be closed. A new `main` SHA makes the summary stale and requires technical
verification again; approval for older evidence is never carried forward.

After GitHub confirms the close, the command posts one formal closing-evidence
comment containing the verified SHA and immutable summary and confirmation
links. Its stable marker makes repeated invocations a no-op. If closing succeeds
but comment creation fails, rerunning the same command detects the closed issue,
posts only the missing evidence comment, and never repeats the close request.

---

## Ready Buffer

Refinement is Tech Lead work (`docs/ROLES.md`), done just in time against the ready buffer of ADR-009 item 6: the buffer of 3–5 "ready" stories in `docs/REFINEMENT-PIPELINE.md`. The scheduler, not a person, decides which issue is refined next, from the **Not refined** queue.

See also: [Lifecycle](HEADLESS-PR-WORKFLOW.md#lifecycle) — the workflow loops continuously; the ready buffer keeps the loop fed.

### Rule

Refine an issue when the ready buffer needs one, and in the order the scheduler gives.

**Never refine more than the buffer needs.** Refining too far ahead wastes refinement work when decisions change during implementation.

### Gate for sequential dependencies

Do not begin refining story N+1 until story N's key decisions and interfaces are stable. "Stable" means:

- The story's acceptance criteria are finalised.
- The files it will touch are declared.
- Any decisions that downstream stories depend on have been made and recorded.

If story N is still in early implementation and key interfaces are unresolved, hold refinement of N+1 until those decisions are made.

### Parallel lanes

Refinement of stories in parallel lanes (no sequential dependency between them) may proceed independently of this gate. The gate applies only to stories that are sequentially dependent.

---

## File Ownership Tagging

Every story body must include a `## Files affected` section before it can show **Ready to implement**. The Tech Lead adds this section during refinement so the WIP pre-flight check can detect file ownership conflicts before new work starts.

The section is a plain markdown list of repository-relative file paths:

```md
## Files affected

- `docs/PROJECT-STATUS.md`
- `.claude/commands/implement.md`
```

The list declares expected ownership for the story. It does not have to predict every incidental test or documentation file, but it must include every known primary file, command file, workflow contract, and shared surface the story is expected to modify.

If the affected files are unknown during refinement, record the uncertainty in Open Questions and do not finish the refinement until the file list is actionable. The story must not show **Ready to implement** before then.

---

## WIP Pre-Flight Check

Before a story that shows **Ready to implement** starts (its first draft PR), the WIP pre-flight check runs using GitHub as the source of truth.

The pre-flight check is:

1. Read the candidate story's `## Files affected` section.
2. Scan open stories that show **In implementation** (an open draft linked PR).
3. Read each active story's `## Files affected` section.
4. Count active **In implementation** stories.
5. Compare file lists for overlap with the candidate story.

The WIP limit is the repository variable `WIP_LIMIT_IMPLEMENTATION` (see [WIP and queue limits](DISPATCHER-CONFIG.md#wip-and-queue-limits)); the current value is **2 parallel stories**. The dispatcher and the flow review read the variable, so the number is not stated anywhere else as a source of truth. Parallel implementation is allowed only when both of these conditions are true:

- Fewer than `WIP_LIMIT_IMPLEMENTATION` stories are already **In implementation**.
- The candidate story's declared files do not overlap with any active **In implementation** story.

If `WIP_LIMIT_IMPLEMENTATION` or more stories are already **In implementation**, do not proceed. If file overlap is detected, do not proceed. Stop and report the conflict as a **conflict** blocker using the Blocked Status Protocol.

If no overlap exists and the WIP count is below `WIP_LIMIT_IMPLEMENTATION`, the story may start. Its implementation branch and draft PR show **In implementation**; no status is written by hand.

This pre-flight logic is part of the workflow contract. Model-specific commands, prompts, and adapter files must reference this contract instead of redefining incompatible rules.

### OSS compatibility invariant

Workflow policy must live first in repository documentation such as this file. Model commands are adapters: they may operationalize the policy for Claude Code, Codex, or another executor, but they must not be the only place where the rule exists.

---

## OSS Compatibility Invariants

These invariants preserve the workflow's ability to support future open-source executors such as Llama, Mistral, Qwen, or other local models. Open-source model integration is deferred, but these architectural decisions must remain portable.

### GitHub Tool Use Is A Separable Wrapper

Risk level: HIGH.

Decision point to preserve: workflow logic must be documented and implemented independently from GitHub API calls. GitHub is the current source-of-truth system, but calls to GitHub must remain wrapper behavior around the workflow, not the workflow policy itself.

If adding an OSS executor, ensure this invariant is preserved: the executor may use a different GitHub wrapper, relay service, or human-mediated sync path without changing the orchestrator's core status, routing, handoff, or blocker rules.

### Executor Routing Is Data-Driven, Not Logic-Driven

Risk level: HIGH.

Decision point to preserve: routing decisions must come from declared capability profiles and labels, not from hardcoded branches such as `if executor == "codex"` or `if model == "claude"`.

If adding an OSS executor, ensure this invariant is preserved: add or update a capability profile and routing label instead of adding executor-specific conditional logic to orchestration code or command instructions.

### Capacity Is Abstracted For Metered And Unmetered Executors

Risk level: MEDIUM.

Decision point to preserve: orchestrator capacity must not assume that all executors are API-metered by tokens. Some executors may be constrained by API budget, local hardware, queue depth, wall-clock time, or availability windows.

If adding an OSS executor, ensure this invariant is preserved: express capacity as an abstract scheduling input rather than as a provider-specific token counter.

### Pre-Flight Logic Lives In Workflow Contract Docs First

Risk level: MEDIUM.

Decision point to preserve: pre-flight rules, including the WIP conflict check, live in this workflow contract before they are wired into model-specific commands such as `/implement`.

If adding an OSS executor, ensure this invariant is preserved: adapter commands may operationalize the pre-flight policy, but the canonical rule remains in `docs/PROJECT-STATUS.md`.

### Handoff Note Format Is Plain Markdown Only

Risk level: MEDIUM.

Decision point to preserve: handoff notes use plain structured markdown with no tool-specific syntax, hidden metadata, YAML front matter, or model-specific annotations.

If adding an OSS executor, ensure this invariant is preserved: the executor must be able to read and write handoff notes as ordinary markdown comments in GitHub or another durable planning surface.
