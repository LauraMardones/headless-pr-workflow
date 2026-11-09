"""``hpw dispatcher <block> <operation> ...`` (ADR-014 → Decision → 2).

Prints exactly one JSON object on one line to stdout, on success and on
failure. Exit codes: ``0`` yes, ``1`` no, ``2`` invalid arguments, ``3``
(``marker``, ``state``, ``facts``, ``scheduler`` and ``status``) unreadable, ``4`` (``write retry-pending`` only) retrying
stopped until the PO's ``/unblock``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Sequence

from .board_sync import sync_board
from .facts import read_facts
from .gateway import Gateway, GhGateway, mask_tokens
from .marker import AUTHORS, DEFAULT_AUTHOR, InvalidMarker, list_markers, marker_holds, marker_text, whoami, write_marker
from .scheduler import next_action
from .state import FILE_NAMES, read_pending, read_state, write_state
from .status import read_status
from .write import (
    DEFAULT_ATTEMPTS,
    EXIT_INVALID,
    Result,
    Settings,
    retry_pending,
    write_board_status,
    write_comment,
    write_label,
)

GatewayFactory = Callable[[str], Gateway]

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_OR_DASH = re.compile(r"^(-|[0-9a-fA-F]{7,40})$")
_ITEM = re.compile(r"^[A-Za-z0-9_.-]+$")


class InvalidArguments(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # noqa: D401 - argparse hook
        raise InvalidArguments(message)


def _positive(text: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", text or ""):
        raise argparse.ArgumentTypeError(f"must be a positive integer: {text!r}")
    return int(text)


def _issue_list(text: str) -> list[int]:
    if not (text or "").strip():
        return []
    return [_positive(part.strip()) for part in text.split(",")]


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="hpw dispatcher", add_help=False)
    blocks = parser.add_subparsers(dest="block", parser_class=_Parser)
    write = blocks.add_parser("write", add_help=False)
    operations = write.add_subparsers(dest="operation", parser_class=_Parser)

    def common(sub: argparse.ArgumentParser, *, attempts: bool = True) -> None:
        sub.add_argument("--repo", required=True)
        if attempts:
            sub.add_argument("--attempts", type=_positive, default=DEFAULT_ATTEMPTS)
        sub.add_argument("--dry-run", action="store_true")

    comment = operations.add_parser("comment", add_help=False)
    comment.add_argument("--number", type=_positive, required=True)
    comment.add_argument("--body-file", required=True)
    comment.add_argument("--evidence-item")
    comment.add_argument("--evidence-head")
    common(comment)

    board = operations.add_parser("board-status", add_help=False)
    board.add_argument("--issue", type=_positive, required=True)
    board.add_argument("--status", required=True)
    common(board)

    label = operations.add_parser("label", add_help=False)
    label.add_argument("--number", type=_positive, required=True)
    action = label.add_mutually_exclusive_group(required=True)
    action.add_argument("--add")
    action.add_argument("--remove")
    common(label)

    pending = operations.add_parser("retry-pending", add_help=False)
    pending.add_argument("--target", type=_positive, required=True)
    pending.add_argument("--item", required=True)
    pending.add_argument("--ref", required=True)
    pending.add_argument("--story", type=_positive, required=True)
    pending.add_argument("--expected-head")
    common(pending, attempts=False)

    facts = blocks.add_parser("facts", add_help=False)
    facts_ops = facts.add_subparsers(dest="operation", parser_class=_Parser)
    f_read = facts_ops.add_parser("read", add_help=False)
    f_read.add_argument("--repo", required=True)
    f_read.add_argument("--epic", type=_positive)
    f_read.add_argument("--dry-run", action="store_true")  # accepted: the command never writes

    scheduler = blocks.add_parser("scheduler", add_help=False)
    scheduler_ops = scheduler.add_subparsers(dest="operation", parser_class=_Parser)
    s_next = scheduler_ops.add_parser("next", add_help=False)
    s_next.add_argument("--repo", required=True)
    s_next.add_argument("--facts-file")
    s_next.add_argument("--in-implementation", type=_issue_list, default=[])
    s_next.add_argument("--in-flight", type=_issue_list, default=[])
    s_next.add_argument("--exclude", type=_issue_list, default=[])
    s_next.add_argument("--dry-run", action="store_true")  # accepted: the command never writes

    status = blocks.add_parser("status", add_help=False)
    status_ops = status.add_subparsers(dest="operation", parser_class=_Parser)
    st_read = status_ops.add_parser("read", add_help=False)
    st_read.add_argument("--repo", required=True)
    st_read.add_argument("--epic", type=_positive)
    st_read.add_argument("--dry-run", action="store_true")  # accepted: the command never writes
    st_sync = status_ops.add_parser("sync", add_help=False)
    st_sync.add_argument("--repo", required=True)
    st_sync.add_argument("--epic", type=_positive)
    st_sync.add_argument("--attempts", type=_positive, default=DEFAULT_ATTEMPTS)
    st_sync.add_argument("--dry-run", action="store_true")

    marker = blocks.add_parser("marker", add_help=False)
    marker_ops = marker.add_subparsers(dest="operation", parser_class=_Parser)

    def author_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--author", choices=AUTHORS, default=DEFAULT_AUTHOR)

    m_write = marker_ops.add_parser("write", add_help=False)
    m_write.add_argument("--number", type=_positive, required=True)
    m_write.add_argument("--action", required=True)
    m_write.add_argument("--field", action="append", default=[])
    m_write.add_argument("--body-file")
    m_write.add_argument("--once", action="store_true")
    common(m_write)

    m_holds = marker_ops.add_parser("holds", add_help=False)
    m_holds.add_argument("--number", type=_positive, required=True)
    m_holds.add_argument("--marker", required=True)
    m_holds.add_argument("--cancel", default="")
    author_arg(m_holds)
    common(m_holds, attempts=False)

    m_list = marker_ops.add_parser("list", add_help=False)
    m_list.add_argument("--number", type=_positive, required=True)
    m_list.add_argument("--marker", default="")
    author_arg(m_list)
    common(m_list, attempts=False)

    m_who = marker_ops.add_parser("whoami", add_help=False)
    common(m_who, attempts=False)

    state = blocks.add_parser("state", add_help=False)
    state_ops = state.add_subparsers(dest="operation", parser_class=_Parser)
    s_read = state_ops.add_parser("read", add_help=False)
    s_read.add_argument("--name", required=True)
    s_read.add_argument("--repo", default="")
    s_write = state_ops.add_parser("write", add_help=False)
    s_write.add_argument("--name", required=True)
    s_write.add_argument("--data-file", required=True)
    s_write.add_argument("--repo", default="")
    s_write.add_argument("--dry-run", action="store_true")
    s_pending = state_ops.add_parser("read-pending", add_help=False)
    s_pending.add_argument("--number", type=_positive, required=True)
    s_pending.add_argument("--item", required=True)
    s_pending.add_argument("--expected-head")
    common(s_pending, attempts=False)
    return parser


def _fields(args: argparse.Namespace) -> list[tuple[str, str]]:
    pairs = []
    for item in args.field:
        key, sep, value = item.partition("=")
        if not sep:
            raise InvalidArguments(f"--field must be key=value: {item!r}")
        pairs.append((key, value))
    return pairs


def _validate(args: argparse.Namespace) -> None:
    if args.block not in ("write", "marker", "state", "facts", "scheduler", "status") or not args.operation:
        raise InvalidArguments(
            "usage: hpw dispatcher write comment|board-status|label|retry-pending ... "
            "| hpw dispatcher marker write|holds|list|whoami ... "
            "| hpw dispatcher state read|write|read-pending ... "
            "| hpw dispatcher facts read ... "
            "| hpw dispatcher scheduler next ... "
            "| hpw dispatcher status read|sync ...")
    # State kept per repository, and the comment state, need the repository; the day files do not.
    repo_free = args.block == "state" and args.operation in ("read", "write") and args.name in (
        "usage-sessions", "usage-notified", "allowance-hold") and not args.repo
    if not repo_free and not _REPO.match(args.repo or ""):
        raise InvalidArguments("--repo must be <owner/repo>")
    if args.block == "state":
        if args.operation in ("read", "write") and args.name not in FILE_NAMES:
            raise InvalidArguments(f"--name must be one of {', '.join(FILE_NAMES)}")
        if args.operation == "read-pending":
            if not _ITEM.match(args.item):
                raise InvalidArguments("--item must be a word such as merge-confirmation")
            if args.expected_head is not None and not _SHA_OR_DASH.match(args.expected_head):
                raise InvalidArguments("--expected-head must be a commit SHA")
        return
    if args.block == "marker":
        if args.operation == "write":
            try:
                marker_text(args.action, _fields(args))
            except InvalidMarker as exc:
                raise InvalidArguments(str(exc)) from None
        if args.operation == "holds" and not args.marker.strip():
            raise InvalidArguments("--marker must not be empty")
        return
    if args.operation == "comment":
        if (args.evidence_item is None) != (args.evidence_head is None):
            raise InvalidArguments("--evidence-item and --evidence-head go together")
        if args.evidence_item is not None and not _ITEM.match(args.evidence_item):
            raise InvalidArguments("--evidence-item must be a word such as merge-confirmation")
        if args.evidence_head is not None and not _SHA_OR_DASH.match(args.evidence_head):
            raise InvalidArguments("--evidence-head must be a commit SHA or -")
    if args.operation == "board-status" and not args.status.strip():
        raise InvalidArguments("--status must not be empty")
    if args.operation == "label" and not (args.add or args.remove or "").strip():
        raise InvalidArguments("the label name must not be empty")
    if args.operation == "retry-pending":
        if not _ITEM.match(args.item) or not _ITEM.match(args.ref):
            raise InvalidArguments("--item and --ref must be words such as merge-owner and pr12")
        if args.expected_head is not None and not _SHA_OR_DASH.match(args.expected_head):
            raise InvalidArguments("--expected-head must be a commit SHA")


def _operation_of(argv: Sequence[str]) -> str:
    return argv[1] if len(argv) > 1 and argv[0] in ("write", "marker", "state", "facts", "scheduler", "status") else ""


def _block_of(argv: Sequence[str]) -> str:
    return argv[0] if argv and argv[0] in ("marker", "state", "facts", "scheduler", "status") else "write"


def _emit(result: Result) -> int:
    print(json.dumps(result.to_dict(), separators=(", ", ": ")))
    return result.exit_code


def _run_marker(args: argparse.Namespace, gateway: Gateway, settings: Settings) -> int:
    if args.operation == "whoami":
        return _emit(whoami(gateway))
    if args.operation == "list":
        return _emit(list_markers(gateway, args.number, marker=args.marker, author=args.author))
    if args.operation == "holds":
        return _emit(marker_holds(gateway, args.number, args.marker, cancel=args.cancel, author=args.author,
                                  settings=settings))  # fmt: skip
    body = ""
    if args.body_file:
        try:
            body = Path(args.body_file).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _emit(Result("write", "failed", error=f"read body file: {exc}", fields={"attempts": 0}, block="marker"))
    return _emit(write_marker(gateway, args.number, args.action, _fields(args), body=body, once=args.once,
                              attempts=args.attempts, dry_run=args.dry_run, settings=settings))  # fmt: skip


def _run_state(args: argparse.Namespace, gateway: Gateway) -> int:
    if args.operation == "read":
        return _emit(read_state(args.name, args.repo))
    if args.operation == "read-pending":
        return _emit(read_pending(gateway, args.number, args.item, expected_head=args.expected_head))
    try:
        text = Path(args.data_file).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _emit(Result("write", "failed", error=f"read data file: {exc}", fields={"name": args.name}, block="state"))
    return _emit(write_state(args.name, args.repo, text, dry_run=args.dry_run))


def _run_scheduler(args: argparse.Namespace, gateway: Gateway) -> int:
    facts = None
    if args.facts_file:
        try:
            facts = json.loads(Path(args.facts_file).read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            facts = {"result": "unreadable", "error": f"read facts file: {exc}"}
    return _emit(next_action(None if facts is not None else gateway, args.repo,
                             in_implementation=args.in_implementation, exclude=args.exclude, facts=facts,
                             in_flight=args.in_flight))  # fmt: skip


def main(argv: Sequence[str], *, gateway_factory: GatewayFactory | None = None, settings: Settings | None = None) -> int:
    """Run ``hpw dispatcher <argv...>``; returns the exit code."""

    argv = list(argv)
    try:
        args = build_parser().parse_args(argv)
        _validate(args)
    except InvalidArguments as exc:
        print(json.dumps({"ok": False, "block": _block_of(argv), "operation": _operation_of(argv), "result": "invalid",
                          "error": mask_tokens(f"arguments: {exc}")}))  # fmt: skip
        return EXIT_INVALID

    factory = gateway_factory or (lambda repo: GhGateway(repo))
    gateway = factory(args.repo or "")
    settings = settings or Settings.from_env()

    if args.block == "marker":
        return _run_marker(args, gateway, settings)
    if args.block == "state":
        return _run_state(args, gateway)
    if args.block == "facts":
        return _emit(read_facts(gateway, args.repo, epic=args.epic))
    if args.block == "scheduler":
        return _run_scheduler(args, gateway)
    if args.block == "status":
        if args.operation == "sync":
            return _emit(sync_board(gateway, args.repo, epic=args.epic, attempts=args.attempts, dry_run=args.dry_run,
                                    settings=settings))  # fmt: skip
        return _emit(read_status(gateway, args.repo, epic=args.epic))

    if args.operation == "comment":
        try:
            body = Path(args.body_file).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _emit(Result("comment", "failed", error=f"read body file: {exc}", fields={"attempts": 0}))
        if not body.strip():
            print(json.dumps({"ok": False, "block": "write", "operation": "comment", "result": "invalid",
                              "error": "arguments: the body file is empty"}))  # fmt: skip
            return EXIT_INVALID
        return _emit(write_comment(
            gateway, args.number, body, attempts=args.attempts, evidence_item=args.evidence_item,
            evidence_head=args.evidence_head, dry_run=args.dry_run, settings=settings,
        ))  # fmt: skip
    if args.operation == "board-status":
        return _emit(write_board_status(gateway, args.issue, args.status, attempts=args.attempts,
                                        dry_run=args.dry_run, settings=settings))  # fmt: skip
    if args.operation == "label":
        name = args.add if args.add is not None else args.remove
        return _emit(write_label(gateway, args.number, name, add=args.add is not None, attempts=args.attempts,
                                 dry_run=args.dry_run, settings=settings))  # fmt: skip
    return _emit(retry_pending(gateway, args.target, args.item, args.ref, args.story,
                               expected_head=args.expected_head, dry_run=args.dry_run, settings=settings))  # fmt: skip


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
