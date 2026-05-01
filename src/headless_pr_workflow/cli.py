"""CLI entrypoint for headless PR workflow automation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from .approval_check import summarize_approval_check
from .catalog import COMMANDS, find_command
from .ci_summary import summarize_ci
from .github import (
    GHCommandError,
    fetch_pr_context,
    fetch_repo_default_branch,
    fetch_required_status_check_context,
    fetch_review_thread_summary,
    fetch_review_threads_for_context,
    summarize_review_threads,
)
from .merge_owner import summarize_merge_owner
from .merge_pr import MERGE_METHODS, summarize_merge_pr
from .pre_merge import summarize_pre_merge
from .review_sha import summarize_review_sha
from .target_branch import summarize_target_branch


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
        if command.name == "target-branch-check":
            command_parser.add_argument("--expected-base", help="Expected PR base branch. Defaults to the repository default branch.")
        if command.name == "merge-owner":
            command_parser.add_argument(
                "--session-id",
                help="Current session identity. Defaults to HPW_SESSION_ID when unset.",
            )
            command_parser.add_argument(
                "--expected-owner",
                help="Explicit expected merge owner identity. Defaults to HPW_EXPECTED_MERGE_OWNER when unset.",
            )
            command_parser.add_argument(
                "--expected-owner-sha",
                help="Head SHA that the expected owner evidence applies to. Defaults to HPW_EXPECTED_MERGE_OWNER_SHA.",
            )
        if command.name == "merge-pr":
            command_parser.add_argument(
                "--method",
                choices=MERGE_METHODS,
                default="merge",
                help="Merge method to rehearse. Dry-run only; never performs a GitHub merge.",
            )

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
    print(f"head sha: {summary.head_ref_oid}")
    print(f"latest review sha: {summary.latest_review_sha or 'none'}")
    print(f"latest review state: {summary.latest_review_state or 'none'}")
    print(f"latest review author: {summary.latest_review_author or 'none'}")
    print(f"latest approval sha: {summary.latest_approval_sha or 'none'}")
    print(f"formal approval status: {summary.approval_status}")
    print(f"solo-maintainer override: {summary.solo_override.status}")
    print(f"override review sha: {summary.solo_override.review_commit_oid or 'none'}")
    print(f"approval source: {summary.approval_source or 'none'}")
    print(f"satisfied by: {summary.satisfied_by or 'none'}")
    if summary.blocking_reasons:
        print("blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    print(f"hard gate passed: {str(summary.hard_gate_passed).lower()}")
    return 0 if summary.hard_gate_passed else 1


def _print_pre_merge(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        summary = _fetch_pre_merge_summary(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

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
    print(f"approval source: {summary.approval.approval_source or 'none'}")
    print(f"status rollup: {summary.ci.status_rollup}")
    print(f"required checks: {summary.ci.required_check_status}")
    thread_counts = summary.review_threads.thread_counts
    print(
        "review threads: "
        f"{thread_counts['unresolved_blocking']} active unresolved, "
        f"{thread_counts['resolved']} resolved, "
        f"{thread_counts['outdated_or_superseded']} outdated/superseded"
    )
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


def _print_merge_owner(target: str | None, *, repo: str | None, args: argparse.Namespace) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=args.json)

    session_id = args.session_id if args.session_id is not None else os.environ.get("HPW_SESSION_ID")
    expected_owner = args.expected_owner if args.expected_owner is not None else os.environ.get("HPW_EXPECTED_MERGE_OWNER")
    expected_owner_sha = (
        args.expected_owner_sha if args.expected_owner_sha is not None else os.environ.get("HPW_EXPECTED_MERGE_OWNER_SHA")
    )

    summary = summarize_merge_owner(
        context,
        current_session_id=session_id,
        current_session_source="--session-id" if args.session_id is not None else "HPW_SESSION_ID",
        expected_owner_id=expected_owner,
        expected_owner_source="--expected-owner" if args.expected_owner is not None else "HPW_EXPECTED_MERGE_OWNER",
        expected_owner_head_sha=expected_owner_sha,
        expected_owner_head_sha_source=(
            "--expected-owner-sha" if args.expected_owner_sha is not None else "HPW_EXPECTED_MERGE_OWNER_SHA"
        ),
    )

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.hard_gate_passed else 1

    print(f"PR #{summary.number}: {summary.title}")
    print(f"url: {summary.url}")
    print(f"state: {summary.state}")
    print(f"head sha: {summary.current_head_sha or 'unknown'}")
    print(f"current session: {summary.current_session.identity or 'missing'} ({summary.current_session.source})")
    print(f"expected owner: {summary.expected_owner.identity or 'missing'} ({summary.expected_owner.source})")
    print(f"expected owner head sha: {summary.expected_owner.head_sha or 'not supplied'}")
    print(f"ownership status: {summary.ownership_status.replace('_', ' ')}")
    if summary.blocking_reasons:
        print("blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    print(f"next safe action: {summary.next_safe_action}")
    print(f"hard gate passed: {str(summary.hard_gate_passed).lower()}")
    return 0 if summary.hard_gate_passed else 1


def _print_merge_pr(target: str | None, *, repo: str | None, method: str, as_json: bool) -> int:
    try:
        pre_merge = _fetch_pre_merge_summary(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    summary = summarize_merge_pr(pre_merge, method=method)

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.would_merge else 1

    print(f"PR #{summary.pre_merge.number}: {summary.pre_merge.title}")
    print(f"url: {summary.pre_merge.url}")
    print("mode: dry-run (no GitHub merge mutation will be performed)")
    print(f"selected method: {summary.method}")
    print(f"base branch: {summary.pre_merge.base_ref_name or 'unknown'}")
    print(f"head sha: {summary.pre_merge.head_ref_oid or 'unknown'}")
    print(f"approval source: {summary.pre_merge.approval.approval_source or 'none'}")
    print(f"satisfied by: {summary.pre_merge.approval.satisfied_by or 'none'}")
    print(f"would merge: {str(summary.would_merge).lower()}")
    if summary.blocking_reasons:
        print("blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    return 0 if summary.would_merge else 1


def _fetch_pre_merge_summary(target: str | None, *, repo: str | None):
    context = fetch_pr_context(target, repo=repo)
    expected_base_ref_name = fetch_repo_default_branch(repo=repo)
    required_checks = fetch_required_status_check_context(repo or context.head_repository or "", expected_base_ref_name)
    review_threads = summarize_review_threads(context, fetch_review_threads_for_context(context, repo=repo))
    return summarize_pre_merge(
        context,
        expected_base_ref_name=expected_base_ref_name,
        required_checks=required_checks,
        review_threads=review_threads,
    )


def _print_target_branch_check(
    target: str | None,
    *,
    repo: str | None,
    expected_base: str | None,
    as_json: bool,
) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
        expected_base_ref_name = expected_base if expected_base is not None else fetch_repo_default_branch(repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    summary = summarize_target_branch(context, expected_base_ref_name=expected_base_ref_name)

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.hard_gate_passed else 1

    print(f"PR #{summary.number}: {summary.title}")
    print(f"url: {summary.url}")
    print(f"actual base: {summary.base_ref_name or 'unknown'}")
    print(f"expected base: {summary.expected_base_ref_name or 'unknown'}")
    print(f"target branch check: {summary.result}")
    if summary.blocking_reasons:
        print("blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    print(f"hard gate passed: {str(summary.hard_gate_passed).lower()}")
    return 0 if summary.hard_gate_passed else 1


def _print_ci_summary(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
        required_checks = fetch_required_status_check_context(repo or context.head_repository or "", context.base_ref_name)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    summary = summarize_ci(context, required_checks=required_checks)

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0

    print(f"PR #{summary.number}: {summary.title}")
    print(f"url: {summary.url}")
    print(f"base: {summary.base_ref_name or 'unknown'}")
    print(f"head: {summary.head_ref_name or 'unknown'} ({summary.head_ref_oid or 'unknown'})")
    print(f"status rollup: {summary.status_rollup}")
    print(f"required checks: {summary.required_check_status}")
    for state in ("passing", "failing", "pending", "skipped", "missing", "unknown"):
        names = summary.check_buckets[state]
        print(f"{state}: {len(names)}" + (f" ({', '.join(names)})" if names else ""))
    if summary.messages:
        print("messages:")
        for message in summary.messages:
            print(f"- {message}")
    return 0


def _print_unresolved_review_threads(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        summary = fetch_review_thread_summary(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.hard_gate_passed else 1

    counts = summary.thread_counts
    print(f"PR #{summary.number}: {summary.title}")
    print(f"url: {summary.url}")
    print(f"head sha: {summary.head_ref_oid or 'unknown'}")
    print(
        "review threads: "
        f"{counts['unresolved_blocking']} active unresolved, "
        f"{counts['resolved']} resolved, "
        f"{counts['outdated_or_superseded']} outdated/superseded"
    )
    if not summary.threads:
        print("unresolved review threads: pass (no review threads found)")
    elif summary.unresolved_blocking_threads:
        print("unresolved review threads: blocked by active unresolved review threads")
        print("blocking threads:")
        for thread in summary.unresolved_blocking_threads:
            print(f"- {_thread_label(thread)}: {thread.reason}")
    else:
        print("unresolved review threads: pass (only resolved/outdated thread history remains)")
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

    if args.command == "ci-summary":
        return _print_ci_summary(args.target, repo=args.repo, as_json=args.json)

    if args.command == "target-branch-check":
        return _print_target_branch_check(
            args.target,
            repo=args.repo,
            expected_base=args.expected_base,
            as_json=args.json,
        )

    if args.command == "pre-merge":
        return _print_pre_merge(args.target, repo=args.repo, as_json=args.json)

    if args.command == "merge-owner":
        return _print_merge_owner(args.target, repo=args.repo, args=args)

    if args.command == "merge-pr":
        return _print_merge_pr(args.target, repo=args.repo, method=args.method, as_json=args.json)

    if args.command == "unresolved-review-threads":
        return _print_unresolved_review_threads(args.target, repo=args.repo, as_json=args.json)

    return _print_scaffold(args.command, args.target, args.json)


def _thread_label(thread: object) -> str:
    path = getattr(thread, "path", None) or "unknown-path"
    line = getattr(thread, "line", None) or getattr(thread, "start_line", None)
    location = f"{path}:{line}" if line else path
    thread_id = getattr(thread, "id", None)
    return f"{location} ({thread_id})" if thread_id else location


if __name__ == "__main__":
    raise SystemExit(main())
