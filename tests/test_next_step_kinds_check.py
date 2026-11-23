"""E4 check of Epic #566 (issue #605): every comment kind that asks the PO
to act is registered in ``docs/NEXT-STEP-KINDS.md`` and names its next step.

The check scans ``scripts/``, ``src/`` and ``.claude/commands/`` for the
producers named by the inventory's markers. It fails when a producer is not
registered, when a ``converted`` kind's example lacks one of the three parts
(read by the shared parser of ``headless_pr_workflow.next_step``), and when
the pending kinds below and the inventory's ``pending`` rows differ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from headless_pr_workflow.next_step import NextStepError, parse_declaration, render


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = "docs/NEXT-STEP-KINDS.md"
# #560 owns slack-failure-stop (issue #503): a Slack message that waits for the PO, outside Epic #566.
FEATURES = {"#560", "#589", "#590", "#591"}
STATUSES = {"converted", "pending"}

# Kinds whose producer is not converted yet. A converting story (#606, #607,
# #608, #612, #613, Feature #591) removes its kinds here and marks them
# `converted` in the inventory.
PENDING_KINDS = frozenset({
    # #607
    "bookkeeping-resource-blocker",
    "bookkeeping-external-blocker",
    "codex-worker-fix-round-blocker",
    # Feature #590 (#613; #612 converted the refine kinds)
    "implement-review-session-summary",
    # Feature #591
    "verify-closure-request",
    "slack-evidence-unwritable",
    "slack-feature-closure-confirmation",
    "slack-epic-closure-approval",
    "slack-dispatch-trigger-stopped",
    "slack-dispatcher-error",
    "slack-red-flow-health",
})

# A string literal that starts a comment with the heading: the heading right
# after a quote, then an escaped "\n" or the end of the line (a shell string
# that goes on on the next line). A reader's `"## Blocked Declaration" in body`
# closes the quote right after the heading and does not match.
_BLOCKER_LITERAL = re.compile(r"""["']## Blocked Declaration(\\n|$)""")
_SESSION_BLOCKER = re.compile(r"Blocked Declaration|Blocked Status Protocol")
_SLACK_EVENT = re.compile(r"^ {8}([a-z_]+)\)\s*$")
_NEXT_ACTION = "Next action:"
_CLOSURE_REQUEST = 'Reply with exactly "yes"'
_SCANNED = ("scripts", "src", ".claude/commands")


@dataclass(frozen=True)
class Kind:
    name: str
    marker: str
    producers: tuple[str, ...]
    feature: str
    status: str
    example: str


def _files(root: Path, top: str):
    base = root / top
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def scan_producers(root: Path) -> set[tuple[str, str]]:
    """Every (marker, producer file) found under root by the inventory's markers."""
    found: set[tuple[str, str]] = set()
    for top in _SCANNED:
        for path in _files(root, top):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(root).as_posix()
            lines = text.splitlines()
            if top == ".claude/commands":
                if path.suffix == ".md" and _SESSION_BLOCKER.search(text):
                    found.add(("blocker", rel))
            elif any(_BLOCKER_LITERAL.search(line) for line in lines):
                found.add(("blocker", rel))
            if _NEXT_ACTION in text:
                found.add(("next-action", rel))
            if _CLOSURE_REQUEST in text:
                found.add(("closure-request", rel))
            if rel == "scripts/slack-notify.sh":
                for line in lines:
                    match = _SLACK_EVENT.match(line)
                    if match:
                        found.add((f"slack:{match.group(1)}", rel))
    return found


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _section_rows(text: str, heading: str) -> list[list[str]]:
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    section = text[start : end if end != -1 else len(text)]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    return [_cells(row) for row in rows[2:]]


def _paths(cell: str) -> tuple[str, ...]:
    return tuple(re.findall(r"`([^`]+)`", cell))


def parse_inventory(text: str) -> tuple[list[Kind], set[tuple[str, str]]]:
    kinds = []
    for cells in _section_rows(text, "Kinds"):
        assert len(cells) == 6, f"a Kinds row needs six cells: {cells}"
        name, marker, producers, feature, status, example = cells
        kinds.append(Kind(name, marker, _paths(producers), feature, status, example))
    excluded = set()
    for cells in _section_rows(text, "Not in scope"):
        assert len(cells) == 3, f"a Not in scope row needs three cells: {cells}"
        for path in _paths(cells[1]):
            excluded.add((cells[0], path))
    return kinds, excluded


