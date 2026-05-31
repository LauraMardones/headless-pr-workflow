# Contributing

Welcome, and thanks for your interest in contributing! This guide covers the essentials: how to report bugs, propose new commands, set up a local development environment, and understand the one design constraint you must keep in mind before submitting a PR.

## Reporting Bugs

Open an issue on the [GitHub Issues](https://github.com/lauramardones/headless-pr-workflow/issues) tracker.

A useful bug report includes:

- **Clear title** — summarise the problem in one line.
- **Steps to reproduce** — the exact sequence of commands or actions that triggers the bug.
- **Expected behaviour** — what you thought would happen.
- **Actual behaviour** — what actually happened, including any error output.

## Proposing a New Command

Before opening a PR for a new command, open an issue first so the approach can be agreed on.

When designing a command, keep the command contract in mind:

- Commands must be assistant-agnostic and GitHub-centered.
- Repo-specific rules and assistant-specific behaviours belong in adapters, not in core commands.
- **Adapters must not weaken core gates.** They may add stricter gates, but they may not relax or bypass the core workflow policy.

See [`docs/ADAPTERS.md`](docs/ADAPTERS.md) for the full adapter model and how to write a compliant adapter.

## Development Setup

```
pip install -e ".[dev]"
pytest
```

That is the full local dev loop. Install the package in editable mode, then run the test suite.

## Design Constraint

Repo-specific logic and assistant-specific behaviour must not enter core commands. Core commands must remain reusable across repositories and assistants. If you have logic that is specific to a particular repository or to a particular AI assistant, put it in an adapter.

## Further Reading

- [`docs/ADAPTERS.md`](docs/ADAPTERS.md) — adapter model and the "adapters must not weaken core gates" rule
- [`docs/HEADLESS-PR-WORKFLOW.md`](docs/HEADLESS-PR-WORKFLOW.md) — normative workflow invariants
- [`docs/MERGE-POLICY.md`](docs/MERGE-POLICY.md) — merge policy
