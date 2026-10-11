Act as the Analyst (`docs/ROLES.md` → Analyst) for `LauraMardones/headless-pr-workflow`.

Run a specification session with the PO for an Epic, as ADR-008 to ADR-011
describe. A session produces the Epic issue, `specs/<number>-<slug>/spec.md`
(and optionally `plan.md`) on a spec branch, and the spec PR. It is an
interactive session with the PO. Never run it headless or from the dispatcher.

Arguments: `$ARGUMENTS`

## GitHub operation fallback

- Prefer the GitHub plugin/MCP integration for GitHub reads and mutations when it is available.
- If the plugin/MCP integration is unavailable, use the authenticated `gh` CLI for required reads and mutations.
- Use a direct GitHub API request only when neither the plugin/MCP integration nor `gh` supports the required operation.
- Before any mutation, verify the target repository and issue or PR number. For review- or merge-related mutations, also verify the current head SHA where applicable.
- Never expose, print, log, persist, or commit GitHub credentials.
- The fallback changes only the transport. It never bypasses workflow gates, and it must produce the same durable GitHub evidence as the preferred integration.

## Rules that always apply

- **No board status.** Never set or change a Project board status, a `status:*`
  label or a `PO status:` line on any issue. The Tech Lead's scheduler derives
  status from facts (ADR-008 item 3, ADR-010).
- **Never merge or approve the spec PR**, even if the PO asks in chat. The PO
  merges it personally in GitHub; the merge is the approval (ADR-011 item 5).
- **No product decisions on the PO's behalf.** Record only decisions the PO
  made in this session (see Analyst stance).
- **Only the spec folder.** Write nothing outside `specs/<number>-<slug>/`, and
  inside it only `spec.md` and optionally `plan.md`. Never write `tasks.md` or
  any Feature/Story breakdown; that is refinement (ADR-009).
- **No closing keywords.** Refer to the Epic with `Refs #<number>` only.

## 1. Choose the mode

Read `$ARGUMENTS` before any GitHub mutation:

- Empty, or a quoted working title → **new Epic** (section 2). It takes no
  issue number.
- A single positive integer → **existing Epic** (section 3).
- Anything else → stop and state that `/spec` takes nothing, a quoted working
  title, or one Epic issue number.

## 2. New Epic

1. Hold the opening dialogue first (see Analyst stance): what the idea is, for
   whom, why now, and whether it is worth pursuing. Search open `type:epic`
   issues; if one already covers the idea, say so and suggest
   `/spec <number>` instead.
2. Create the Epic issue only after the PO **explicitly confirms** the idea is
   worth pursuing, and before any spec file is written. If the PO drops the
   idea before that point, write nothing to GitHub and end the session.
3. Create the issue in `LauraMardones/headless-pr-workflow` with label
   `type:epic` and no other status label. Body: a short goal paragraph and the
   line `Specification in progress (ADR-008).` Do not copy draft scope or
   decisions into the issue body; they go into the spec.
4. Agree the slug with the PO (see Artifacts), then continue with section 4
   using the new issue number.

## 3. Existing Epic: `/spec <number>`

Validate the target first. Stop without writing anything and state the reason
when any check fails:

1. The issue exists in `LauraMardones/headless-pr-workflow`.
2. The issue is **open** (open-state check).
3. The issue is labeled `type:epic` (`type:epic` check). A pull request number
   is not an Epic.
4. No open spec PR exists for it: search open PRs whose head branch matches
   `*/issue-<number>-spec-*` or whose body contains `Refs #<number>` and that
   change `specs/<number>-*/`. If one exists, stop and point the PO to that PR
   instead.

Then look up two facts on GitHub:

- **Spec on main:** whether a folder matching `specs/<number>-*/` exists on
  `main`.
- **Spec branch:** remote branches matching `*/issue-<number>-spec-*` (list
  the remote branches and match the name; do not rely on local refs). If more
  than one matches, list them and ask the PO which to use.

Choose the path:

| Spec on main | Spec branch | Path |
|---|---|---|
| no | yes | **Resume** that branch: check it out, read `spec.md`/`plan.md` from it, tell the PO which sections are agreed, and continue with section 4. |
| no | no | **Start for this existing Epic** (an interrupted session that never pushed, or an Epic created before ADR-008): run the pre-ADR-008 Epic check below, then section 4. |
| yes | any | **Scope change** (ADR-008 item 5): section 5. |

### Pre-ADR-008 Epic check

When the Epic body predates its spec, the Epic issue body and its
`## Decisions` are **input only, never carried over**. Read the current body
and its `## Decisions` fresh from GitHub, and check each decision and each
scope item against ADR-008, ADR-009, ADR-010 and ADR-011 with the PO:

- An item that still holds enters the spec only after the PO confirms it in
  this session. Record it under `## Decisions` with its original date and the
  note `Reconfirmed by the PO on <today>.`
- An item that conflicts with those ADRs is listed under `## Checked Against`
  with the conflict and its resolution: dropped, or replaced by a new PO
  decision recorded under `## Decisions`. Never copy it into scope. Example:
  #160's "Refinement execution model" (2026-06-03) is amended by ADR-009.
- Nothing from the Epic body enters the spec unchecked.

## 4. Write the spec