def check_inventory(root: Path, text: str, pending: frozenset[str]) -> list[str]:
    """The E4 check; returns every failure, empty when the inventory is complete."""
    kinds, excluded = parse_inventory(text)
    failures = []
    names = [kind.name for kind in kinds]
    for name in sorted({n for n in names if names.count(n) > 1}):
        failures.append(f"kind {name} is listed twice")
    registered = set(excluded)
    for kind in kinds:
        registered.update((kind.marker, path) for path in kind.producers)
        if not kind.producers:
            failures.append(f"kind {kind.name} names no producer")
        if kind.feature not in FEATURES:
            failures.append(f"kind {kind.name} has feature {kind.feature}, not one of {sorted(FEATURES)}")
        if kind.status not in STATUSES:
            failures.append(f"kind {kind.name} has status {kind.status}, not converted or pending")
        if not kind.example:
            failures.append(f"kind {kind.name} has no example")
        if kind.status == "converted":
            try:
                steps = parse_declaration(kind.example.replace("``", "").strip())
            except NextStepError as exc:
                failures.append(f"converted kind {kind.name}: {exc}")
            else:
                if not steps:
                    failures.append(f"converted kind {kind.name}: the example has no next step")
    for marker, path in sorted(registered):
        if not (root / path).is_file():
            failures.append(f"registered producer {path} ({marker}) does not exist")
    for marker, path in sorted(scan_producers(root) - registered):
        failures.append(f"producer {path} ({marker}) is not registered in {INVENTORY}")
    listed_pending = {kind.name for kind in kinds if kind.status == "pending"}
    for name in sorted(listed_pending - pending):
        failures.append(f"pending kind {name} is not named in the check's pending list")
    for name in sorted(pending - listed_pending):
        failures.append(f"the pending list names {name}, which is not a pending kind of {INVENTORY}")
    return failures


def _inventory() -> str:
    return (ROOT / INVENTORY).read_text(encoding="utf-8")


def test_inventory_is_complete_on_this_checkout() -> None:
    assert check_inventory(ROOT, _inventory(), PENDING_KINDS) == []


def test_inventory_covers_every_comment_kind_group_of_the_epic() -> None:
    kinds, _ = parse_inventory(_inventory())
    markers = {kind.marker for kind in kinds}

    assert {"blocker", "next-action", "closure-request"} <= markers
    assert {"slack:po_blocker", "slack:decision_blocker"} <= markers
    assert {kind.feature for kind in kinds} == FEATURES


def test_scan_finds_the_known_producers_and_skips_readers() -> None:
    found = scan_producers(ROOT)

    for path in (
        "scripts/dispatcher-invoke.sh",
        "src/headless_pr_workflow/codex_review_bookkeeping.py",
        "src/headless_pr_workflow/dispatcher/codex_worker.py",
        ".claude/commands/implement.md",
    ):
        assert ("blocker", path) in found
    for reader in (
        "scripts/dispatcher-notify-retry.sh",
        "scripts/lib/conflict-round.sh",
        "scripts/lib/decision-blocker.sh",
        "src/headless_pr_workflow/dispatcher/facts.py",
        "src/headless_pr_workflow/dispatcher/status.py",
        "src/headless_pr_workflow/dispatcher/session_blocker.py",
    ):
        assert ("blocker", reader) not in found


def _synthetic_root(tmp_path: Path) -> Path:
    for path in {path for _, path in parse_inventory(_inventory())[1]} | {
        path for kind in parse_inventory(_inventory())[0] for path in kind.producers
    }:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    return tmp_path


def test_check_fails_on_a_synthetic_unregistered_producer(tmp_path: Path) -> None:
    root = _synthetic_root(tmp_path)
    producer = root / "scripts" / "new-producer.sh"
    producer.write_text('post_comment "$n" "## Blocked Declaration\nType: external\n"\n', encoding="utf-8")

    failures = check_inventory(root, _inventory(), PENDING_KINDS)

    assert failures == [f"producer scripts/new-producer.sh (blocker) is not registered in {INVENTORY}"]


@pytest.mark.parametrize(
    "line",
    [
        'body = "Next action: run /refine 12\\n"',
        "        new_event)",
        "echo 'Reply with exactly \"yes\" to confirm'",
    ],
)
def test_check_fails_on_each_other_synthetic_marker(tmp_path: Path, line: str) -> None:
    root = _synthetic_root(tmp_path)
    if line.strip().endswith(")"):
        (root / "scripts" / "slack-notify.sh").write_text(f"case $event in\n{line}\n", encoding="utf-8")
    else:
        (root / "src" / "new_producer.py").parent.mkdir(parents=True, exist_ok=True)
        (root / "src" / "new_producer.py").write_text(line + "\n", encoding="utf-8")

    failures = check_inventory(root, _inventory(), PENDING_KINDS)

    assert len(failures) == 1 and "is not registered" in failures[0]


