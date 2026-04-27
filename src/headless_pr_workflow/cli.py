"""CLI entrypoint for headless PR workflow automation."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .approval_check import summarize_approval_check
from .catalog import COMMANDS, find_command
from .github import GHCommandError, fetch_pr_context, fetch_repo_default_branch, fetch_required_status_checks
from .pre_merge import summarize_pre_merge
from .review_sha import summarize_review_sha


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
        command_parser.add_argument("--repo", help="GitHub repository in OWNER/REPO format.")
        command_parser.add_argument("--json", action="store_true", help="Emit scaffold metadata as JSON.")
        if command.name == "pr-context":
            command_parser.add_argument("--include-raw", action="store_true", help="Include raw GitHub CLI payload in JSON output.")

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


def _print_pr_context(target: str | None, *, repo: str | None, as_json: bool, include_raw: bool) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    if as_json:
        print(json.dumps(context.to_dict(include_raw=include_raw), indent=2))
        return 0

    counts = context.check_counts
    print(f"PR #{context.number}: {context.title}")
    print(f"state: {context.state}")
    print(f"url: {context.url}")
    print(f"base: {context.base_ref_name} ({context.base_ref_oid or 'unknown'})")
    print(f"head: {context.head_ref_name} ({context.head_ref_oid})")
    print(f"repository: {context.head_repository or 'unknown'}")
    print(f"draft: {str(context.is_draft).lower()}")
    print(f"review decision: {context.review_decision or 'unknown'}")
    print(f"latest approval sha: {context.latest_approval_sha or 'none'}")
    print(
        "checks: "
        f"{counts['success']} success, "
        f"{counts['failure']} failure, "
        f"{counts['pending']} pending, "
        f"{counts['skipped']} skipped, "
        f"{counts['unknown']} unknown"
    )
    if context.labels:
        print(f"labels: {', '.join(context.labels)}")
    if context.review_requests:
        print(f"review requests: {', '.join(context.review_requests)}")
    return 0


def _print_review_sha(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    summary = summarize_review_sha(context)

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.hard_gate_passed else 1

    print(f"PR #{summary.number}: {summary.title}")
    print(f"url: {summary.url}")
    print(f"head sha: {summary.head_ref_oid}")
    print(f"latest review sha: {summary.latest_review_sha or 'none'}")
    print(f"latest review state: {summary.latest_review_state or 'none'}")
    print(f"latest review author: {summary.latest_review_author or 'none'}")
    print(f"latest approval sha: {summary.latest_approval_sha or 'none'}")
    print(f"approval status: {summary.approval_status}")
    print(f"hard gate passed: {str(summary.hard_gate_passed).lower()}")
    return 0 if summary.hard_gate_passed else 1


def _print_approval_check(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    summary = summarize_approval_check(context)

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.hard_gate_passed else 1

    print(f"PR #{summary.number}: {summary.title}")
    print(f"url: {summary.url}")
    print(f"head sha: {summary.head_ref_oid or 'unknown'}")
    print(f"latest review sha: {summary.latest_review_sha or 'none'}")
    print(f"latest review state: {summary.latest_review_state or 'none'}")
    print(f"latest review author: {summary.latest_review_author or 'none'}")
    print(f"latest approval sha: {summary.latest_approval_sha or 'none'}")
    print(f"approval status: {summary.approval_status}")
    print(f"approval source: {summary.approval_source or 'none'}")
    if summary.blocking_reasons:
        print("blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    print(f"hard gate passed: {str(summary.hard_gate_passed).lower()}")
    return 0 if summary.hard_gate_passed else 1


def _print_pre_merge(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
        expected_base_ref_name = fetch_repo_default_branch(repo=repo)
        required_check_names = fetch_required_status_checks(repo or context.head_repository or "", expected_base_ref_name)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    summary = summarize_pre_merge(
        context,
        expected_base_ref_name=expected_base_ref_name,
        required_check_names=required_check_names,
    )

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.hard_gate_passed else 1

    print(f"PR #{summary.number}: {summary.title}")
    print(f"url: {summary.url}")
    print(f"state: {summary.state}")
    print(f"draft: {str(summary.is_draft).lower()}")
    print(f"expected base: {summary.expected_base_ref_name or 'unknown'}")
    print(f"base: {summary.base_ref_name or 'unknown'} ({summary.base_ref_oid or 'unknown'})")
    print(f"head: {summary.head_ref_name or 'unknown'} ({summary.head_ref_oid or 'unknown'})")
    print(f"mergeable: {summary.mergeable or 'unknown'}")
    print(f"merge state status: {summary.merge_state_status or 'unknown'}")
    print(f"approval status: {summary.approval.approval_status}")
    if summary.blocking_reasons:
        print("blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    print("checks:")
    for check in summary.checks:
        status = "pass" if check.ok else "fail"
        print(f"- [{status}] {check.code}: {check.message}")
        for detail in check.details:
            print(f"  {detail}")
    print(f"hard gate passed: {str(summary.hard_gate_passed).lower()}")
    return 0 if summary.hard_gate_passed else 1


def _print_gh_error(error: GHCommandError, *, as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": error.error,
                    "command": error.command,
                    "returncode": error.returncode,
                    "stderr": error.stderr,
                },
                indent=2,
            )
        )
    else:
        print(error, file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "catalog":
        return _print_catalog(args.json)

    if args.command == "pr-context":
        return _print_pr_context(
            args.target,
            repo=args.repo,
            as_json=args.json,
            include_raw=args.include_raw,
        )

    if args.command == "review-sha":
        return _print_review_sha(args.target, repo=args.repo, as_json=args.json)

    if args.command == "approval-check":
        return _print_approval_check(args.target, repo=args.repo, as_json=args.json)

    if args.command == "pre-merge":
        return _print_pre_merge(args.target, repo=args.repo, as_json=args.json)

    return _print_scaffold(args.command, args.target, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
