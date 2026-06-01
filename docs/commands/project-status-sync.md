# Command Contract: `hpw project-status sync`

This document is the authoritative contract for the `hpw project-status sync` command. It is intended for implementors building Feature #111. It does not define new status semantics — all status names, transitions, and the fact-vs-intent distinction are defined in [`docs/PROJECT-STATUS.md`](../PROJECT-STATUS.md).

> **Risk note:** Feature #111 (prototype) is a soft dependency. If the prototype surfaces edge cases not covered by this contract, a follow-on update to this document will be required before those cases are considered in-scope for the command.

---

## Overview

`hpw project-status sync` reads GitHub Projects state and compares it against the expected workflow status derived from observable repository facts (branch existence, PR state, merge status). It reports detected transitions and, unless `--dry-run` is specified, mutates GitHub Projects status fields to match detected state.

**This command reflects fact; it does not act on intent.** See [Fact vs Intent Boundary](#fact-vs-intent-boundary).

---

## Minimum Viable Invocation

```
hpw project-status sync --repo <owner/repo>
```

All other flags are optional.

---

## Inputs

### Required flags

| Flag | Type | Description |
|---|---|---|
| `--repo <owner/repo>` | string | The GitHub repository to sync. Must be in `owner/repo` format. |

### Optional flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--dry-run` | boolean | false | Report what would change without mutating any GitHub Projects state. |
| `--json` | boolean | false | Emit all output as machine-readable JSON to stdout instead of human-readable summary. Compatible with `--dry-run`. |
| `--item <number>` | integer | (all items) | Scope sync to a single issue or PR number. Useful for debugging a specific story. |
| `--project <project-number>` | integer | (auto-detected) | Target a specific GitHub Projects board by number. If omitted, the command auto-detects the board linked to the repository. Fails with exit code 3 if no board is linked and this flag is omitted, or with exit code 4 if the repo is linked to more than one board and this flag is omitted. |

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `GH_TOKEN` or `GITHUB_TOKEN` | Yes | GitHub personal access token or app token with `repo` and `project` scopes. Checked in order: `GH_TOKEN` first, then `GITHUB_TOKEN`. |

The command does not read from `.env` files or local configuration. Token must be present in the environment before invocation.

### Required context

- The repository must exist and be accessible with the provided token.
- The repository must be linked to at least one GitHub Projects board (unless `--project` is specified).
- The token must have read access to the repository and write access to the linked project (write access is not required when `--dry-run` is set).

---

## Outputs

### Human-readable output (default)

Written to stdout. One line per item assessed. Format:

```
[SYNC]  #<number> <title>
        Current status : <current-status>
        Detected status: <detected-status>
        Action         : <transition | no change | skipped (<reason>)>
```

If no items have a detectable transition:

```
No status transitions detected. (0 changes)
```

If `--dry-run` is active, a header line is prepended:

```
DRY RUN — no GitHub state will be mutated.
```

And each action line reads `would transition` instead of `transition`:

```
        Action         : would transition  In implementation → In review
```

End-of-run summary line (always present):

```
Sync complete: <N> transition(s) applied, <M> item(s) unchanged, <K> item(s) skipped.
```

In dry-run:

```
Dry run complete: <N> transition(s) would be applied, <M> item(s) unchanged, <K> item(s) skipped.
```

### JSON output (`--json` flag)

Written to stdout as a single JSON object. No human-readable text is mixed into stdout when `--json` is active. Errors are also emitted as JSON (see [Failure Modes](#failure-modes)).

#### Schema

```jsonc
{
  "dry_run": boolean,           // true if --dry-run was set
  "repo": string,               // "owner/repo"
  "project_number": integer,    // GitHub Projects board number
  "items": [
    {
      "number": integer,        // issue or PR number
      "title": string,          // issue or PR title
      "current_status": string, // status value currently set in GitHub Projects
      "detected_status": string | null, // status derived from repo facts; null if undetectable
      "action": "transition" | "no_change" | "skipped",
      "skip_reason": string | null, // non-null when action == "skipped"
      "transition": {           // present only when action == "transition"
        "from": string,
        "to": string,
        "applied": boolean      // false in dry_run; true if mutation succeeded
      } | null
    }
  ],
  "summary": {
    "transitions_applied": integer,
    "unchanged": integer,
    "skipped": integer
  }
}
```

**Notes:**
- `action` is `"transition"` in both live and dry-run modes; `transition.applied` distinguishes them — `false` in dry-run, `true` if the mutation succeeded in live mode.
- All fields are required except `skip_reason` (null when `action != "skipped"`) and `transition` (null when `action != "transition"`).
- The top-level object is always emitted, even when `items` is empty.

---

## Side Effects

**The only GitHub state this command mutates is GitHub Projects status fields.**

Specifically:
- It reads GitHub Projects item status for each story in the linked board.
- It reads repository facts: branch existence, PR open/draft/ready/merged state, review approvals, CI check status.
- It writes a new status value to a GitHub Projects item **only when** a detectable transition is confirmed and `--dry-run` is not set.

**This command does not:**
- Open, close, or comment on issues or PRs.
- Create or delete branches.
- Trigger CI runs.
- Write to any file in the repository.
- Modify any GitHub Projects field other than the status field.
- Move items to statuses that are intent signals (`Ready for implementation`, `Ready to merge`) — these are set by human or PO action, not by sync.

---

## Transition Detection Rules

The sync command derives an expected status from observable repository facts. Each rule below is applied in order; the first matching rule wins. If no rule matches, `detected_status` is `null` and the item is skipped with reason `"no detectable state"`.

**These rules detect only fact statuses.** Intent-signal statuses (`Ready for implementation`, `Ready to merge`) are never written by this command. See [Fact vs Intent Boundary](#fact-vs-intent-boundary).

### Rule 1 — Done

**Condition:** The linked PR is merged.

**Detected status:** `Done`

**Notes:** A merged PR is the sole fact that confirms `Done`. The rule applies regardless of the current Projects status.

### Rule 2 — Needs rework

**Condition:** A linked PR is open, not merged, not draft AND the most recent review on the PR has status `CHANGES_REQUESTED`.

**Detected status:** `Needs rework`

**Notes:** "Most recent review" means the latest review event per reviewer. If any reviewer has requested changes and no subsequent approval supersedes it, the item is in `Needs rework`. This rule is evaluated before Rule 3 (In review) because it is more specific — `CHANGES_REQUESTED` state is a subset of the open-ready-not-merged conditions; placing the more specific rule first ensures `Needs rework` items are not mis-classified as `In review`.

### Rule 3 — In review

**Condition:** A linked PR exists AND the PR is marked ready for review (not draft) AND the PR is not merged AND it does not have an active approval that passes all required checks AND the most recent review per reviewer does not include an unresolved `CHANGES_REQUESTED` (i.e., Rule 2 did not match).

**Detected status:** `In review`

**Notes:** "Linked" means the PR body contains `Closes #<number>`, `Fixes #<number>`, or `Resolves #<number>` (case-insensitive), or the PR is manually linked to the issue via GitHub's issue tracker. A PR that is open but still in draft is not `In review` — see Rule 4.

### Rule 4 — In implementation

**Condition:** A branch matching the naming convention `*/issue-<number>-*` or `codex/issue-<number>-*` or `claude/issue-<number>-*` exists AND either no PR exists yet, or the linked PR is in draft state.

**Detected status:** `In implementation`

**Branch naming convention:** the prefix `*/` matches any prefix followed by `/issue-<number>-`. Exact prefix list: `codex/`, `claude/`, `feature/`, `fix/`. Additional prefixes may be supported; the rule matches if the branch name contains `issue-<number>-` after the first `/`.

**Notes:** Branch existence is checked against the remote (origin). Local-only branches are not sufficient to trigger this rule.

### Rule 5 — No detectable state

**Condition:** None of the above rules match (no branch, no PR, no review, not merged).

**Detected status:** `null`

**Action:** `skipped` with `skip_reason: "no detectable state"`.

---

## Failure Modes

Each failure mode maps to a specific non-zero exit code. When `--json` is set, errors are emitted to stdout as a JSON error object (see schema below). When `--json` is not set, errors are written to stderr and stdout may be partial.

### Error JSON schema

```jsonc
{
  "error": true,
  "code": integer,       // matches the exit code
  "message": string,     // human-readable description
  "detail": string | null // additional context, e.g. HTTP status or field name
}
```

### Exit codes

| Code | Name | Condition |
|---|---|---|
| `0` | Success | Sync completed without error. Zero or more transitions applied. Dry-run with zero or more detected transitions also exits 0. |
| `1` | Auth failure | `GH_TOKEN` / `GITHUB_TOKEN` is absent, invalid, or lacks required scopes. |
| `2` | Repository not found | The `--repo` value does not resolve to an accessible repository. |
| `3` | Project not found | No GitHub Projects board is linked to the repository AND `--project` was not specified; or the specified `--project` number does not exist or is not accessible. |
| `4` | Ambiguous project | The repository is linked to more than one GitHub Projects board and `--project` was not specified. |
| `5` | Item not found | `--item <number>` was specified but the issue or PR number does not exist in the repository. |
| `6` | Mutation failed | A status update to GitHub Projects failed after the transition was detected. One or more items may have been updated; partial results are reported. |
| `7` | Rate limit | GitHub API rate limit was hit during the sync run. Partial results may have been applied. |
| `8` | Unexpected error | An error occurred that does not match any of the above categories. The `detail` field contains additional context. |

**Partial results (codes 6 and 7):** When a sync run is interrupted partway through, the items that were successfully updated are reported normally. The items that were not updated are reported with `action: "skipped"` and `skip_reason: "mutation failed"` or `skip_reason: "rate limit"`. The exit code reflects the worst failure encountered.

---

## Dry-Run Behaviour

When `--dry-run` is set:

- All detection rules run exactly as in live mode.
- No GitHub Projects status fields are mutated.
- Output reports what **would** change, with a clear dry-run marker.
- The `transition.applied` field is `false` for all detected transitions.
- Exit codes 0–5 are still applicable (detection and auth errors are reported). Exit codes 6 and 7 cannot occur in dry-run mode (no mutations are attempted).

### Dry-run invariant

A dry-run with the same inputs as a live run must report the same set of detected transitions. The only difference between dry-run and live output is the mutation outcome.

---

## Worked Examples

### Example 1 — Detected transition (human-readable)

**Scenario:** Issue #42 has a PR that was just marked ready for review. Projects status is still `In implementation`.

**Command:** `hpw project-status sync --repo org/repo --dry-run`

**stdout:**
```
DRY RUN — no GitHub state will be mutated.

[SYNC]  #42 Story: Add retry logic to sync worker
        Current status : In implementation
        Detected status: In review
        Action         : would transition  In implementation → In review

Dry run complete: 1 transition(s) would be applied, 0 item(s) unchanged, 0 item(s) skipped.
```

**Exit code:** `0`

---

### Example 2 — No change detected (human-readable)

**Scenario:** Issue #17 is `In implementation`. A branch exists and the PR is in draft. Status is already correct.

**Command:** `hpw project-status sync --repo org/repo --item 17`

**stdout:**
```
[SYNC]  #17 Story: Document merge policy
        Current status : In implementation
        Detected status: In implementation
        Action         : no change

Sync complete: 0 transition(s) applied, 1 item(s) unchanged, 0 item(s) skipped.
```

**Exit code:** `0`

---

### Example 3 — Failure mode (human-readable)

**Scenario:** Token is missing from the environment.

**Command:** `hpw project-status sync --repo org/repo`

**stderr:**
```
Error: GitHub token not found. Set GH_TOKEN or GITHUB_TOKEN in the environment.
```

**stdout:** (empty)

**Exit code:** `1`

---

### Example 4 — Detected transition (JSON)

**Scenario:** Issue #42, same as Example 1, using `--json`.

**Command:** `hpw project-status sync --repo org/repo --dry-run --json`

**stdout:**
```json
{
  "dry_run": true,
  "repo": "org/repo",
  "project_number": 3,
  "items": [
    {
      "number": 42,
      "title": "Story: Add retry logic to sync worker",
      "current_status": "In implementation",
      "detected_status": "In review",
      "action": "transition",
      "skip_reason": null,
      "transition": {
        "from": "In implementation",
        "to": "In review",
        "applied": false
      }
    }
  ],
  "summary": {
    "transitions_applied": 0,
    "unchanged": 0,
    "skipped": 0
  }
}
```

**Exit code:** `0`

---

### Example 5 — No change detected (JSON)

**Scenario:** Issue #17, same as Example 2, using `--json`.

**Command:** `hpw project-status sync --repo org/repo --item 17 --json`

**stdout:**
```json
{
  "dry_run": false,
  "repo": "org/repo",
  "project_number": 3,
  "items": [
    {
      "number": 17,
      "title": "Story: Document merge policy",
      "current_status": "In implementation",
      "detected_status": "In implementation",
      "action": "no_change",
      "skip_reason": null,
      "transition": null
    }
  ],
  "summary": {
    "transitions_applied": 0,
    "unchanged": 1,
    "skipped": 0
  }
}
```

**Exit code:** `0`

---

### Example 6 — Failure mode (JSON)

**Scenario:** Token missing, `--json` set.

**Command:** `hpw project-status sync --repo org/repo --json`

**stdout:**
```json
{
  "error": true,
  "code": 1,
  "message": "GitHub token not found. Set GH_TOKEN or GITHUB_TOKEN in the environment.",
  "detail": null
}
```

**Exit code:** `1`

---

### Example 7 — Skipped item (no detectable state, JSON)

**Scenario:** Issue #99 has no branch and no PR yet.

**Command:** `hpw project-status sync --repo org/repo --item 99 --json`

**stdout:**
```json
{
  "dry_run": false,
  "repo": "org/repo",
  "project_number": 3,
  "items": [
    {
      "number": 99,
      "title": "Story: Define rollback procedure",
      "current_status": "Ready for implementation",
      "detected_status": null,
      "action": "skipped",
      "skip_reason": "no detectable state",
      "transition": null
    }
  ],
  "summary": {
    "transitions_applied": 0,
    "unchanged": 0,
    "skipped": 1
  }
}
```

**Exit code:** `0`

---

## Fact vs Intent Boundary

This command **only writes fact statuses**. It never writes intent-signal statuses.

For full definitions, see [`docs/PROJECT-STATUS.md` — Fact vs Intent Distinction](../PROJECT-STATUS.md#fact-vs-intent-distinction).

The relevant boundary for this command:

| Status | Type | Written by sync? | Reason |
|---|---|---|---|
| `In implementation` | Fact | Yes | Branch or draft PR detected |
| `In review` | Fact | Yes | PR marked ready for review |
| `Needs rework` | Fact | Yes | Latest review requests changes |
| `Done` | Fact | Yes | PR merged |
| `Ready for implementation` | Intent signal | **No** | Set by PO when dependencies resolve |
| `Ready to merge` | Intent signal | **No** | Set by reviewer when approval + checks pass |
| `Refined` | Fact | **No** | Set by PO during refinement |
| `In refinement` | Fact | **No** | Set by PO during refinement |
| `Blocked` | Fact | **No** | Set by executor when a blocker is declared |
| `Backlog` | Fact | **No** | Set on item creation |

**Why this boundary matters:** The sync command reads repository state. Repository state cannot confirm that dependencies are resolved (an intent judgment) or that a review fully passes product criteria (a judgment). Sync writes only what is observable and unambiguous from repository facts. Any transition that requires human judgment is outside the sync command's authority.

---

## Flag Interactions

| Combination | Behaviour |
|---|---|
| `--dry-run` alone | Detect transitions, report, do not mutate. |
| `--json` alone | Live sync, JSON output. |
| `--dry-run --json` | Detect transitions, report as JSON, do not mutate. |
| `--item N --dry-run` | Detect transition for item N only, do not mutate. |
| `--item N --json` | Live sync for item N only, JSON output. |
| `--project P --dry-run` | Scoped to project P; dry-run applies. |

There are no mutually exclusive flag combinations. All flags may be combined.

---

## Consistency with `docs/PROJECT-STATUS.md`

This contract was authored against `docs/PROJECT-STATUS.md` as of the state that closes Feature #109. If `docs/PROJECT-STATUS.md` is updated after this contract is merged, this document must be reviewed for consistency before the next implementation story (Feature #111) is pulled.

Status names used in this document match exactly the names defined in `docs/PROJECT-STATUS.md`. No new status names are introduced here.