_GOOD_STEP = render("/implement", 605, "Claude Code or Codex", "the story starts")


def _with_converted_kind(example: str) -> str:
    row = f"| synthetic-kind | blocker | `scripts/dispatcher-invoke.sh` | #589 | converted | {example} |\n"
    text = _inventory()
    anchor = text.index("\n## Not in scope")
    return text[:anchor].rstrip("\n") + "\n" + row + text[anchor:]


def test_check_passes_on_a_synthetic_converted_kind_with_all_parts() -> None:
    assert check_inventory(ROOT, _with_converted_kind(_GOOD_STEP), PENDING_KINDS) == []


@pytest.mark.parametrize(
    "example",
    [
        _GOOD_STEP.replace("`/implement 605`", "`/implement`"),
        _GOOD_STEP.replace(" in Claude Code or Codex", ""),
        _GOOD_STEP.split("; then")[0] + ".",
        "Unblocked when: the PO decides",
    ],
    ids=["no-number", "no-place", "no-outcome", "no-next-step"],
)
def test_check_fails_on_a_synthetic_converted_kind_missing_a_part(example: str) -> None:
    failures = check_inventory(ROOT, _with_converted_kind(example), PENDING_KINDS)

    assert len(failures) == 1 and failures[0].startswith("converted kind synthetic-kind:")


def test_session_blocker_kinds_and_the_decision_message_are_converted() -> None:
    # Issue #608: the session blockers and the Slack decision_blocker message
    # carry the next step; none of them is pending any more.
    kinds, _ = parse_inventory(_inventory())
    converted_by_608 = {
        "refine-decision-blocker", "implement-decision-blocker", "implement-conflict-blocker",
        "implement-other-blocker", "implement-waiver-blocker", "review-decision-blocker",
        "review-waiver-blocker", "slack-decision-blocker",
    }
    status = {kind.name: kind.status for kind in kinds}
    assert {name: status[name] for name in converted_by_608} == dict.fromkeys(converted_by_608, "converted")
    assert not converted_by_608 & PENDING_KINDS


def test_check_fails_when_a_pending_kind_is_not_listed_by_name() -> None:
    failures = check_inventory(ROOT, _inventory(), PENDING_KINDS - {"slack-dispatcher-error"})

    assert failures == ["pending kind slack-dispatcher-error is not named in the check's pending list"]


def test_check_fails_when_the_pending_list_names_a_converted_kind() -> None:
    text = _inventory().replace("| slack-dispatcher-error | slack:dispatcher_error | `scripts/slack-notify.sh` | #591 | pending |",
                                "| slack-dispatcher-error | slack:dispatcher_error | `scripts/slack-notify.sh` | #591 | converted |")
    text = text.replace('"Dispatcher Error" with the last action',
                        _GOOD_STEP)

    failures = check_inventory(ROOT, text, PENDING_KINDS)

    assert failures == [f"the pending list names slack-dispatcher-error, which is not a pending kind of {INVENTORY}"]


@pytest.mark.parametrize(
    "path",
    [
        "src/headless_pr_workflow/dispatcher",
        "scripts/dispatcher-notify-retry.sh",
    ],
)
def test_declaration_readers_do_not_read_the_changed_field(path: str) -> None:
    # The readers use Type:, Declared by:, Unblocked when: and the /unblock
    # line only; neither the former Resume instruction field nor the new
    # Next step field is read (issue #605 → Assumptions), except that the
    # notify-retry scan reads Next step (issue #606).
    base = ROOT / path
    # codex_worker.py is a producer (fix_round_blocker, converted by #607), not a reader.
    files = sorted(f for f in base.rglob("*.py") if f.name != "codex_worker.py") if base.is_dir() else [base]
    assert files
    for file in files:
        text = file.read_text(encoding="utf-8")
        # scripts/dispatcher-notify-retry.sh reads the Next step line of a job
        # declaration into the po_blocker context (issue #606).
        labels = ("Resume instruction",) if file.name == "dispatcher-notify-retry.sh" else ("Resume instruction", "Next step")
        for label in labels:
            assert label not in text, f"{file.relative_to(ROOT)} reads {label}"