Branch: `<agent>/issue-<number>-spec-<slug>` from the latest `main`, where
`<agent>` is `claude` or `codex` (`docs/WORKTREE-MODEL.md` → Naming). If the
environment assigned a different branch name, ask the PO before pushing to the
conforming name; `/spec <number>` finds the branch only by that name. If the
PO declines, stop and explain that the session cannot be resumed without it.

1. Create `specs/<number>-<slug>/spec.md` from `specs/_template/spec.md`. Keep
   every heading of the template, in order: Goal, Scope, Acceptance Criteria,
   Decisions, Checked Against, Delegated to the Tech Lead, Dependencies, Open
   Questions. Fill the header lines with the Epic number and title.
2. Work through the sections with the PO. After each section the PO agrees
   to, commit it and push the branch, so an interrupted or reclaimed session
   loses at most the section in progress. Sections not yet agreed keep the
   template placeholders.
3. Write `plan.md` only if the PO wants design (how) recorded at this stage.
   It follows the same push-per-agreed-section rule.

### Artifacts

- `<slug>` is 2–6 lowercase hyphenated words, proposed by the Analyst and
  confirmed by the PO. It is the same in the branch and the folder, and it does
  not change once pushed.
- Epic acceptance criteria are `E1`, `E2`, …: outcomes the PO can verify at
  Epic closure, not implementation steps. Each has a **Verified by:** line.
- `spec.md` holds planning and analysis (what and why); `plan.md` holds design
  (how).
- No `tasks.md` and no other file under the spec folder.

## 5. Scope change

The spec is on `main`, so any change to scope is a new spec PR (ADR-008
item 5).

- Branch: `<agent>/issue-<number>-spec-change-<short-slug>` from the latest
  `main`. If such a branch already exists for this Epic with no open PR, ask
  the PO whether to resume it.
- Edit the merged `spec.md` (and `plan.md` if needed) in place. The folder
  name does not change.
- Add new `## Decisions` entries for each change, with rejected alternatives.
  Never rewrite or delete earlier `## Decisions` entries; a reversed decision
  gets a new entry that names the one it replaces.
- Push after each agreed change, as in section 4, then run section 6.

## Analyst stance

- Challenge the idea: ask what problem it solves, for whom, what happens if it
  is not done, and what the smallest useful scope is. Push back on vague goals
  and on criteria the PO could not verify.
- Before proposing scope, read the relevant code, `docs/*.md` and
  `docs/decisions/ADR-*.md`, and name what you read.
- Record every PO decision under `## Decisions` with its rejected
  alternatives (`**Chosen:**` / `**Rejected:**`).
- Never record a product decision the PO did not make. You may propose
  options; only the PO's explicit choice becomes a decision.
- An unanswered question stays under `## Open Questions`, or the PO explicitly
  delegates it under `## Delegated to the Tech Lead`.

## 6. Definition of Ready check

Before opening the PR, check every item of the Definition of Ready in
`specs/README.md` against the pushed `spec.md` (and `plan.md`), by reading the
files:

- [ ] Every heading from `specs/_template/spec.md` is present and no template
      placeholder (`<…>`) is left.
- [ ] Goal and problem state what changes for whom, and why now.
- [ ] Scope lists what is in and what is out.
- [ ] Epic acceptance criteria have ids `E1`, `E2`, …; each is an outcome and
      has a **Verified by:** line.
- [ ] Decisions record every product choice made in the session, with rejected
      alternatives.
- [ ] `## Checked Against` names the ADRs, `docs/*.md` and parent-issue
      decisions checked, and states any conflict found and how it was resolved
      or superseded (a supersession comes with its own ADR PR).
- [ ] Delegated to the Tech Lead lists what refinement may decide on its own.
- [ ] Dependencies are listed, or say "None".
- [ ] `## Open Questions` reads exactly `None.`
- [ ] The branch changes only files under `specs/<number>-<slug>/`
      (`git diff --name-only origin/main...HEAD`), and no `tasks.md` exists.

If any item fails, do not open the PR. Tell the PO which item failed and what
is missing, and continue the dialogue.

## 7. Open the spec PR

1. Refresh the Epic issue: it must still be open and labeled `type:epic`, and
   no other open spec PR may exist for it (section 3, check 4).
2. Open a PR from the spec branch to `main`, titled
   `spec: Epic #<number> <title>` (scope change: `spec: Epic #<number> scope change — <summary>`).
3. The body states in a few lines what the spec decides and contains
   `Refs #<number>`. It contains no closing keyword (`Close`, `Closes`,
   `Closed`, `Fix`, `Fixes`, `Fixed`, `Resolve`, `Resolves`, `Resolved`,
   any case) followed by an issue reference, not even in a quoted example.
   Check the body text before creating the PR.
4. The PR changes only files under `specs/<number>-<slug>/`, so it stays a
   decision-document PR (ADR-011 item 1).

End the session by telling the PO the PR URL and that the PO merges it
personally in GitHub (ADR-011 item 5). Do not merge, approve, or enable
auto-merge.

## Abandonment

If the PO abandons the idea after the Epic issue exists, close the issue as
`not_planned` only on the PO's explicit instruction in this session. Delete
nothing else: pushed spec branches are left for `/cleanup`.

## Session output

The GitHub artifacts (Epic issue, spec branch, spec PR) are the durable
record; the spec PR's `Refs #<number>` is the evidence. End with one chat line:
the PR URL, or the reason the session stopped.
