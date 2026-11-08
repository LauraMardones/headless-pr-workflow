# ADR-014: Dispatcher GitHub Writes, Markers and Saved State Go Through Three Python Building Blocks

**Status:** Accepted
**Date:** 2026-11-05
**Related:** #431, #368 (F1), #418, #432, #433, #434, #328, #338, #341, #342, #348, #406, ADR-002, ADR-010, ADR-011, ADR-013
**Effective when:** each building block takes effect when its story merges: the verified write with #432, the markers with #433, saved state with #434. Until then the current scripts, `docs/DISPATCHER-CONFIG.md` and the other `docs/*.md` describe how the dispatcher works, and they change with those stories.

## Context

Dispatcher stories need many review rounds. The findings repeat three classes (#368 → Why): writes that are not checked and evidence that is lost (#328, #338, #342), markers trusted from any comment author (#328, #342), and state lost when a run is cancelled or re-run (#338, #341). Each script writes its own version of "write, read back, retry, fail closed, post evidence", so each new story repeats the old mistakes in a new place. Logic inside `.github/workflows/dispatcher.yml` is not reached by pytest at all.

Epic #418 delegates the technical approach, including the choice between bash and Python, to the Tech Lead (`specs/418-fewer-reviews-reliable-tests/spec.md` → Delegated to the Tech Lead). The three building-block stories #432, #433 and #434 need the language and the interfaces fixed before they can be refined. The inventory below was taken from `main` at `976e95f` (after #409 and #406); line numbers move, function names are the stable reference.

## Decision

### 1. Where the logic lives: a split

- **Python** (`src/headless_pr_workflow/dispatcher/`, run through `scripts/hpw dispatcher ...`) holds the three building blocks below, and any new rule that decides from GitHub facts what the dispatcher does.
- **Bash** (`scripts/dispatcher-*.sh`) keeps the control flow: step order, session starts, Slack calls through `slack-notify.sh`, exit codes and `DISPATCH_HANDLED`. Existing bash decision rules (for example `conflict_round_rule`) stay in bash; a story that rewrites one moves it to Python. Nothing else moves (#368 → Out of Scope).
- **`dispatcher.yml`** only calls scripts and runs the `actions/cache` steps (#368 → F6). Expressions that decide something move into a script with #434.

`hpw` already works this way in the dispatcher: `dispatcher-merge.sh` calls `hpw merge-owner --json` and `hpw merge-pr --json` and reads the exit code and the JSON. `codex_review_bookkeeping.py` already writes comments, board statuses and markers from Python and fails when GitHub does not confirm a write.

### 2. Call contract between bash and Python

Every building block has the same contract:

- **Call:** `"$HPW_BIN" dispatcher <block> <operation> --repo <owner/repo> [options] [--dry-run]`. `HPW_BIN` defaults to `scripts/hpw`, as in `dispatcher-merge.sh`.
- **Transport:** `gh` through `subprocess`, with the token `gh` already uses (`GH_TOKEN`, which is `PROJECT_TOKEN` in the workflow). No other HTTP client. The token and request headers are never printed.
- **stdout:** exactly one JSON object on one line, on success and on failure: `{"ok": <bool>, "block": "...", "operation": "...", "result": "<word>", ..., "error": "<step>: <reason>" | null}`. The result words for each operation are listed below.
- **stderr:** log lines for the dispatcher log only.
- **Exit codes:** `0` the answer is yes (written, holds, read); `1` the answer is no (not written, does not hold, not usable); `2` invalid arguments; `3` could not be decided, because a read failed, an account could not be resolved or a response could not be parsed. Write operations never return `3`: a write that cannot be verified is reported as `1`.
- **`--dry-run`:** nothing is written. The JSON names the write that would have happened, `result` is `dry-run`, and the exit code is `0`.
- **Bash shims:** each bash function that callers use today (for example `post_comment`, `marker_holds`) becomes a thin shim that calls the block. Call sites and tests keep their names where possible. "No behaviour change" means existing dispatcher tests pass without changing what they assert; only how they reach GitHub may change, for example a new fake (#368 → Assumptions).

### 3. The three building blocks

#### Block 1: verified GitHub write (`hpw dispatcher write`, #432)

| | |
|---|---|
| **Operations** | `comment --number <n> --body-file <path>`; `board-status --issue <n> --status "<name>"`; `label --number <n> (--add \| --remove) <name>`; `retry-pending --target <n> --item <item> --ref <ref> --story <n> [--expected-head <sha>]` |
| **Inputs** | The target and the content. `--attempts <n>` (default 3) and `--evidence-item <item> --evidence-head <sha\|->` for a comment that is durable evidence (#348). |
| **What it does** | Writes, then reads the result back from GitHub. For a comment, the dispatcher's own account wrote it and its body is the text that was sent. For a board status, the item's Status is the requested name (as `scripts/set-project-status` does today). For a label, the issue has (or no longer has) the label. When the read-back shows nothing was written, it retries. Before it retries a comment, it looks for an identical own comment created since the first attempt, so a write whose response was lost is not posted twice. |
| **Result** | `written`, `already` (nothing to change), `pending` (the write failed and the evidence-pending comment was left), `failed`, `dry-run`; plus `url`, `created_at`, `attempts`, `before`/`after` and `sent` (a status mutation was sent, so a failed write may still have changed the board) for a status. `retry-pending` adds `none`, `posted`, `stale`, `missing` and `gave-up` (the existing `evidence_retry` bound and give-up blocker, `EVIDENCE_RETRY_MAX`). |
| **Exit codes** | `0` written, already or dry-run; `1` pending, failed or missing; `4` (`retry-pending` only) retrying stopped until the PO's `/unblock`; `2` invalid arguments. |
| **Evidence it posts** | A comment write is its own evidence. With `--evidence-item`, a write that still fails leaves the evidence-pending comment of #348 (`scripts/lib/evidence-pending.sh` format), and a later run posts it with `retry-pending`. Board and label writes post nothing; the board is the record (ADR-010), and the caller names the change in its own comment or Session Summary. |
| **Fail-closed result** | Exit `1` means "not done". The caller does not record the step as done, does not move the story on, and keeps a state the next run can retry. A caller may ignore exit `1` only for a write listed as best-effort in the inventory below. |

#### Block 2: markers under the dispatcher's own account (`hpw dispatcher marker`, #433)

| | |
|---|---|
| **Operations** | `write --number <n> --action <action> [--field key=value ...] [--body-file <path>] [--once]`; `holds --number <n> --marker <text-or-prefix> [--cancel <marker>] [--author dispatcher\|bookkeeping]`; `list --number <n> [--marker <m>] [--author dispatcher\|bookkeeping]`; `whoami` |
| **Inputs** | The issue or PR, the marker (format unchanged: `<!-- hpw:dispatcher action=<action> key=value ... -->`, so markers already on GitHub stay valid), and the account whose markers count. |
| **What it does** | Resolves each account's login once per process: `dispatcher` is the login behind `GH_TOKEN` (or `DISPATCHER_LOGIN`), `bookkeeping` is `CODEX_BOOKKEEPING_LOGIN` or the same login. A marker counts only on a comment whose `user.login` is that login. A PO `/unblock` line counts only from `DECISION_BLOCKER_PO_LOGIN` (the `marker_holds` rule of #262 and #293). `write` posts through Block 1; with `--once` it posts nothing when the same marker already holds. |
| **Result** | `holds`: `holds`, `absent`, `unblocked`, `cancelled` or `unreadable`, with the comment's `url` and `created_at`. `write`: Block 1's result words. `list`: the matching own comments (`id`, `created_at`, `body`). `whoami`: the resolved logins. |
| **Exit codes** | `holds`: `0` holds, `1` does not hold, `3` unreadable. `list`/`whoami`: `0` read, `3` unreadable. `write`: as Block 1. `2` invalid arguments. |
| **Evidence it posts** | The marker comment itself: the hidden marker and the visible text the caller gives (for example a Blocked Declaration). |
| **Fail-closed result** | A login that cannot be resolved, or comments that cannot be read, give `unreadable` (exit `3`). They never match any author. The bash shim needs the caller's fail-closed direction as an explicit argument (today's `<on-read-error>` of `marker_holds`, made mandatory). That direction must start no session, post no second blocker, write no board status and merge nothing. |

#### Block 3: saved state (`hpw dispatcher state`, #434)

| | |
|---|---|
| **Operations** | `read --name <name>`; `write --name <name> --data-file <path>`; `read-pending --number <n> --item <item> [--expected-head <sha>]` |
| **Inputs** | A registered state name. File state: `actionable-events` (`.dispatcher-state/<owner>/<repo>/`; `notified-refinement` was retired with the ready-for-refinement notification by #474), `usage-sessions` and `usage-notified` (`.dispatcher-usage/`), `allowance-hold` (`.dispatcher-allowance/hold.json`). Comment state: the hidden payload of the dispatcher's own evidence-pending comment, found through Block 2, and the `evidence:pending` label that indexes it. |
| **What it does** | `read` checks the file against its current format (formats and cache keys keep their meaning, #368) and never changes it. `write` checks the data, writes a temporary file, renames it into place and reads it back. A path that is not a regular file, or a file that `read` found unreadable, is moved aside to `<file>.unreadable` and never overwritten silently (today's `save_state` rule in `dispatcher-notify-retry.sh`). `read-pending` decodes the payload and rejects a payload recorded for another head. |
| **Result** | `read`: `ok`, `missing` (with the name's empty value), `malformed`, `stale` (another day), `unreadable`, plus `data`. `write`: `written` or `failed`. `read-pending`: `none`, `ok`, `stale-head`, `malformed`, `unreadable`, plus `payload`. |
| **Exit codes** | `read`/`read-pending`: `0` ok, missing or none; `1` malformed or stale; `3` unreadable. `write`: `0` written, `1` failed. `2` invalid arguments. |
| **Evidence it posts** | None on GitHub. A failure is one stderr line naming the file and the step. A state that cannot be saved makes the step red, as `dispatcher-notify-retry.sh` does today (#352). |
| **Fail-closed result** | Bad state is never used as if it were good and never overwrites good state. A missing file is a normal cache miss and gives the empty value. For a malformed or unreadable file, each name has one fixed result: `actionable-events` sends nothing in the retry step and leaves the file as it is, and a new event is delivered directly instead of stored (as today); `allowance-hold` probes again (as today); `usage-notified` notifies again (as today); `usage-sessions` **starts no session**, with a `[USAGE LIMIT ERROR]` line. That last one is the only result that changes: today a malformed counter reads as 0 sessions. |

**Workflow rules that belong to Block 3** (in `dispatcher.yml`, enforced by #434's change-check rule):

1. Every `actions/cache/save` key ends in `${{ github.run_id }}-${{ github.run_attempt }}`, so a re-run never collides with the first attempt (#341). Restores match by prefix, so the newest snapshot wins.
2. State is saved right after the step that changed it. A final save runs with `if: always() && <guards>`, so it also runs after a failed or cancelled step; a save never depends on `!cancelled()` alone (#338).
3. Only the state writer (the default branch) saves. Other refs read the default-branch snapshot and run with `--dry-run` (unchanged).

### 4. Finding classes each block prevents

| Block | Finding classes it prevents | Evidence |
|---|---|---|
| 1. Verified write | A board write that only warns while the step still reports success; a durable evidence comment whose failure only warns, so the evidence is lost; an error report taken as success; a retry that posts a write twice | #328 ("Retry a failed In review transition after hand-off"), #342 ("Keep merge pending until confirmation evidence is posted", "Reject post-merge-sync error reports"), #338 ("Propagate actionable-state write failures") |
| 2. Own-account markers | A marker trusted from any comment author, so a forged marker stops a fix round or writes a board status; ownership read from any commenter's comment, or its fields from two different comments; an account that cannot be resolved treated as a match | #328 ("Authenticate comments carrying dispatcher markers"), #342 ("Bind handoff evidence to the actual dispatcher owner", "Bind ownership fields to one implement handoff", "Keep a failed owner-marker write retryable") |
| 3. Saved state | State lost because a cancelled run skipped the save; malformed or unreadable state overwritten with an empty copy; a cache key reused by a re-run; a counter write failure that lets a session start; state saved under the wrong ref or day | #338 ("Persist actionable state before cancellation skips the save", "Preserve pending events when rejecting malformed state"), #341 ("Make cache keys unique across workflow re-runs", "Fail closed when the session counter cannot be written", "Save the counter under the day it actually records") |

#355 was checked too. Its findings are about routing in the command files, not these classes, so it is not evidence for any block.

### 5. Change-check rules

`scripts/dispatcher-change-check.sh` gets one new check per block. The story that builds the block adds the rule, with a test that fails on a hand-rolled variant. Each rule prints `PASS|FAIL <file> <check-id> - <reason>` like the existing checks (`executable slack-guard dry-run stdout-json dispatch-handled`).

| Check id | Added by | Flags, outside the block's own shim functions |
|---|---|---|
| `verified-write` | #432 | `gh api` with `-X`/`--method` `POST`, `PATCH`, `PUT` or `DELETE`, or with `-f body=` or `--input`; `gh api graphql` whose query holds `mutation`; `gh issue\|pr comment\|edit\|close\|reopen\|ready\|merge\|review`; `gh label`; `curl` with a write method |
| `own-marker` | #433 | A literal `hpw:dispatcher` or `hpw:codex-review-bookkeeping`; a `gh api user` or `/user` call; a jq filter on `.user.login`; a match on a visible marker text read from comments (for example `DECISION_BLOCKER_CLAIM_MARKER`) |
| `saved-state` | #434 | A redirect, `cp`, `mv`, `rm`, `cat`, `read <` or `mkdir` on a path under `.dispatcher-state`, `.dispatcher-usage` or `.dispatcher-allowance`, or on a variable named `*STATE_DIR`, `*STATE_FILE`, `HOLD_FILE`, `SESSIONS_FILE` or `NOTIFIED_FILE`. #434 also makes `.github/workflows/dispatcher.yml` eligible and flags there an `actions/cache/save` key without `github.run_attempt`, a state save whose `if:` has no `always()`, and a `run:` step that calls `gh` directly |

Exceptions are an allowlist inside the change-check script, one entry (function name and check id) per "stays" row in the inventory that the rule would otherwise flag. Adding an exception means amending this ADR.

### 6. Adoption rule

- New dispatcher code that writes to GitHub, writes or reads a marker, or reads or writes saved state calls the block. If the block lacks an operation, the story adds it to the block with a test instead of writing its own version.
- A PR that changes `scripts/dispatcher-*.sh`, a `scripts/lib/*.sh` file they source, or `dispatcher.yml` runs `bash scripts/dispatcher-change-check.sh --files <changed files>` (the existing `/implement` self-review step). The `/implement` Session Summary `--checks` names the result for each check id, for example `dispatcher-change-check: verified-write PASS, own-marker PASS, saved-state PASS`. A `FAIL` blocks the hand-off, as today.
- A reviewer treats a new hand-rolled variant that the change-check missed as a Blocker and asks for a rule fix in the same PR.
- Until a block's story merges, its check id does not exist, and the Session Summary names only the checks that do.

### 7. Inventory of hand-rolled variants

Source: `main` at `976e95f`. **B1** = verified write, **B2** = own-account markers, **B3** = saved state. The ten functions named in #431 are in bold.

`scripts/dispatcher-invoke.sh`:

| Variant (line) | Block | Note |
|---|---|---|
| **`post_comment`** (449) | B1 | Moved (#432): the shim for `write comment`; sets `LAST_POSTED_AT` from the block's `created_at` |
| **`set_project_status`** (915) | B1 | Moved (#432): the shim for `write board-status`; returns 1 with a `[BOARD ERROR]` line when the status is not written |
| **`set_project_status_checked`** (2404) | B1 | Moved (#432): calls `set_project_status` |
| **`set_project_status_ready`** (3477) | B1 | Moved (#432): calls `set_project_status` with "Ready for implementation" |
| **`evidence_retry`** (1183) | B1 | Moved (#432): the shim for `write retry-pending`, which reads its payload through B3 `read-pending` (#434); the shim posts the give-up blocker through `post_blocker_once` (B2) |
| `evidence_record_failure` (1157) | B1 | Moved (#432): `write retry-pending` counts the failed run and reports the bound itself. The function stays for its two other callers (`merge-confirmation-missing`, `cleanup-summary-unrecorded`); its writes go through `post_comment`, and its marker reads are B2 (#433) |
| `settle_merge_confirmation` (2215) | B1 | Moved (#432): its writes go through `evidence_retry` and `evidence_record_failure` |
| `label_pending_cleanup` (1238) | B1 | Moved (#432): the shim for `write label --add`, which creates a missing repository label |
| `retry_pending_cleanup_summaries` (1253) | B1 | Moved (#432): `write label --remove`. **Best-effort:** a refused removal is logged and retried on a later run; the label is only an index |
| `post_conflict_blocker` (885), `post_mid_cycle_blocker` (947) | stays | They only build the text; the write goes through `post_comment` (B1) |
| `dispatcher_marker` (1004) | B2 | The marker format |
| **`resolve_dispatcher_login`** (1013) | B2 | `whoami`; today an empty login is caught only by the callers |
| **`marker_holds`** (1029) | B2 | `holds`; `<on-read-error>` becomes mandatory |
| `post_blocker_once` (1051) | B2 | `write --once` |
| **`bot_comments_json`** (1091) | B2 | `list` |
| `evidence_failure_count` (1100), `evidence_is_pending` (2252), `cleanup_evidence_recorded` (2291) | B2 | Read own markers with inline jq |
| Marker builders: `evidence_failed_marker` (1081), `evidence_gave_up_marker` (1085), `merge_owner_marker` (2162), `merge_attempt_marker` (2263), `fix_round_attempt_marker` (2360), `hand_off_pending_marker` (2390), `hand_off_pending_prefix` (2394), `hand_off_done_marker` (2398), `conflict_round_attempt_marker` (2902), `merge_conflict_marker` (2906), `review_attempt_marker` (3176); inline `action=in-merge-without-pr` (2773) and `action=fix-round-max` (2812, 3574) | B2 | `write --action`; the text stays the same |
| **`record_merge_owner`** (2166) | B2 | Merge-owner marker with its own retry loop and pending copy (#348) |
| `ensure_merge_owner` (2204) | B2 | Posts the pending owner marker for exactly this head |
| **`head_has_merge_owner_marker`** (2981, #406) | B2 | Own-marker read for one head; today a failed read counts as present (the merge script decides again) |
| **`merge_owner_missing_marker`** (2910, the `action=merge-owner-missing` marker of #406) and `block_ownerless_head` (2991) | B2 | One blocker per head through `write --once` |
| `record_pending_hand_off` (2440), `complete_pending_hand_off` (2460) | B2 | Pending/done markers; the status write inside goes through B1 |
| `bookkeeping_login` (1816), `fix_round_heads` (1825), `fix_round_attempt_superseded` (2370), `no_push_round_is_expected` (2518) | B2 | Markers of the bookkeeping account: `--author bookkeeping` |
| `conflict_round_state` (2917) | B2 | Logins and comment reads; the rule it calls stays (below) |
| `check_and_resolve_decision_blocker_comment` (670) | B2 | Visible CLAIM and RESUME markers matched from any author |
| `post_allowance_comment` (1448), `post_usage_limit_comment` (1534), the pause record in `pause_at_usage_limit` (1630) | B2 | "Already the newest comment" checks on any author's text; best-effort writes (B1 exit `1` is logged, not fatal) |
| `register_actionable_event` (520), `register_po_blocker` (539), `evidence_unwritable_alert` (1130), `notify_closure_if_complete` (757) | stays | They call `dispatcher-notify-retry.sh register`, which owns that state (B3 there) |
| `record_session_start` (1589) | stays | Calls `dispatcher-usage-limit.sh record`, which owns that state (B3 there) |
| `_dispatcher_exit_hook` (487), `DISPATCH_REGISTER_FAILURES_FILE` | stays | A temporary file that lives for one run, not saved state |
| `has_handoff_note` (410), `check_and_notify_decision_blocker` (567), `latest_decision_blocker_json` (593) | stays | They read Handoff Notes and Blocked Declarations that sessions and the PO write, not dispatcher markers; their trust rule is in `docs/PROJECT-STATUS.md` |
| `check_and_resolve_decision_blocker_comment` — the PO's `/unblock` rule (#433) | stays | The `.user.login` filter on `DECISION_BLOCKER_PO_LOGIN` is the PO's resolving-comment rule of `docs/PROJECT-STATUS.md`, not a dispatcher marker. Its CLAIM and CONFIRM comments go through B2 (`own-marker` allowlist entry `check_and_resolve_decision_blocker_comment`) |
| `resolved` (`dispatcher-notify-retry.sh`) — the PO's `/unblock` rule (#433) | stays | Same rule for a `decision_blocker` or `po_blocker` event; `evidence_resolved` moved to B2 (`own-marker` allowlist entry `resolved`) |
| `evidence_pending_comment` (`scripts/lib/evidence-pending.sh`) (#433) | stays | Builds the text of the evidence-pending comment of #348, with the `hpw:dispatcher` marker inside; the block writes it (B1) and reads it back (B2) (`own-marker` allowlist entry `evidence_pending_comment`) |
| `review_request_after` (2504) | stays | The non-Bot review-request rule of ADR-006 and `review_policy.py`, not a dispatcher marker |
| `conflict_round_rule` (`scripts/lib/conflict-round.sh`) | stays | A pure decision rule over the facts it is given; it already filters by the three logins it receives (#409, #449) |
| Reads with no marker and no state: `pr_conflicts_with_main` (979), `pr_mergeability` (988), `pr_merged_at` (1110), `find_linked_pr*` (1770–1796), `fetch_board_data` (1855), `get_status_option_id` (2025), `get_project_item_id` (905), `pr_is_merged` (2259), `pytest_green_on_head` (3059), the stale-story reads of linked PRs, commits and branches (3530–3610) | stays | Read-only. B1 does its own fresh read-back and does not use the board snapshot |

Other scripts:

| Variant (file, line) | Block | Note |
|---|---|---|
| Owner-marker check: `gh api user` plus jq on `.l == $bot` (`dispatcher-merge.sh`, 116–128) | B2 | `holds --marker "action=merge-owner pr=<n> head=<sha>"` |
| Merge confirmation POST with retry and pending copy (`dispatcher-merge.sh`, 175–193) | B1 | Moved (#432): `write comment --evidence-item merge-confirmation` |
| `gh api graphql` viewer check (`dispatcher-merge.sh`, 79) | stays | Read-only capability check |
| Closing summary POST with retry and pending copy (`dispatcher-cleanup.sh`, 122–140) | B1 | Moved (#432): `write comment --evidence-item cleanup-summary` |
| Merged-PR lookup (`dispatcher-cleanup.sh`, 47–54) | stays | Read-only |
| **`load_state`** (118), **`save_state`** (147) and the `register` path (167–195) (`dispatcher-notify-retry.sh`) | B3 | Moved (#434): thin shims for `state read|write` on `actionable-events`; a malformed line is dropped and the valid lines are kept |
| Delivery-failure comment POST (`dispatcher-notify-retry.sh`, 325) | B1 | Moved (#432): `write comment`; a write that is not read back leaves `comment_posted` false, so the next cycle retries |
| `evidence_resolved` (208), `resolved` (239) (`dispatcher-notify-retry.sh`) | B2 | Own-login filter and the PO `/unblock` rule, written again here |
| **`load_token_login`** (`dispatcher-poll.sh`, 353) | B2 | `whoami`, today through `curl` |
| Refinement state read (470–483) and write (518–527) (`dispatcher-poll.sh`) | B3 | Moved (#434): `state read|write` on `notified-refinement`; removed by #474 with the notification |
| `graphql` (156), `rest_get_all` (336), `load_open_prs` (316), `status_items` (282) (`dispatcher-poll.sh`) | stays | Read-only. The poll passes the comments it reads to `conflict_round_rule`, which filters by login |
| `read_count` (41), `record`, `notify-due`, `mark-notified` (71–89) (`dispatcher-usage-limit.sh`) | B3 | Moved (#434): `state read|write` on `usage-sessions` and `usage-notified`; a malformed or unreadable counter starts no session |
| `read_limit` (`dispatcher-usage-limit.sh`, 52) | stays | Reads configuration, not state |
| `HOLD_FILE` read (143–150) and write (274–286) (`dispatcher-allowance.sh`) | B3 | Moved (#434): `state read|write` on `allowance-hold` |
| `gh run list` (`dispatcher-trigger-check.sh`, 48) | stays | Read-only; the event it queues goes through `dispatcher-notify-retry.sh` |
| `dispatcher-change-check.sh` | stays | The checker itself; it makes no GitHub call. `verified-write` reports it as SKIP, because its own patterns would match |
| `graphql` (`dispatcher-poll.sh`, 156) | stays | A read-only GraphQL query sent with `curl -X POST`: the `verified-write` allowlist entry `graphql verified-write` |

**Best-effort writes (#432).** A caller may ignore a Block 1 exit `1` only for these writes. Each logs the block's error.

| Write (function or step) | Status or object | Why a failure is not fatal | Test |
|---|---|---|---|
| E1 before `/implement` (`dispatcher-invoke.sh`) | Board: "In implementation" | The session still starts; the hand-off records a pending hand-off, so the story is not started twice | `tests/test_dispatcher_review_loop.py::test_failed_first_status_write_does_not_start_the_story_twice` asserts that the session starts |
| Reserve skip and allowance error before `/implement` (`check_subscription_allowance`, `check_usage_limit`) | Board: "Ready for implementation" | No session starts either way and the run ends as handled; the story is picked up again as stale or ready | `tests/test_dispatcher_board_fail_closed.py::test_refused_ready_at_the_allowance_starts_no_session_and_is_handled` |
| `retry_pending_cleanup_summaries` | Label `evidence:pending` removed | The label is only an index; a later run removes it | — |

Comment writes whose failure a caller ignores are all B2 rows above, moved by #433: the CLAIM comment of `check_and_resolve_decision_blocker_comment`, `post_allowance_comment`, `post_usage_limit_comment`, the pause record in `pause_at_usage_limit`, `record_pending_hand_off`, `complete_pending_hand_off`, the fix-round attempt marker in `hand_off_to_codex_review_loop` (`fix_round_attempt_marker`) and the review attempt marker in `resume_review_story` (`review_attempt_marker`).

The board writes of `finish_story` ("Done", and "In merge" when the cleanup evidence is missing) and of the stale-story rollback ("Ready for implementation") fail closed since #432; `docs/DISPATCHER-CONFIG.md` → "GitHub writes" lists what each does instead. When such a write may have landed (`sent`) without being confirmed, the caller writes the old status back before it waits for a later run, and acts as if the new status holds when that write-back fails too. No new B1 variant was found on #432's base (`3b5cae6`).

`.github/workflows/dispatcher.yml`:

| Variant (step, line) | Block | Note |
|---|---|---|
| "Resolve notification state scope" (114) | B3 | Moved (#434): `dispatcher-workflow-step.sh scope`; rule 3 (state writer) |
| "Restore refinement notification state" (128), "Save poll notification state" (173), "Save refinement notification state" (316) | B3 | Moved (#434): rules 1 and 2 hold; "Save poll notification state" gained `always()` |
| "Set cache date" (180), "Restore session state cache" (184), "Set cache save date" (261), "Save session state cache" (265) | B3 | Moved (#434): the dates are `dispatcher-workflow-step.sh cache-date` and `cache-save-date`; rules 1 and 2 hold |
| Inline `gh api` read of the `evidence:pending` label (165, in "Run dispatcher poll") | B3 | Moved (#434): `dispatcher-poll.sh` reads the label and reports `evidence_pending`; a failed read counts as pending |
| "Notify usage limit reached" (246), `mark-notified` | B3 | Moved (#434): `dispatcher-workflow-step.sh usage-limit-notify` |
| The `RESUMABLE` jq expression (in "Run dispatcher poll") | stays | It decides no write, marker or state, so no block replaces it. Moved (#434) into `dispatcher-poll.sh` under #368 → F6: the poll reports `resumable` and `next_issue` |

## Options Considered

- **A. Split: building blocks in Python, control flow in bash. Chosen.** The blocks are small, tested in pytest with a fake gateway, and reached from bash through one contract that `hpw merge-owner` and `hpw merge-pr` already use. The 3,795-line control flow and its tests stay as they are. The cost is one Python process per call (a run makes tens of them) and a JSON contract to keep stable.
- **B. Everything in bash, as a shared `scripts/lib/` library. Rejected.** The finding classes come from bash habits a library cannot remove. `|| true`, `2>/dev/null` and `set -e` being off inside `if` and `||` turn a failed `gh` or `jq` call into an empty string that reads as "no". JSON is checked with jq strings, not types. Every test needs the PATH fakes for `gh` and `jq`, and on Windows Git Bash `jq` CRLF output and path rewriting break scripts that pass on Linux (#398). A bash library would repeat the same mistakes in one place instead of many.
- **C. Move the whole dispatcher to Python. Rejected.** It is outside #368 → Out of Scope, every dispatcher test would have to be rewritten, and "no behaviour change" could no longer be shown by unchanged tests.
- **D. Leave the logic in `dispatcher.yml` steps. Rejected.** pytest does not reach workflow expressions (#368 → Why), and F6 removes decision logic from the workflow.

## Consequences

- #432 creates `src/headless_pr_workflow/dispatcher/` with one gateway around `gh` (the pattern of `codex_review_bookkeeping.py`), the `hpw dispatcher` command and Block 1, and moves the B1 rows to it. #434 and #433 build on that gateway; #433 hard-depends on #432 because marker writes go through Block 1 (#368 → Implementation Order). Each story adds its change-check rule and moves its own inventory rows.
- One result changes: a malformed or unreadable `usage-sessions` counter starts no session instead of counting as 0. #434 adds the test. Every other fail-closed result above is today's result, now written down in one place.
- `dispatcher.yml` already runs `hpw` (through `dispatcher-merge.sh`), so no new runner setup is needed.
- A test that runs the real `hpw` against the bash `gh` fake cannot run on Windows, because Python's `subprocess` cannot start that fake (AGENTS.md → Running Tests). Block tests therefore use a Python fake gateway, and shell tests that need a block on Windows set `HPW_BIN` to a fake.
- Other #160 work (Feature #417) may change `dispatcher-invoke.sh` after this ADR. Each building-block story checks the inventory against its own base and lists any new variant in its PR.
- `codex_review_bookkeeping.py` may later use the same gateway. That is not part of this decision.
- Revisit if a block's call cost becomes a noticeable share of a dispatcher run, or if the remaining bash control flow causes the same finding classes again after #432–#434. That would be the case for moving more of it to Python, through a new ADR.
