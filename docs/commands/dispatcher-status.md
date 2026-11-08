# Commands: `hpw dispatcher status read` and `hpw dispatcher status sync`

`status read` derives the statuses; `status sync` (below) writes them. The sections up to "Statuses" describe `read`.

Derives one board status for every open issue with a `type:` label of epic, feature, story, task or bug, and counts each Feature's and Epic's closed and remaining implementation issues, from GitHub facts alone (ADR-010: the board shows state; ADR-014: one building block, one JSON line). `read` writes nothing: no board status, label, comment or issue body, and it never reads the board. [`status sync`](#status-sync) writes what it derives. Code: `src/headless_pr_workflow/dispatcher/status.py`.

```
hpw dispatcher status read --repo <owner/repo> [--epic <n>] [--dry-run]
```

Python: `derive_status(facts) -> (status, fact)` and `count_progress(items)` are pure (no gateway, no model, no write; the same facts give the same result). `read_status(gateway, repo, *, epic=None, env=None) -> Result` collects the facts with read calls only and returns what the command prints.

- `--epic <n>`: only that Epic and the issues under it, like [`facts read`](dispatcher-facts.md).
- `--dry-run` is accepted and changes nothing: the command has no write to skip.

Environment: `GH_TOKEN` (through `gh`; the merge gates need GraphQL access) and `DECISION_BLOCKER_PO_LOGIN` (passed to the facts reader and used for the trusted-author rule).

## Exit codes

| Code | Result | Meaning |
|---|---|---|
| `0` | `read` | the statuses are derived |
| `2` | `invalid` | invalid arguments |
| `3` | `unreadable` | the open-issue list, the open-PR list or the facts could not be read |

## Output

One JSON line on stdout: `{"ok", "block": "status", "operation": "read", "result", "items", "progress", "error"}`.

- `items`: one `{"number", "type", "status", "fact"}` per open issue with a known type, sorted by number. `fact` is a one-line reason. `status` is one of the table below, or `unknown` when a fact of that item could not be read (the `fact` then names the step); the rule never guesses a status.
- `progress`: `{"epics": {"<n>": {"features_closed", "features_total", "issues_closed", "issues_total"}}, "features": {"<n>": {"closed", "total"}}}`, counted from the `Parent epic: #N` and `Feature group: #N` lines of the open and the closed issues; implementation issues are story, task and bug. A closed issue counts as closed whatever its close reason. `null` when the closed issues could not be read (the statuses are still given).
- A closed issue with a known type is `Done` from its state alone (`derive_status` with `state: closed`); the reader lists open issues only.

Stderr: one `[STATUS] #<n> <type> <status>: <fact>` line per item. Tokens are masked.

## Statuses

Precedence, first match wins: Done, Blocked, In specification, No spec, Refinement failed, Not refined, then the rest. Facts come from [`facts read`](dispatcher-facts.md) unless stated.

| Status | Applies to | Fact |
|---|---|---|
| Done | all | The issue is closed. |
| Blocked | all | An open decision blocker (no later `/unblock` from the PO), or a `## Blocked Declaration` of type dependency, external, conflict or resource from a trusted author with no later trusted comment that has a line starting with `Unblocked`. |
| In specification | Epic; other types by gap rule 1 | An open PR adds or changes `specs/<epic>-*/spec.md`. For a Feature, story, task or bug: its Epic has such a PR, has no merged spec, and the issue is not refined. |
| No spec | all | Not refined and the Epic has no merged spec and no open spec PR; or the issue has no open parent Epic (gap rule 2). |
| Refinement failed | all | The scheduler's `last-refinement-failed` fact: the issue's latest refine Session Summary, created after the current spec commit, reports `refine-check` fail or unreadable, and no `/unblock` follows it. `status.py` calls the scheduler's rule (`_refine_gate`); a parity test guards it. |
| Not refined | all | Not refined with the Epic's spec merged: never refined, or stale after a scope change. Also with an open PR. |
| Mergeable | story, task, bug | An open, non-draft linked PR whose merge gates pass on its current head. |
| In review | story, task, bug | An open, non-draft linked PR whose gates do not all pass, or cannot be read. |
| In implementation | story, task, bug | An open linked PR in draft. |
| Ready to implement | story, task, bug | Refined, every hard dependency closed, no open linked PR. |
| Refined | story, task, bug | Refined and a hard dependency is open or unreadable. |
| In progress | Epic, Feature | Refined and not closed. |

An Epic or a Feature never gets Refined, Ready to implement, In implementation, In review or Mergeable, whatever its facts. A refined story under an Epic with no merged spec keeps its implementation statuses.

Gap rules (the table of #461 leaves these open):

1. An issue that is not refined whose Epic has an open spec PR and no merged spec is `In specification`.
2. An issue with no `Parent epic:` line, or whose parent is not an open Epic, is `No spec`, unless a story, task or bug has an open linked PR: then `Mergeable`, `In review` or `In implementation` apply.
3. A board item that is not an issue with a known `type:` label gets no derived status (`unknown`), so the writer leaves it alone.

An Epic is refined when its spec record is current (if the spec is merged), its latest refine Session Summary passes, and no decision blocker is open or newer than that summary.

## Linked PR and merge gates

A linked PR is an open PR whose body says `closes`, `fixes` or `resolves #N` (case-insensitive, whole number), the rule of `find_linked_pr` in `scripts/dispatcher-invoke.sh`. With several, the most advanced status wins. The merge gates are the evaluation of `scripts/merge-gate-summary` (loaded as a module, no second copy), asked once per non-draft linked PR on its current head. A gate result that cannot be read, or that was read for another head, counts as not passing: `In review`, never `Mergeable`.

## Reads

Open issues and comments (through `read_facts`), open PRs (all pages), the files of each open PR (only when an open Epic exists), closed issues (all pages), and one gate evaluation per non-draft linked PR. When the files of an open PR cannot be read, the items under an Epic get `unknown`.


## status sync

Writes the derived status to every board item whose Status differs, and keeps each open Epic's progress section current. It is the only code that writes board status (ADR-010). Nothing calls it yet; the dispatcher does in #478. Code: `src/headless_pr_workflow/dispatcher/board_sync.py`.

```
hpw dispatcher status sync --repo <owner/repo> [--epic <n>] [--attempts <n>] [--dry-run]
```

- `--epic <n>`: only that Epic, the issues under it, and the board items among them.
- `--attempts <n>`: write attempts per item (default 3, as `write board-status`).
- `--dry-run`: reads, derives and lists every difference, and writes nothing (no board write, no issue-body write).

The command does not read `DISPATCHER_ENABLED`; the dispatcher decides whether to call it.

### What it does

1. Reads the statuses with the rule above (`read_status`), the board (every item of the repository's first Projects v2 board, all pages), and the open and closed issues.
2. For each board item that is an issue with a known `type:` label, compares the derived status with the board's Status. A closed issue is `Done` from its state alone. A difference includes a card moved by hand. Each is written through `write_board_status` (write, read back, up to `--attempts`); an item that already matches is not written. One item that fails does not stop the others.
3. An item whose status is `unknown` is left alone and listed in `skipped` with the reason (`unknown: <reason>`). An open typed issue that is not on the board is listed as `not_on_board` and never added. Items that are not issues, and issues without a known type, are not touched or listed.
4. For each open Epic, builds the progress section and writes the Epic body only when it differs.

The board option names are not created or renamed here (#478). A derived status that is not a Status option fails that item (`resolve status: ... is not a Status option`); with `--dry-run` it is listed with `missing_option: true`.

### Progress section

Between `<!-- hpw:progress:start -->` and `<!-- hpw:progress:end -->`, with a `## Progress` heading inside:

```
<!-- hpw:progress:start -->
## Progress

- #<n> <title>: <status>, <closed> of <total> implementation issues closed

Features: <closed> of <total> closed; implementation issues: <closed> of <total> closed
<!-- hpw:progress:end -->
```

One row per Feature (open or closed; status from the rule, counts from `count_progress`), then the Epic line. It has no date or run id, so unchanged facts give an identical section and no write. If the markers are absent the section is appended once at the end of the body. Everything outside the markers is never touched. The write uses the body read immediately before it and is read back: the body must equal what was sent. Markers that are duplicated, out of order or only one of the pair: nothing is written and that Epic is `failed`. A closed Epic is not written.

### Output and exit codes

One JSON line on stdout: `{"ok", "block": "status", "operation": "sync", "result", "corrections", "skipped", "progress", "dry_run", "error"}`.

- `corrections`: `{"issue", "type", "before", "after", "fact", "result", "error"}` per difference; `result` is `written`, `dry-run` or `failed`; a dry run adds `missing_option`. `before` is `null` for a card with no Status.
- `skipped`: `{"issue", "reason"}`.
- `progress`: `{"epic", "result", "error"}` per open Epic; `result` is `written`, `already`, `dry-run` or `failed`.
- `error`: `null` or `<step>: <reason>` of the first failure, tokens masked.

| Code | Result | Meaning |
|---|---|---|
| `0` | `synced` | every difference written and verified, or none |
| `0` | `dry-run` | `--dry-run`: differences listed, nothing written |
| `1` | `failed` | at least one board or Epic-body write failed |
| `2` | `invalid` | invalid arguments |
| `3` | `unreadable` | the board, the issues, the facts or an Epic body could not be read; nothing was written |

Stderr (the run log): one `[SYNC] #<n> <before> -> <after>: <fact>` line per correction (`(none)` for a card with no Status), one `[SYNC] progress #<epic>: <result>` line per Epic, one `[SYNC] skipped #<n>: <reason>` line per skipped item. A run is idempotent: right after a run, `--dry-run` lists no correction and no progress change.

Environment: `GH_TOKEN` (through `gh`; `PROJECT_TOKEN`-class access to the board and issue bodies) and `DECISION_BLOCKER_PO_LOGIN`.
