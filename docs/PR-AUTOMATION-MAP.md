# PR Automation Map

This map normalizes the initial script catalog into reusable command contracts. Command names are stable workflow concepts, not necessarily file names.

## Columns

- `Command`: Stable CLI surface, usually exposed as `hpw <command>`.
- `Description`: What the command determines or performs.
- `Role`: Primary workflow role.
- `Priority`: `P0-blocking`, `P1-high`, `P2-medium`, or `P3-low`.
- `Phase`: Workflow phase.
- `Type`: `hard-gate`, `report`, `advisory`, `action`, or `adapter-check`.
- `Layer`: `core`, `optional-core`, `repo-adapter`, `assistant-adapter`, or `ops-later`.
- `Status`: `planned`, `scaffolded`, `implemented`, `wrapper-only`, or `deprecated`.

## Phase Names

- `A-intake`
- `B-planning`
- `C-session`
- `D-implementation`
- `E-review-readiness`
- `F-review`
- `G-reimplementation`
- `H-merge`
- `I-post-merge`
- `J-audit`

## Normalized MVP Commands

| Command | Description | Role | Priority | Phase | Type | Layer | Status |
|---|---|---|---|---|---|---|---|
| `pr-context` | Fetch complete current PR context from GitHub. | all | P1-high | C-session | report | core | implemented |
| `pr-takeover` | Produce safe takeover context for a PR. | implementer, orchestrator | P1-high | C-session | action | core | scaffolded |
| `worktree-status` | Report local worktrees, branches, and dirty state. | all | P1-high | C-session | report | core | implemented |
| `review-sha` | Report current head SHA and approval SHA relationship. | reviewer, implementer | P0-blocking | F-review | hard-gate | core | implemented |
| `approval-check` | Verify approval applies to current PR head SHA. | implementer, reviewer | P0-blocking | F-review | hard-gate | core | implemented |
| `re-review-needed` | Detect whether new commits require review re-evaluation. | reviewer, implementer | P1-high | F-review | hard-gate | core | implemented |
| `review-delta` | Show changes since the last reviewed SHA. | reviewer, implementer | P2-medium | F-review | report | core | scaffolded |
| `unresolved-review-threads` | Detect unresolved GitHub review threads. | reviewer, implementer | P1-high | F-review | hard-gate | core | implemented |
| `blocking-comments` | Detect blocking review comments or labels. | reviewer, implementer | P1-high | F-review | hard-gate | core | scaffolded |
| `ci-summary` | Summarize CI/check state for the current head SHA. | QA, implementer | P1-high | E-review-readiness | report | core | implemented |
| `target-branch-check` | Verify PR targets the expected base branch. | implementer, merge-owner | P0-blocking | H-merge | hard-gate | core | implemented |
| `merge-owner` | Determine whether this session may merge. | merge-owner, orchestrator | P1-high | H-merge | hard-gate | core | implemented |
| `pre-merge` | Compose merge-readiness checks. | merge-owner | P0-blocking | H-merge | hard-gate | core | implemented |
| `merge-pr` | Dry-run merge after fresh pre-merge gates pass. | merge-owner | P0-blocking | H-merge | action | core | implemented |
| `post-merge-sync` | Sync local state after merge. | merge-owner, implementer | P1-high | I-post-merge | action | core | scaffolded |
| `branch-cleanup` | Remove stale merged local branches when safe. | implementer | P2-medium | I-post-merge | action | core | scaffolded |
| `workflow-status` | Summarize PR state and next action. | orchestrator | P1-high | C-session | report | core | scaffolded |
| `next-action` | Suggest exact next safe workflow action. | orchestrator | P1-high | C-session | advisory | core | scaffolded |

## Generic But Renamed

| Initial Name | Normalized Name | Reason |
|---|---|---|
| `pre-qa.sh` | `review-readiness` | Avoids implying a project-specific QA phase. |
| `pre-review.sh` | `implementation-readiness` | Names the readiness state, not a meeting/event. |
| `review-routing.sh` | `required-reviewers` | Generalizes specialist routing. |
| `create-story-branch.sh` | `create-topic-branch` | Avoids assuming story-based planning. |
| `epic-link-check.sh` | `parent-link-check` | Avoids assuming epics. |
| `wave-plan.sh` | `batch-plan` | Generalizes delivery grouping. |
| `session-start.sh` | `workflow-start` | Centers workflow state over session mechanics. |
| `session-handoff.sh` | `handoff-note` | Describes the artifact produced. |

## Optional Generic Planning Layer

| Command | Notes |
|---|---|
| `issue-intake` | Useful when the workflow includes issue triage, but not required for PR safety. |
| `issue-template-check` | Generic only if template rules are adapter-driven. |
| `priority-check` | Generic only if label schema is configurable. |
| `parent-link-check` | Generic only if issue hierarchy is configurable. |
| `batch-plan` | Advisory planning tool, not a merge gate. |
| `dependency-check` | Generic if based on declared dependencies or changed paths. |
| `blocked-items` | Useful orchestrator report. |
| `role-router` | Advisory only. |

## Repo-Adapter Commands

| Initial Name | Adapter Reason |
|---|---|
| `source-of-truth-check.sh` | Depends on project-specific source-of-truth files. |
| `prompt-version-check.sh` | Depends on repo-specific prompt docs and code paths. |
| `events-contract-check.sh` | Depends on repo-specific event contracts. |
| `determinism-check.sh` | Depends on project-specific forbidden AI/runtime boundaries. |
| `human-gate-check.sh` | Depends on project-specific manual approval policy. |
| `protected-files.sh` | Depends on repo-specific protected path rules. |
| `mandatory-docs.sh` | Depends on repo-specific documentation policy. |
| `contract-drift.sh` | Depends on repo-specific contract model. |
| `docs-drift.sh` | Depends on repo-specific documentation structure. |
| `impl-spec-check.sh` | Depends on repo-specific implementation spec expectations. |
| `deploy-gate.sh` | Depends on repo-specific deployment policy. |
| `release-readiness.sh` | Depends on repo-specific release process. |

## Later Ops/Audit Commands

| Command | Reason Deferred |
|---|---|
| `pr-audit` | Useful after event model stabilizes. |
| `post-merge-audit` | Useful after merge command is implemented. |
| `cost-report` | Requires telemetry and assistant-specific accounting. |
| `session-registry` | Local convenience, not source of truth. |
| `prune-worktrees` | Requires conservative local deletion UX. |
| `stale-branches` | Useful after branch policy stabilizes. |
| `cleanup-branches` | Hygiene automation after MVP safety gates. |
