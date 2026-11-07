# Command: `hpw dispatcher status read`

Derives one board status for every open issue with a `type:` label of epic, feature, story, task or bug, and counts each Feature's and Epic's closed and remaining implementation issues, from GitHub facts alone (ADR-010: the board shows state; ADR-014: one building block, one JSON line). It writes nothing: no board status, label, comment or issue body, and it never reads the board. #476 writes what it derives. Code: `src/headless_pr_workflow/dispatcher/status.py`.

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

Precedence, first match wins. Facts come from [`facts read`](dispatcher-facts.md) unless stated.

| Status | Applies to | Fact |
|---|---|---|
| Done | all | The issue is closed. |
| Blocked | all | An open decision blocker (no later `/unblock` from the PO), or a `## Blocked Declaration` of type dependency, external, conflict or resource from a trusted author with no later trusted comment that has a line starting with `Unblocked`. |
| In specification | Epic; other types by gap rule 1 | An open PR adds or changes `specs/<epic>-*/spec.md`. For a Feature, story, task or bug: its Epic has such a PR, has no merged spec, and the issue is not refined. |
| No spec | all | Not refined and the Epic has no merged spec and no open spec PR; or the issue has no open parent Epic (gap rule 2). |
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
