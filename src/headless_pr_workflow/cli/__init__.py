"""CLI package wrapper with next-action extension."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from ..next_action import summarize_next_action_from_subprocess


_LEGACY_CLI_PATH = Path(__file__).resolve().parent.parent / "cli.py"
_LEGACY_SPEC = importlib.util.spec_from_file_location("headless_pr_workflow._legacy_cli", _LEGACY_CLI_PATH)
if _LEGACY_SPEC is None or _LEGACY_SPEC.loader is None:
    raise ImportError(f"Unable to load legacy CLI module from {_LEGACY_CLI_PATH}")
_legacy_cli = importlib.util.module_from_spec(_LEGACY_SPEC)
sys.modules[_LEGACY_SPEC.name] = _legacy_cli
_LEGACY_SPEC.loader.exec_module(_legacy_cli)

for _name in dir(_legacy_cli):
    if _name.startswith("__") and _name not in {"__doc__", "__all__"}:
        continue
    globals().setdefault(_name, getattr(_legacy_cli, _name))


def build_parser() -> argparse.ArgumentParser:
    parser = _legacy_cli.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            next_action_parser = action.choices.get("next-action")
            if next_action_parser is not None:
                next_action_parser.add_argument(
                    "--path",
                    help="Local worktree path to inspect. Defaults to current working directory.",
                )
            break
    return parser


def _print_next_action(target: str | None, *, repo: str | None, as_json: bool, path: str | None) -> int:
    if target is None or repo is None:
        print("next-action requires <pr> and --repo <owner/repo>", file=sys.stderr)
        return 2

    summary = summarize_next_action_from_subprocess(target, repo=repo, path=path)

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.ok else 1

    if not summary.ok:
        error = summary.errors or {"message": "Unable to produce next-action recommendation."}
        print(f"next-action: {error.get('message', 'failed')}", file=sys.stderr)
        return 1

    print(f"Next action: {summary.action}")
    print(f"Rationale: {summary.rationale}")
    if summary.blocking_reasons:
        print("Blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    print(f"Source posture: {summary.source_posture or 'unknown'}")
    print(f"Source commands: {', '.join(summary.source_commands)}")
    if summary.warnings:
        print("Warnings:")
        for warning in summary.warnings:
            print(f"- {warning}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "next-action":
        return _print_next_action(
            args.target,
            repo=args.repo,
            as_json=args.json,
            path=args.path,
        )

    _sync_legacy_overrides()
    return _legacy_cli.main(argv)


def _sync_legacy_overrides() -> None:
    for name, value in globals().items():
        if name.startswith("_"):
            continue
        if name in {"argparse", "importlib", "json", "sys", "Path", "build_parser", "main"}:
            continue
        if hasattr(_legacy_cli, name):
            setattr(_legacy_cli, name, value)
