# ADR-016: The automation runs under its own GitHub App

**Status:** Accepted
**Date:** 2026-11-21
**Related:** Epic #530 (spec PR #631, `specs/530-standing-slask-epic/spec.md` → Decisions, 2026-11-21), issue #626, ADR-011, ADR-012, ADR-013, ADR-014, `docs/DISPATCHER-CONFIG.md`
**Amends:** ADR-012's consequence "The Claude session holds `PROJECT_TOKEN`… Until a narrower credential exists…"
**Effective when:** the Feature under Epic #530 that delivers the account has switched the dispatcher, board-status and the dispatcher-started sessions to the App's token. Until then `PROJECT_TOKEN` is what the workflows use, as `docs/DISPATCHER-CONFIG.md` describes.

## Context

The dispatcher, board-status and the sessions the dispatcher starts use `PROJECT_TOKEN`, the PO's personal token. The PO's own sessions use the same account, so all of them share one limit of 5000 API calls per hour. Issue #626 records 11 failed dispatcher runs out of the last 40 (2026-11-21), caused by that limit. ADR-012 left the token as it was "until a narrower credential exists". On 2026-11-21 the PO chose an own account for the automation (#626, option b), and spec #530 records the decision.

## Decision

1. The dispatcher, board-status and the sessions the dispatcher starts run under their own GitHub App, `hpw-automation`, installed on the PO's account for this repository only. `PROJECT_TOKEN` stays with the PO's own sessions.
2. The sessions use the App's token for everything they do, including `git push`.
3. The App has repository access, board (project) write, and administration read, so the merge gate can read branch protection. The gate is not weakened: it reports `checks=fail(required-unavailable)` when the read fails, as before. The exact permission list is in `docs/DISPATCHER-CONFIG.md`.
4. Board write is not proven for a user-owned project. The Tech Lead proves it with a test call. If the App cannot write the board, this decision is not worked around: the PO decides again by a new scope change to spec #530.
5. The 5-minute dispatcher schedule is unchanged. Reading less per run is a Tech Lead task in #626 and needs no decision.

## Consequences

- ADR-012's consequence about `PROJECT_TOKEN` no longer holds for the dispatcher-started sessions once this ADR is effective. A session with the App's token can still merge, so the merge gate keeps resting on the command instructions and branch protection.
- ADR-014's transport line ("the token `gh` already uses, `PROJECT_TOKEN` in the workflow") and ADR-013's mentions of the token read as the token the workflow sets in `GH_TOKEN`. They need no edit.
- A merge by the App's account shows as a different account from the PO's, so the PO's own merge of a decision document (ADR-011 item 5) is easier to tell apart. No rule changes.
- The App's installation token is created in each workflow run and expires after an hour. The Tech Lead chooses how the workflows create it.
- The PO creates the App and stores its credential as repository secrets. An agent never holds or prints it.
- Revisit if the board-write proof fails, or if the App's own limit is reached.
