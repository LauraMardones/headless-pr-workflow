"""``hpw dispatcher <block> <operation> ...`` (ADR-014 → Decision → 2).

Prints exactly one JSON object on one line to stdout, on success and on
failure. Exit codes: ``0`` yes, ``1`` no, ``2`` invalid arguments, ``4``
(``write retry-pending`` only) retrying stopped until the PO's ``/unblock``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Sequence

from .gateway import Gateway, GhGateway, mask_tokens
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
    return parser


def _validate(args: argparse.Namespace) -> None:
    if args.block != "write" or not args.operation:
        raise InvalidArguments("usage: hpw dispatcher write comment|board-status|label|retry-pending ...")
    if not _REPO.match(args.repo or ""):
        raise InvalidArguments("--repo must be <owner/repo>")
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
    return argv[1] if len(argv) > 1 and argv[0] == "write" else ""


def _emit(result: Result) -> int:
    print(json.dumps(result.to_dict(), separators=(", ", ": ")))
    return result.exit_code


def main(argv: Sequence[str], *, gateway_factory: GatewayFactory | None = None, settings: Settings | None = None) -> int:
    """Run ``hpw dispatcher <argv...>``; returns the exit code."""

    argv = list(argv)
    try:
        args = build_parser().parse_args(argv)
        _validate(args)
    except InvalidArguments as exc:
        print(json.dumps({"ok": False, "block": "write", "operation": _operation_of(argv), "result": "invalid",
                          "error": mask_tokens(f"arguments: {exc}")}))  # fmt: skip
        return EXIT_INVALID

    factory = gateway_factory or (lambda repo: GhGateway(repo))
    gateway = factory(args.repo)
    settings = settings or Settings.from_env()

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
