# Merge Policy

Merge policy is normative. Adapters may add stricter gates, but they must not weaken these rules.

## Required Merge Conditions

A PR may be merged only when all conditions are true after a fresh GitHub refresh:

- The PR is open and targets the expected base branch.
- The current PR head SHA is known.
- The approval gate is satisfied for the current PR head SHA (see [Approval Gate: Independent Review](#approval-gate-independent-review)).
- No new commits were pushed after the relied-on approval evidence.
- Required CI/checks have completed successfully for the current head SHA.
- Blocking review threads or comments are resolved or explicitly waived according to repo policy.
- Required manual gates are satisfied.
- The merge owner is the session that last implemented the approved current head SHA, or an authorized human/operator acting under explicit takeover rules.

## Main Required-Check Policy

### `required_status_checks` values

| Value | Meaning |
|---|---|
| `absent` | GitHub branch protection does not configure any required status checks for the branch. |
| `required` | GitHub branch protection configures one or more required status checks for the branch. Tooling defers entirely to GitHub's live required-check status; the absent-by-policy bypass described below does not apply. |

### `ci_workflows` values

| Value | Meaning |
|---|---|
| `absent` | No GitHub Actions workflow files exist in `.github/workflows`. |
| `present_non_required` | Workflow files exist in `.github/workflows` but none are configured as required status checks for the branch. |
| `present_required` | Workflow files exist in `.github/workflows` and at least one is configured as a required status check for the branch. |

### Required-check gate

For `main` in this repository, GitHub branch protection requires the `pytest` check, so the absent-by-policy bypass does not apply to `main`. The bypass applies only to a branch whose `docs/required-check-policy.json` entry declares `required_status_checks` as `absent`, and only while all of these are true:

- GitHub does not report configured required status checks for `main`, or branch-protection required-check data is unavailable for this private repository.
- The current PR status-check rollup is empty, or every reported check is passing or skipped.
- The machine-readable policy source in `docs/required-check-policy.json` declares `required_status_checks` as `absent` and `ci_workflows` as `absent` or `present_non_required` for `main`.

When those facts are verified, `hpw ci-summary` and `hpw pre-merge` may report the required-check gate as passing because required checks are absent by explicit repository policy. Unavailable branch-protection data is not enough by itself; failing, pending, unknown, missing, or configured required checks must still block merge readiness.

Non-required workflow files (i.e. `ci_workflows: "present_non_required"`) do not affect the required-check gate. Only the presence or absence of *configured required status checks* determines whether the gate passes.

When `docs/required-check-policy.json` declares `required_status_checks` as `required` for a branch (set once branch protection actually configures a required check; `main` was switched when #322 merged, after branch protection began requiring `pytest`), the absent-by-policy bypass never applies for that branch. `hpw ci-summary`, `hpw pre-merge`, and `scripts/merge-gate-summary` defer to GitHub's live required-check status: a required check that is failing, pending, or has not yet reported blocks merge readiness exactly as a real GitHub-configured required check would.

`required_status_checks: "required"` must be paired with `ci_workflows: "present_required"`, and `present_required` is valid only with `required`. Any other pairing, or an unknown value in either field, is an inconsistent policy and fails closed. Under a `required` policy the gate also fails closed when it cannot confirm the configured checks:

| GitHub reports | `hpw ci-summary` / `hpw pre-merge` | `scripts/merge-gate-summary` |
|---|---|---|
| Inconsistent policy entry for the branch | `required_check_status: policy_inconsistent`, blocking, unless GitHub reports named required checks (those stay authoritative) | `policy=fail(inconsistent-policy)` in every case |
| Required-check data unavailable | `required_check_status: unavailable`, blocking | `checks=fail(required-unavailable)` |
| No required checks configured for the branch | `required_check_status: policy_required_unconfigured`, blocking | `checks=fail(required-unconfigured)` |
| Configured required checks, empty status-check rollup | `required_check_status: missing`, blocking | `checks=fail(missing-required)` |

A `required` policy never yields `policy_absent` or `absent-ok`. Because of the "no required checks configured" row, `docs/required-check-policy.json` must be switched to `required` only after branch protection actually requires the check; switching it earlier blocks every PR targeting that branch.

## Approval Gate: Independent Review

The approval gate is satisfied when a reviewer independent of the implementer found no blockers on the exact current head SHA (ADR-007). A repository with one maintainer is the normal case, not an exception: no second human approver is expected.

Any one of three kinds of evidence satisfies the gate. Each is checked by script (`hpw approval-check`, `hpw pre-merge`, `scripts/merge-gate-summary`) and fails closed: evidence the recognizer cannot verify does not count.

| Path | Evidence | Reported as |
|---|---|---|
| 1 | Clean Codex review of the current head | `approval_source: codex-review`, `approval=codex-review` |
| 2 | Separate-session review of the current head | `approval_source: separate-session-review`, `approval=separate-session` |
| 3 | Formal GitHub approval of the current head | `approval_source: formal`, `approval=formal` |

When more than one path is valid, the first of formal, Codex review, separate-session review is reported.

A blocking state wins over every path: a `CHANGES_REQUESTED` review decision, an unknown head SHA and a draft PR each block the merge even when path 1 or path 2 evidence is valid. A PR may be reviewed while draft, but no approval evidence may be relied on for merge until the PR is marked Ready for review.

### Path 1: Clean Codex Review

A PR conversation comment counts only when all of the following hold:

- It is authored by the Codex bot: actor type `Bot` with login `chatgpt-codex-connector` (shown as `chatgpt-codex-connector[bot]` by the REST API), and it was not edited by another account.
- It states that no major issues were found, in the known clean-result format.
- Its "Reviewed commit" identifier is a prefix of the current head SHA and of no other commit on the PR.
- No commit and no force-push appears after it in the PR timeline, so it was posted while the named commit was the head.
- No newer Codex review reports findings: no later PR review submission by the Codex bot, and no later Codex bot comment that is neither a clean result nor a usage-limit reply.
- The PR has a linked story (an issue in this repository that the PR closes), and every linked story carries exactly one `executor:` label, which is an `executor:claude-code-*` label.

Path 1 fails closed, with the stated `codex_review.status`, when:

| Status | Condition |
|---|---|
| `stale` | The named commit is not the current head, or a commit or force-push was added after the comment, even when the head SHA is again the one named. |
| `ambiguous` | The named prefix matches more than one commit on the PR. |
| `superseded` | A newer Codex review or comment has findings. |
| `not-applicable` | No linked story is found, a linked story has no `executor:` label or more than one, or the linked story carries `executor:codex`. |
| `missing` | No clean comment by the Codex bot exists. A 👍 reaction never counts, because it names no commit. Codex text quoted by a human, or posted by a `User` account with the bot's login, never counts. |
| `invalid` | The only Codex reply is a usage-limit or error reply or has an unknown format, the bot's comment was edited by another account, or the comments, commits, timeline or linked story could not be read completely. |

**A PR with no linked story cannot clear the gate through a Codex review.** Path 1 identifies the implementer from the linked story's `executor:` label, so a storyless PR must use path 2 or path 3. A PR implemented by Codex (`executor:codex`) also needs path 2 or path 3.

Unresolved Codex finding threads keep blocking through the review-thread gate, independently of this path.

### Path 2: Separate-Session Review

A separate-session review counts only when all conditions are true:

- Review was performed in a separate session from the session that implemented the reviewed head SHA.
- The review is recorded on GitHub against the current head SHA as a PR review submission summary (state `COMMENTED`).
- The review summary states that no blockers remain for that exact head SHA: it includes `solo-maintainer override accepted`, `no blockers remain for <head-sha>`, and that the review is the approval to rely on for the current head SHA.
- The review summary names the reviewing provider on its own line: `Reviewer provider: claude` or `Reviewer provider: codex`.
- The reviewing provider is not the provider that implemented the PR, per the `executor:` label of any linked story (`executor:claude-code-*` is `claude`, `executor:codex` is `codex`). With no linked story, or a linked story with no `executor:` label, there is nothing to compare and a stated valid provider is enough.

Path 2 fails closed when no reviewing provider is stated or the value is not `claude` or `codex`, when the stated provider matches the implementing provider, when the summary was recorded on an earlier head (stale), or when the linked stories could not be read completely.

The marker phrases keep their earlier wording for compatibility. The sentence that no independent GitHub approver is available is no longer required.

The expected GitHub evidence surface is the PR review submission summary attached to the reviewed head SHA. A top-level issue or PR conversation comment is not an equivalent surface for path 2 evidence because it is not the same SHA-bound review submission surface.

When a review session determines that no blockers remain, the reviewer should mark the PR Ready for review before issuing the final review summary.

Required PR review summary template:

```text
Reviewed head SHA `<head-sha>`.

No blockers remain for <head-sha>.

solo-maintainer override accepted.

Reviewer provider: <claude|codex>

This solo-maintainer override is the approval to rely on for the current head SHA.
```

Review-evidence tooling may inspect normalized review data from GraphQL surfaces such as `latestReviews` and `reviews`. Those fields are discovery surfaces for PR review submissions; references to them do not make issue comments an equivalent policy surface for path 2 evidence.

### Path 3: Formal GitHub Approval

A formal GitHub approval on the current head SHA satisfies the gate. A formal approval recorded on an earlier head is stale and does not count. Path 3 does not depend on the PR timeline or linked stories.

No path waives any other merge condition: required checks, unresolved review threads, draft state, mergeability and the fresh refresh before merge apply unchanged.

## SHA-Bound Approval

Approval is not a general property of a PR. Approval is a property of a reviewed PR head SHA.

"Approval" here means whichever evidence satisfies the approval gate: a clean Codex review, a separate-session review or a formal approval.

If the PR head SHA changes after approval:

- Existing approval evidence must be treated as stale until re-evaluated. This holds for all three paths.
- Merge must be blocked.
- Review delta should be shown to the reviewer.
- A new review must be recorded against the new head SHA: a new clean Codex review, a new separate-session review summary or a new formal approval.

## Fresh GitHub Refresh

Merge must never rely on stale local state.

Immediately before merge, the merge owner must refresh from GitHub and verify:

- PR number and repository.
- Current head SHA.
- Base branch.
- Approval state.
- Review thread state.
- CI/check state.
- Branch protection or mergeability state.

## Merge Ownership

Merge ownership belongs to the implementation session that last produced the approved head SHA. This creates a clean chain:

implementation session -> pushed head SHA -> separate review session -> approval -> same implementation session may merge after fresh refresh

If that session is unavailable, a takeover session may become merge owner only after:

- Fetching fresh GitHub PR context.
- Confirming no local-only state is required.
- Recording takeover intent.
- Re-running pre-merge checks.

The initial `hpw merge-owner` implementation is intentionally conservative. It
does not infer ownership from local chat history, branch names, or worktree
state. The current session identity must be supplied with `--session-id` or
`HPW_SESSION_ID`. Until durable owner recording exists, expected owner evidence
must be supplied explicitly with `--expected-owner` or
`HPW_EXPECTED_MERGE_OWNER`, optionally bound to the current PR head with
`--expected-owner-sha` or `HPW_EXPECTED_MERGE_OWNER_SHA`.

## Merge Command Behavior

The merge command must:

- Refuse to merge without a fresh pre-merge result.
- Refuse to merge if the current head SHA differs from the approved SHA.
- Refuse to merge if required CI is pending, failing, missing, or stale.
- Refuse to merge if unresolved blockers exist.
- Report the exact PR, head SHA, base branch, and merge method before action.

## Post-Merge Requirements

After merge:

- Confirm merged state in GitHub.
- Record or emit merge SHA when available.
- Sync local base branch if a local checkout exists.
- Mark temporary worktrees and branches as cleanup candidates.
