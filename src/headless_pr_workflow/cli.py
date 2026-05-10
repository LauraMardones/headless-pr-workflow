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
from .merge_pr import MERGE_METHODS, PostMergeVerification, run_github_merge, summarize_merge_pr
from .pre_merge import summarize_pre_merge
from .pr_takeover import summarize_pr_takeover
from .re_review_needed import summarize_re_review_needed
from .review_delta import comparison_failure_summary, fetch_commit_comparison, select_review_delta_baseline, summarize_review_delta
from .review_sha import summarize_review_sha
from .target_branch import summarize_target_branch
from .worktree_status import summarize_worktree_status


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
                help="Merge method to use for dry-run or live execution.",
            )
            command_parser.add_argument(
                "--execute",
                action="store_true",
                help=(
                    "Perform the GitHub merge after fresh readiness gates pass. "
                    "No --admin, --auto, or branch deletion behavior is supported."
                ),
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


def _print_re_review_needed(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    summary = summarize_re_review_needed(context)

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
    print(f"solo-maintainer override: {summary.solo_override.status}")
    print(f"approval source: {summary.approval_source or 'none'}")
    print(f"satisfied by: {summary.satisfied_by or 'none'}")
    print(f"re-review needed: {str(summary.re_review_needed).lower()}")
    if summary.blocking_reasons:
        print("blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    print(f"hard gate passed: {str(summary.hard_gate_passed).lower()}")
    return 0 if summary.hard_gate_passed else 1


def _print_review_delta(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    baseline = select_review_delta_baseline(context)
    comparison = None
    if baseline is not None and baseline.sha != context.head_ref_oid:
        repo_name = repo or context.head_repository
        if not repo_name:
            summary = comparison_failure_summary(context, "missing-repository")
            _print_review_delta_summary(summary, as_json=as_json)
            return 1
        try:
            comparison = fetch_commit_comparison(repo_name, baseline.sha, context.head_ref_oid)
        except GHCommandError as error:
            summary = comparison_failure_summary(context, error.error)
            if as_json:
                payload = summary.to_dict()
                payload["command"] = error.command
                payload["returncode"] = error.returncode
                payload["stderr"] = error.stderr
                print(json.dumps(payload, indent=2))
            else:
                _print_review_delta_summary(summary, as_json=False)
                print(error, file=sys.stderr)
            return 1

    summary = summarize_review_delta(context, comparison)
    _print_review_delta_summary(summary, as_json=as_json)
    return 0 if summary.report_generated else 1


def _print_review_delta_summary(summary, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return

    print(f"PR #{summary.number}: {summary.title}")
    print(f"url: {summary.url}")
    print(f"repository: {summary.repository or 'unknown'}")
    print(f"baseline sha: {summary.baseline.sha if summary.baseline else 'none'}")
    print(f"baseline source: {summary.baseline.source if summary.baseline else 'none'}")
    print(f"baseline review state: {summary.baseline.review_state if summary.baseline else 'none'}")
    print(f"baseline review author: {summary.baseline.review_author if summary.baseline else 'none'}")
    print(f"current head sha: {summary.current_head_sha or 'unknown'}")
    print(f"head ref: {summary.head_ref_name or 'unknown'}")
    print(f"delta exists: {str(summary.delta_exists).lower()}")
    print(f"status: {summary.status}")
    if summary.changed_file_count is not None:
        print(f"changed files: {summary.changed_file_count}")
    if summary.additions is not None:
        print(f"additions: {summary.additions}")
    if summary.deletions is not None:
        print(f"deletions: {summary.deletions}")
    if summary.files:
        print("files:")
        for file in summary.files:
            additions = "unknown" if file.additions is None else str(file.additions)
            deletions = "unknown" if file.deletions is None else str(file.deletions)
            print(f"- {file.path} ({file.status or 'unknown'}, +{additions}/-{deletions})")
    if summary.messages:
        print("messages:")
        for message in summary.messages:
            print(f"- {message}")


def _print_pr_takeover(target: str | None, *, repo: str | None, as_json: bool) -> int:
    try:
        context = fetch_pr_context(target, repo=repo)
        expected_base_ref_name = fetch_repo_default_branch(repo=repo)
        required_checks = fetch_required_status_check_context(repo or context.head_repository or "", expected_base_ref_name)
        raw_threads = fetch_review_threads_for_context(context, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    repository = repo or context.head_repository
    approval = summarize_approval_check(context)
    ci = summarize_ci(context, required_checks=required_checks)
    review_threads = summarize_review_threads(context, raw_threads)
    merge_readiness = summarize_pre_merge(
        context,
        expected_base_ref_name=expected_base_ref_name,
        required_checks=required_checks,
        review_threads=review_threads,
    )
    summary = summarize_pr_takeover(
        context,
        repository=repository,
        approval=approval,
        ci=ci,
        review_threads=review_threads,
        merge_readiness=merge_readiness,
    )

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0

    ctx = summary.context
    counts = ctx.check_counts
    print(f"PR #{ctx.number}: {ctx.title}")
    print(f"url: {ctx.url}")
    print(f"state: {ctx.state}")
    print(f"draft: {str(ctx.is_draft).lower()}")
    print(f"base: {ctx.base_ref_name}")
    print(f"head: {ctx.head_ref_name} ({ctx.head_ref_oid})")
    if repository:
        print(f"repository: {repository}")
    if ctx.labels:
        print(f"labels: {', '.join(ctx.labels)}")
    if ctx.review_requests:
        print(f"review requests: {', '.join(ctx.review_requests)}")
    print()

    print(f"approval status: {approval.approval_status}")
    print(f"latest review sha: {approval.latest_review_sha or 'none'}")
    print(f"latest approval sha: {approval.latest_approval_sha or 'none'}")
    print(f"solo-maintainer override: {approval.solo_override.status}")
    print(f"approval source: {approval.approval_source or 'none'}")
    print(f"satisfied by: {approval.satisfied_by or 'none'}")
    re_review_needed = not approval.hard_gate_passed
    print(f"re-review needed: {str(re_review_needed).lower()}")
    if approval.blocking_reasons:
        print("approval blocking reasons:")
        for reason in approval.blocking_reasons:
            print(f"- {reason}")
    print()

    print(f"status rollup: {ci.status_rollup}")
    print(f"required checks: {ci.required_check_status}")
    print(
        "checks: "
        f"{counts['success']} success, "
        f"{counts['failure']} failure, "
        f"{counts['pending']} pending, "
        f"{counts['skipped']} skipped, "
        f"{counts['unknown']} unknown"
    )
    if ci.messages:
        for message in ci.messages:
            print(f"- {message}")
    print()

    thread_counts = review_threads.thread_counts
    print(
        "review threads: "
        f"{thread_counts['unresolved_blocking']} active unresolved, "
        f"{thread_counts['resolved']} resolved, "
        f"{thread_counts['outdated_or_superseded']} outdated/superseded"
    )
    print()

    print(f"merge readiness: {'pass' if merge_readiness.hard_gate_passed else 'blocked'}")
    if merge_readiness.blocking_reasons:
        print("merge blocking reasons:")
        for reason in merge_readiness.blocking_reasons:
            print(f"- {reason}")
    print()

    na = summary.next_action
    print(f"next action: {na.action_class}")
    print(f"summary: {na.summary}")
    if na.reasons:
        print("reasons:")
        for reason in na.reasons:
            print(f"- {reason}")
    if na.follow_up_commands:
        print("suggested follow-up commands:")
        for cmd in na.follow_up_commands:
            print(f"- {cmd}")

    if summary.warnings:
        print()
        print("warnings:")
        for warning in summary.warnings:
            print(f"- {warning}")

    return 0


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


def _print_merge_pr(target: str | None, *, repo: str | None, method: str, execute: bool, as_json: bool) -> int:
    try:
        pre_merge = _fetch_pre_merge_summary(target, repo=repo)
    except GHCommandError as error:
        return _print_gh_error(error, as_json=as_json)

    mode = "execute" if execute else "dry_run"
    summary = summarize_merge_pr(pre_merge, method=method, mode=mode)

    if execute and pre_merge.hard_gate_passed:
        try:
            merge_result = run_github_merge(
                number=pre_merge.number,
                repo=repo,
                method=method,
                head_sha=pre_merge.head_ref_oid,
            )
        except GHCommandError as error:
            return _print_gh_error(error, as_json=as_json)
        post_merge = None
        if merge_result.ok:
            try:
                post_context = fetch_pr_context(str(pre_merge.number), repo=repo)
            except GHCommandError as error:
                return _print_gh_error(error, as_json=as_json)
            post_merge = PostMergeVerification.from_context(post_context)
        summary = summarize_merge_pr(
            pre_merge,
            method=method,
            mode=mode,
            merge_result=merge_result,
            post_merge=post_merge,
        )

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.execution_succeeded else 1

    print(f"PR #{summary.pre_merge.number}: {summary.pre_merge.title}")
    print(f"url: {summary.pre_merge.url}")
    if summary.dry_run:
        print("mode: dry-run (no GitHub merge mutation will be performed)")
    else:
        print("mode: execute (GitHub merge mutation requires all fresh gates to pass)")
    print(f"selected method: {summary.method}")
    print(f"base branch: {summary.pre_merge.base_ref_name or 'unknown'}")
    print(f"head sha: {summary.pre_merge.head_ref_oid or 'unknown'}")
    print(f"approval source: {summary.pre_merge.approval.approval_source or 'none'}")
    print(f"satisfied by: {summary.pre_merge.approval.satisfied_by or 'none'}")
    print(f"would merge: {str(summary.would_merge).lower()}")
    if summary.merge_result is not None:
        print(f"merge command: {' '.join(summary.merge_result.command)}")
        print(f"merge command return code: {summary.merge_result.returncode}")
        if summary.merge_result.stdout:
            print(f"merge stdout: {summary.merge_result.stdout}")
        if summary.merge_result.stderr:
            print(f"merge stderr: {summary.merge_result.stderr}")
    if summary.post_merge is not None:
        print(f"post-merge state: {summary.post_merge.state}")
        print(f"post-merge verified merged: {str(summary.post_merge.merged).lower()}")
    if summary.blocking_reasons:
        print("blocking reasons:")
        for reason in summary.blocking_reasons:
            print(f"- {reason}")
    return 0 if summary.execution_succeeded else 1


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


def _print_worktree_status(target: str | None, *, as_json: bool) -> int:
    summary = summarize_worktree_status(target)

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.ok else 1

    if not summary.ok:
        message = summary.error["message"] if summary.error else "unable to inspect local Git state"
        print(f"worktree-status: {message}", file=sys.stderr)
        return 1

    print(f"repository root: {summary.repository_root}")
    print(f"worktree path: {summary.worktree_path}")
    if summary.branch.detached:
        print(f"branch: detached HEAD ({summary.head_sha or 'unknown'})")
    else:
        print(f"branch: {summary.branch.name or 'unknown'}")
        print(f"head sha: {summary.head_sha or 'none'}")
    print(f"upstream: {summary.branch.upstream or 'none'}")
    print(f"upstream sha: {summary.branch.upstream_sha or 'none'}")
    if summary.branch.ahead is None or summary.branch.behind is None:
        print("ahead/behind: unavailable")
    else:
        print(f"ahead/behind: {summary.branch.ahead} ahead, {summary.branch.behind} behind")
        print("tracking caveat: ahead/behind counts may be stale until fetch")
    print(
        "changes: "
        f"{len(summary.status.staged)} staged, "
        f"{len(summary.status.unstaged)} unstaged, "
        f"{len(summary.status.untracked)} untracked, "
        f"{len(summary.status.conflicted)} conflicted"
    )
    print(f"unpushed commits: {len(summary.unpushed_commits)}")
    if summary.branch_in_use_by_other_worktree is True:
        print("linked worktree warning: current branch is checked out by another linked worktree")
    elif summary.branch_in_use_by_other_worktree is False:
        print("linked worktree warning: none")
    else:
        print("linked worktree warning: not determinable")
    if summary.warnings:
        print("warnings:")
        for warning in summary.warnings:
            print(f"- {warning}")
    return 0


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

    if args.command == "pr-takeover":
        return _print_pr_takeover(args.target, repo=args.repo, as_json=args.json)

    if args.command == "review-sha":
        return _print_review_sha(args.target, repo=args.repo, as_json=args.json)

    if args.command == "approval-check":
        return _print_approval_check(args.target, repo=args.repo, as_json=args.json)

    if args.command == "re-review-needed":
        return _print_re_review_needed(args.target, repo=args.repo, as_json=args.json)

    if args.command == "review-delta":
        return _print_review_delta(args.target, repo=args.repo, as_json=args.json)

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
        return _print_merge_pr(args.target, repo=args.repo, method=args.method, execute=args.execute, as_json=args.json)

    if args.command == "unresolved-review-threads":
        return _print_unresolved_review_threads(args.target, repo=args.repo, as_json=args.json)

    if args.command == "worktree-status":
        return _print_worktree_status(args.target, as_json=args.json)

    return _print_scaffold(args.command, args.target, args.json)


def _thread_label(thread: object) -> str:
    path = getattr(thread, "path", None) or "unknown-path"
    line = getattr(thread, "line", None) or getattr(thread, "start_line", None)
    location = f"{path}:{line}" if line else path
    thread_id = getattr(thread, "id", None)
    return f"{location} ({thread_id})" if thread_id else location


if __name__ == "__main__":
    raise SystemExit(main())
