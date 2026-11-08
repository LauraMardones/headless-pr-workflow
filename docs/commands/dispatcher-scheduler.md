# Command: `hpw dispatcher scheduler next`

Chooses the dispatcher's next new-work action from the facts of [`hpw dispatcher facts read`](dispatcher-facts.md): which Epic, Feature or story to refine next, or which ready story to start implementing. It never writes to GitHub and never reads the board (ADR-010: decisions come from facts; ADR-014: one building block, one JSON line). It returns one action per run; the dispatcher calls it again on the next run (ADR-012: one session at a time). Code: `src/headless_pr_workflow/dispatcher/scheduler.py`.

```
hpw dispatcher scheduler next --repo <owner/repo> [--facts-file <path>] [--in-implementation <n>[,<n>...]] [--dry-run]
```

Python: `choose_next(facts, *, in_implementation, limits) -> Decision` is the rule itself and gets no gateway; `next_action(gateway, repo, *, in_implementation, env=None, facts=None) -> Result` loads the limits, reads the facts with `read_facts` unless they are given, and returns what the command prints.

- `--facts-file <path>`: use the JSON line printed by `hpw dispatcher facts read` instead of reading GitHub. A file that cannot be read or parsed, or whose `result` is not `read`, gives exit `3`.
- `--in-implementation`: the issue numbers of the stories in implementation, comma-separated; empty or absent means none. The list is trusted as given: the caller owns it (#474 for new work, #475 for work in flight).
- `--dry-run` is accepted and changes nothing: the command has no write to skip.

Environment: `GH_TOKEN` (through `gh`, only without `--facts-file`), `DECISION_BLOCKER_PO_LOGIN` (passed to the facts reader), and the three limits `WIP_LIMIT_IMPLEMENTATION`, `READY_BUFFER_MIN`, `READY_BUFFER_MAX`, checked by the rules of `scripts/lib/limits.sh`: a whole number (`08` is 8), `WIP_LIMIT_IMPLEMENTATION` 1 or more, `READY_BUFFER_MIN` not above `READY_BUFFER_MAX`. A missing, empty or invalid value gives exit `3` before any GitHub read, and `error` names the variable and its value.

## Exit codes

| Code | Result | Meaning |
|---|---|---|
| `0` | `refine` or `implement` | an action is chosen |
| `1` | `idle` | nothing to do (`action` is `null`) |
| `2` | `invalid` | invalid arguments |
| `3` | `unreadable` | the facts cannot be read or used, or a limit is invalid (`action` is `null`) |

## Output

One JSON line on stdout: `{"ok", "block": "scheduler", "operation": "next", "result", "action", "skipped", "notes", "counts", "limits", "error"}`.

- `action`: `null`, or `{"kind": "refine" | "implement", "issue", "type" ("epic" | "feature" | "story" | "task" | "bug"), "epic", "step" (2–6), "reason"}`. Reason words: `scope-change`, `stale-record`, `unblocked`, `new-spec`, `not-refined`, `ready`.
- `skipped`: one `{"issue", "reason"}` per issue that could not be chosen for a reason below, sorted by issue number. An issue that could be chosen but loses to an earlier one is not listed.
- `notes`: `files-affected-missing #<n>` for each story whose `## Files affected` section is missing when the overlap check runs.
- `counts`: `{"ready", "waiting", "in_implementation"}`; `null` when the result is `unreadable`.
- `limits`: the three values used, or `null` when they are invalid.

Stderr: `[SCHEDULER] <refine|implement> #<n> step=<k> reason=<word>` or `[SCHEDULER] idle` (or `[SCHEDULER] unreadable: <error>`), then one `[SCHEDULER] skip #<n> <reason>` line per skipped issue and one `[SCHEDULER] note <note>` line per note. Tokens are masked.

## Steps

The rule chooses steps 2 to 6 of #460 → "Order of actions in a run". Step 1, work in flight (merge, fix and conflict rounds, review), stays with the dispatcher. The lowest step with a candidate wins.

| Step | Candidates, in this order | `reason` |
|---|---|---|
| 2 — re-refinement and resume | a stale Epic (record `stale` or `invalid`); stale Features; stale stories in `--in-implementation`; any Epic, Feature or story resumed after `/unblock` | `scope-change`, `stale-record`, `unblocked` |
| 3 — Epic refinement after a new spec | an Epic with spec `merged` and record `missing` | `new-spec` |
| 4 — Feature refinement | a Feature that is not refined and not stale, only when the ready buffer is open and no story is left to refine (see "Just-in-time Feature breakdown") | `not-refined` |
| 5 — implementation start | a story whose facts say `ready`, not in `--in-implementation` | `ready` |
| 6 — story refinement for the ready buffer | stale stories, then stories never refined | `stale-record`, `not-refined` |

Rules that hold before a step:

- **Just-in-time Feature breakdown.** A Feature that is not refined and not stale is a step-4 candidate only when the step-6 buffer condition holds (`ready < READY_BUFFER_MIN` and `ready + waiting < READY_BUFFER_MAX`) and no story is left to refine. A story is left to refine when it is an open Story, Task or Bug whose `Feature group:` Feature is refined and not stale, whose Epic has a merged spec and a `current` record, that is not refined (never refined, or stale), is not in `--in-implementation`, and whose refine gate is neither `open-decision-blocker` nor `last-refinement-failed` (a story resumed after `/unblock` counts as left). A story whose facts are unreadable counts as left unless its Epic is known to have no merged spec or no `current` record. The count runs across all Epics. A Feature held back is skipped with `buffer-full` (checked first) or `stories-left-to-refine #<n>` (`<n>` the lowest such story); the next run breaks it down once none is left. Steps 2 and 3 are not held back.
- **No merged spec.** Under an Epic with spec `missing` or `ambiguous` nothing is refined, not the Epic, its Features or its stories. `skipped` lists the Epic once as `no-merged-spec: <facts reason>`, which names `specs/<epic>-*/spec.md`. A ready story there can still be implemented.
- **Epic first.** While an Epic's record is not `current`, its Features and stories are not refined (`epic-not-refined`).
- **Feature first.** A story is refined only when its `Feature group:` Feature is refined and not stale (`feature-not-refined #<feature>`); a story with no `Feature group:` is not refined (`no-feature`).
- **Decision blocker.** An issue with an open decision blocker is not refined (`open-decision-blocker`). An issue whose blocker the PO answered with `/unblock`, and whose latest refine Session Summary is older than `unblocked_at`, is resumed at step 2, also when the buffer is full.
- **Each refinement is started once.** An issue whose latest refine Session Summary is later than its Epic's `spec.committed_at` and later than any `unblocked_at`, and does not report `pass`, is not refined again (`last-refinement-failed`) until a new spec commit or an `/unblock`.
- **Stories in implementation.** A story in `--in-implementation` is refined only when it is stale (step 2); any other state of it is work in flight for step 1. It is never an implementation candidate.

## Order within a step

1. The Epic's `prio:` label: `P0` before `P1` before `P2`, no label last.
2. The earlier spec merge (`spec.committed_at`); an Epic with no merged spec comes after every Epic with one.
3. The issue's own `prio:` label, the same way.
4. The Feature's criterion prefix (`F1` before `F2` before `F10`); a story uses its Feature's prefix; none last.
5. The lower issue number.

## Ready buffer

`counts.ready` is the stories (Story, Task, Bug, across all Epics) that are ready and not in implementation; `counts.waiting` is those refined but not ready and not in implementation. Step 6 runs only when `ready < READY_BUFFER_MIN` and `ready + waiting < READY_BUFFER_MAX`; otherwise each step-6 candidate is skipped with `buffer-full`.

## WIP limit and file overlap

A story starts only while the stories in `--in-implementation` are fewer than `WIP_LIMIT_IMPLEMENTATION` (otherwise each candidate is skipped with `wip-limit`), and only when none of its `files_affected` is in the `files_affected` of a story in implementation (otherwise `file-overlap #<n> <path>`, and the next candidate is tried). A missing `## Files affected` section on either side means no overlap check for that pair, as in the dispatcher's WIP pre-flight today, and adds the note `files-affected-missing #<n>`.

## Skip reasons

| Reason | Meaning |
|---|---|
| `no-merged-spec: <facts reason>` | the Epic has no merged spec, so nothing under it is refined |
| `epic-not-refined` | the issue's Epic record is not `current` |
| `feature-not-refined #<n>` | the story's Feature is not refined, or is stale, or is not an open Feature |
| `no-feature` | the story has no `Feature group:` |
| `open-decision-blocker` | the issue waits for the PO's answer |
| `last-refinement-failed` | the latest refinement after the newest spec commit and any `/unblock` did not pass |
| `buffer-full` | the ready buffer does not allow story refinement or a Feature breakdown |
| `stories-left-to-refine #<n>` | the Feature is not broken down while story `#<n>` (the lowest) is left to refine |
| `wip-limit` | `WIP_LIMIT_IMPLEMENTATION` stories are in implementation |
| `file-overlap #<n> <path>` | the story shares `<path>` with story `#<n>` in implementation |
| `unreadable: <facts reason>` | the facts could not read the issue (an Epic whose comments failed shows `unreadable: comments`) |
