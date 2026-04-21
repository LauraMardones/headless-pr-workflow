"""CLI entrypoint for headless PR workflow automation."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .catalog import COMMANDS, find_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hpw",
        description="Headless PR Workflow command scaffold.",
    )
    subparsers = parser.add_subparsers(dest="command")

    catalog_parser = subparsers.add_parser("catalog", help="List known workflow commands.")
    catalog_parser.add_argument("--json", action="store_true", help="Emit command catalog as JSON.")

    for command in COMMANDS:
        command_parser = subparsers.add_parser(command.name, help=command.description)
        command_parser.add_argument("target", nargs="?", help="PR, issue, branch, or repo target depending on command.")
        command_parser.add_argument("--json", action="store_true", help="Emit scaffold metadata as JSON.")

    return parser


def _print_catalog(as_json: bool) -> int:
    if as_json:
        print(json.dumps([asdict(command) for command in COMMANDS], indent=2))
        return 0

    for command in COMMANDS:
        print(f"{command.name}\t{command.priority}\t{command.phase}\t{command.command_type}\t{command.layer}\t{command.status}")
    return 0


def _print_scaffold(command_name: str, target: str | None, as_json: bool) -> int:
    command = find_command(command_name)
    if command is None:
        print(f"unknown command: {command_name}", file=sys.stderr)
        return 2

    payload = asdict(command)
    payload["target"] = target
    payload["implemented"] = False
    payload["message"] = "Command contract is scaffolded; live GitHub behavior is not implemented yet."

    if as_json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"{command.name}: {command.description}")
    print(f"status: {command.status}")
    print("implementation: pending")
    if target:
        print(f"target: {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "catalog":
        return _print_catalog(args.json)

    return _print_scaffold(args.command, args.target, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
