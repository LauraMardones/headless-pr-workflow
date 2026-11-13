# Command: `hpw dispatcher facts read`

Reads, from GitHub alone, the facts the scheduler needs to choose the next refinement or implementation action, and derives three facts per issue: **refined**, **ready** and **stale**. It never writes to GitHub (ADR-010: decisions come from facts, never from board status; ADR-014: one building block, one JSON line). Code: `src/headless_pr_workflow/dispatcher/facts.py`.

```
hpw dispatcher facts read --repo <owner/repo> [--epic <n>] [--dry-run]
```

Python, same result as the command prints: `read_facts(gateway, repo, *, epic=None, env=None) -> Result`.

`--epic <n>` limits the output to that Epic and the issues whose `Parent epic:` is `#n`. `--dry-run` is accepted and changes nothing: the command has no write to skip.

Environment: `GH_TOKEN` (through `gh`), `DECISION_BLOCKER_PO_LOGIN` (the PO's login; `/unblock` counts only from it). When it is empty, no `/unblock` counts and a decision blocker stays `open`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | read. One issue that cannot be read is reported on that issue and stays exit `0`. |
| `2` | invalid arguments |
| `3` | the open-issue list, the `specs/` folder or a spec file's history could not be read (`epics` and `issues` are empty) |

Exit `1` is not used.

## Output

One JSON line on stdout: `{"ok", "block": "facts", "operation": "read", "result": "read" | "unreadable", "epics", "issues", "error"}`. Log lines go to stderr; tokens are masked.

Each Epic: `number`, `title`, `prio`, `spec`, `record`, `stale`, `refinement`, `decision_blocker`. The last two follow the rules below for a Feature or story; both are `null` when the Epic's comments cannot be read (stderr names the step).

- `spec.state`: `merged` (with `path`, the newest `commit` on `main` that changed it and its `committed_at`), `missing` (no `specs/<epic>-*/spec.md` on `main`) or `ambiguous` (more than one). The last two carry a `reason` naming `specs/<epic>-*/spec.md`.

Each Feature or story (Story, Task, Bug): `number`, `title`, `type`, `prio`, `epic`, `feature` (the `Feature group:` of a story), `criterion_prefix` (Feature only), `record`, `refinement`, `decision_blocker`, `dependency_blocker` (stories), `dependencies` (stories), `files_affected`, `refined`, `ready`, `stale`, `reasons`. `prio` is the `prio:` label.

## Facts

**`record.state`** compares the issue's `Spec:` line (the format `scripts/refine-check` checks) with the Epic's newest spec commit through GitHub compare:

| State | When |
|---|---|
| `current` | the line names the Epic's spec path at a commit equal to, or descending from, the newest spec commit |
| `stale` | the commit is older (`behind` or `diverged`) |
| `missing` | no `Spec:` line |
| `invalid` | malformed line, another path, or a commit GitHub cannot find |
| `unchecked` | the line is valid, but the Epic has no merged spec to compare with |

**`refinement`** is the latest refine Session Summary (`## Session Summary` with `Command: refine` or `Command: refine-<type>`): `created_at`, `url` and `refine_check` (`pass`, `fail`, `unreadable`, or `none` when there is no summary).

**`decision_blocker`** is the latest `## Blocked Declaration` with `Type: decision`: `state` `none`, `open` or `unblocked` (with `unblocked_at`). It is `unblocked` when a later comment by `DECISION_BLOCKER_PO_LOGIN` has a line that is exactly `/unblock`.

**`dependency_blocker`** (stories only; `null` otherwise) has the same shape for a `## Blocked Declaration` with `Type: dependency`. It is `unblocked` when a later `/unblock` line by `DECISION_BLOCKER_PO_LOGIN` exists, or a trusted `Unblocked` comment (the Blocked Status Protocol's resolution line) follows it, or a passing refine Session Summary is later than the declaration. While it is `open` the story is not ready (`open-dependency-blocker`); `refined` is unchanged, so the story counts as refined-but-waiting.

**Trusted authors.** `refinement`, `decision_blocker` and `dependency_blocker` count only comments by `DECISION_BLOCKER_PO_LOGIN`, an `OWNER`, `MEMBER` or `COLLABORATOR`, or a Bot (the rule of `latest_decision_blocker_json`). Any other author is ignored.

**`dependencies`** lists each issue under `Hard depends on:` as `closed`, `open` or `unresolvable` (not found, or its read failed); `None.` means none. `dependencies.state` is `unreadable` when the `## Dependencies` section or the line is missing. A close reason is not used: closed is closed.

**`files_affected`** (Feature or story) lists the paths under `## Files affected`, parsed as `extract_files` in `scripts/dispatcher-invoke.sh` does: the section runs from the first line starting with `## Files affected` to the next line starting with `##`, each `- ` item loses its backticks and a `(note)`. It is `null` when the section is missing and `[]` when it has no items. `hpw dispatcher scheduler next` uses it for the file-overlap check ([dispatcher-scheduler.md](dispatcher-scheduler.md)).

**`refined`** (Feature or story) is true only when all hold:

1. under an Epic with spec `merged`, `record.state` is `current` (under `missing` or `ambiguous` no record is required);
2. the latest refine Session Summary reports `pass`;
3. the decision blocker is not `open`;
4. when the blocker is `unblocked`, that summary is later than `unblocked_at`.

**`ready`** is true only for a Story, Task or Bug that is refined, whose hard dependencies are all `closed` (or that has none), whose `Hard depends on:` has no entry without an issue number (other than a `none` entry, text on the label line included), and whose `dependency_blocker` is not `open`. An Epic or Feature is never ready. A story becomes ready on the first read after its last hard dependency closes.

**`stale`** is true for an Epic, Feature or story whose Epic has spec `merged` and whose `record.state` is `stale` or `invalid`. Under an Epic with no merged spec it is false.

## Reasons

Every fact that does not hold carries a reason in `reasons`: `record-<state>`, `no-passing-summary`, `open-decision-blocker`, `summary-before-unblock`, `blocked-by #N`, `unresolvable #N`, `dependencies-unreadable`, `dependency-without-issue`, `open-dependency-blocker`. An issue that cannot be placed or read has one reason and is never refined, ready or stale: `no-parent`, `parent-not-open-epic`, `unreadable: <step>` (body, comments, or a failed compare).

Stderr has one line per issue, `[FACTS] #<n> <type> refined=<yes|no> ready=<yes|no> stale=<yes|no> <reasons>`, one line per Epic without a merged spec naming the expected path, and one line naming the failed step when the result is `unreadable`.
