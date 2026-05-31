# Headless PR Workflow

Reusable, assistant-agnostic workflow policy and automation patterns for GitHub-centered pull request work.

This repository treats GitHub as the system of record for issues, pull requests, reviews, approvals, CI status, blockers, and merge state. Local branches, worktrees, and assistant sessions are temporary execution contexts.

## Prerequisites

- Python ≥ 3.10
- [GitHub CLI (`gh`)](https://cli.github.com/) installed and authenticated

  ```bash
  gh auth login
  ```

## Quickstart

**Prerequisites:** Python 3.10+, [GitHub CLI (`gh`)](https://cli.github.com/) authenticated.

```bash
pip install git+https://github.com/LauraMardones/headless-pr-workflow.git
```

Run a pre-merge readiness check against a pull request:

```bash
hpw pre-merge <PR_NUMBER> --repo OWNER/REPO
```

Example output shape:

```
pre-merge
  pr:          #42  main ← feature/my-branch
  approved:    yes (sha: abc1234)
  ci:          all passing
  unresolved:  0 threads
  fresh:       yes
  verdict:     ready to merge
```

Check overall workflow status:

```bash
hpw workflow-status <PR_NUMBER> --repo OWNER/REPO
```

List all available commands:

```bash
hpw catalog
```

## Core Principles

- GitHub is the source of truth.
- Review approval is bound to a specific PR head SHA.
- Implementation and review must not happen in the same session for the same reviewed head SHA.
- New commits after approval require approval to be re-evaluated.
- Merge requires a fresh GitHub refresh immediately before merge.
- Merge ownership belongs to the session that last implemented the PR head that was approved.
- Takeover between assistants and sessions is a first-class workflow path.
- Deterministic checks belong in scripts.
- Repo-specific rules belong in repo adapters.
- Assistant-specific behavior belongs in optional assistant adapters.

## Implemented Commands

All `hpw` commands are fully implemented:

| Command | Description |
|---|---|
| `pre-merge` | Full pre-merge readiness gate (approval, CI, threads, staleness) |
| `workflow-status` | Overall PR workflow state summary |
| `next-action` | Recommended next action for a PR |
| `review-delta` | Diff summary since last review baseline |
| `approval-check` | Approval status and head SHA binding |
| `pr-takeover` | Cross-session or cross-assistant takeover assessment |
| `merge-pr` | Merge execution with pre-merge gates |
| `post-merge-sync` | Post-merge state verification |
| `branch-cleanup` | Local branch and worktree cleanup guidance |
| `worktree-status` | Worktree state relative to PR head |
| `ci-summary` | CI check summary for a PR |
| `review-sha` | Head SHA recorded at last review |
| `re-review-needed` | Whether new commits require re-review |
| `merge-owner` | Session identity and merge ownership check |
| `target-branch-check` | Verify PR targets the expected base branch |
| `pr-context` | Full PR context fetch (metadata, review threads, CI) |
| `catalog` | List all known workflow commands |

## Repository Layout

```text
docs/                        Normative policy and workflow docs
  HEADLESS-PR-WORKFLOW.md    Core workflow policy
  ROLES.md                   Session role definitions
  MERGE-POLICY.md            Merge rules and ownership
  TAKEOVER-RULES.md          Cross-session takeover protocol
  WORKTREE-MODEL.md          Worktree and branch model
  PR-AUTOMATION-MAP.md       Command-to-workflow mapping
  ADAPTERS.md                Adapter extension model
  ROADMAP.md                 Project roadmap
  required-check-policy.json Required CI check policy schema

src/headless_pr_workflow/    Python CLI implementation (~20 modules)
  cli.py                     `hpw` entrypoint
  cli/                       CLI subcommand helpers
  github/                    GitHub API integration (gh CLI wrapper)
  pre_merge.py               pre-merge command
  workflow_status.py         workflow-status command
  next_action.py             next-action command
  review_delta.py            review-delta command
  approval_check.py          approval-check command
  pr_takeover.py             pr-takeover command
  merge_pr.py                merge-pr command
  post_merge_sync.py         post-merge-sync command
  branch_cleanup.py          branch-cleanup command
  worktree_status.py         worktree-status command
  ci_summary.py              ci-summary command
  review_sha.py              review-sha command
  re_review_needed.py        re-review-needed command
  merge_owner.py             merge-owner command
  target_branch.py           target-branch-check command
  catalog.py                 command catalog registry

scripts/                     Thin shell convenience wrappers (hpw, hpw.ps1)
examples/                    Assistant and repo adapter examples
  assistants/                Per-assistant adapter examples
  repos/                     Per-repo adapter examples
  github-actions/            GitHub Actions integration examples
    hpw-pre-merge-gate.yml   Copy-paste workflow: enforce hpw pre-merge as a required check
tests/                       Deterministic tests for all modules
```

## Integration Examples

- `examples/github-actions/hpw-pre-merge-gate.yml` — copy-paste GitHub Actions workflow that enforces `hpw pre-merge` as a required status check before merge

## Policy Documentation

- `docs/HEADLESS-PR-WORKFLOW.md` — core policy
- `docs/ROLES.md` — session roles (implementer, reviewer, merge owner)
- `docs/MERGE-POLICY.md` — merge rules and approval binding
- `docs/TAKEOVER-RULES.md` — takeover protocol
- `docs/WORKTREE-MODEL.md` — worktree and branch lifecycle
- `docs/PR-AUTOMATION-MAP.md` — command-to-workflow mapping
- `docs/ADAPTERS.md` — extending with repo and assistant adapters
