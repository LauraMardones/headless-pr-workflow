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

## Why HPW / How it fits

HPW is a governance layer. Tools like Claude Code Actions, OpenAI Codex, GitHub Copilot Coding Agent, PR-Agent, and Pullfrog operate at the execution layer: they use AI to implement code, open pull requests, generate reviews, and automate delivery. HPW does not replace them — it defines the invariants that any session must satisfy before a PR is merged.

Three invariants HPW enforces by design:

- **SHA-bound approval** — an approval is valid only for the exact head commit SHA that was reviewed. Any new commit after approval requires re-review before merge.
- **Session separation** — the session that implements a PR head SHA must not also approve that same SHA in the same session. Implementation and review are structurally separate acts.
- **GitHub as system-of-record** — all workflow state (approvals, CI, review threads, merge readiness) is read from and written to GitHub, not inferred from local state or assistant memory.

These properties are checkable and deterministic. `hpw pre-merge` enforces them as a blocking gate; `hpw workflow-status` surfaces current state against them at any point.

| Tool | Layer | Focus |
|---|---|---|
| HPW | Governance | Auditable merge invariants, deterministic policy gate, session contracts |
| claude-code-action | Execution | AI-driven code implementation and PR creation via GitHub Actions |
| OpenAI Codex | Execution | AI-driven code generation and implementation |
| GitHub Copilot Coding Agent | Execution | AI-driven code suggestions, implementation, and PR automation |
| PR-Agent | Execution | AI-driven code review, suggestions, and PR management |
| Pullfrog | Execution | AI-driven PR description and change summarisation |

A team running any execution-layer tool can add HPW as a required status check (`hpw pre-merge`) to enforce the governance invariants before every merge, without changing how their existing AI tooling works.

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

scripts/                     Shell scripts for automation and notifications
  hpw                        Thin shell wrapper (Unix)
  hpw.ps1                    Thin shell wrapper (Windows PowerShell)
  flow-review.sh             Weekly flow review report from GitHub Projects v2
  slack-notify.sh            Slack notification adapter — posts Block Kit messages to an incoming webhook
examples/                    Assistant and repo adapter examples
  assistants/                Per-assistant adapter examples
  repos/                     Per-repo adapter examples
  github-actions/            GitHub Actions integration examples
    hpw-pre-merge-gate.yml   Copy-paste workflow: enforce hpw pre-merge as a required check
tests/                       Deterministic tests for all modules
```

## Integration Examples

- `examples/github-actions/hpw-pre-merge-gate.yml` — copy-paste GitHub Actions workflow that enforces `hpw pre-merge` as a required status check before merge

## Required Secrets

The following GitHub Actions secrets are required for full automation:

| Secret | Used by | Description |
|---|---|---|
| `GH_TOKEN` | `scripts/flow-review.sh` | GitHub personal access token with `repo` and `read:project` scopes |
| `SLACK_WEBHOOK_URL` | `scripts/slack-notify.sh` | Slack incoming webhook URL for dispatcher notifications |

Set these in **Settings → Secrets and variables → Actions** in the repository.

## Policy Documentation

- `docs/HEADLESS-PR-WORKFLOW.md` — core policy
- `docs/ROLES.md` — session roles (implementer, reviewer, merge owner)
- `docs/MERGE-POLICY.md` — merge rules and approval binding
- `docs/TAKEOVER-RULES.md` — takeover protocol
- `docs/WORKTREE-MODEL.md` — worktree and branch lifecycle
- `docs/PR-AUTOMATION-MAP.md` — command-to-workflow mapping
- `docs/ADAPTERS.md` — extending with repo and assistant adapters
