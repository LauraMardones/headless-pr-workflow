"""``docs/PROJECT-STATUS.md`` and ``docs/ADAPTERS.md`` describe the board as a display of facts (issue #479).

The status names, their order and the precedence are copied from ``STATUSES`` in
``src/headless_pr_workflow/dispatcher/status.py``; these tests keep the document in step with
that tuple, and keep the removed statuses, the intent-signal table and the transition table out
of the statuses and the executor routing text.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REMOVED = ("Backlog", "Ready for refinement", "In refinement", "Ready for implementation", "In merge",
           "Ready to merge", "Needs rework")


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def statuses() -> tuple[str, ...]:
    tree = ast.parse(read("src/headless_pr_workflow/dispatcher/status.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "STATUSES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("STATUSES is not defined in status.py")


def statuses_section() -> str:
    document = read("docs/PROJECT-STATUS.md")
    start = document.index("## Statuses")
    return document[start : document.index("## Precedence", start)]


def removed_note() -> str:
    section = statuses_section()
    return section[section.index("### Removed statuses") :]


def test_every_status_of_the_writer_has_a_heading_in_the_same_order() -> None:
    section = statuses_section()
    positions = [section.index(f"### {name}\n") for name in statuses()]

    assert len(statuses()) == 12
    assert positions == sorted(positions), "the headings follow the order of STATUSES in status.py"


def test_each_status_names_its_issue_types_and_its_fact() -> None:
    blocks = {block.split("\n", 1)[0]: block for block in statuses_section().split("\n### ")[1:]}
    for name in statuses():
        assert "**Applies to:**" in blocks[name], name
        assert "**Fact:**" in blocks[name], name


def test_removed_status_names_appear_only_in_the_removed_statuses_note() -> None:
    document = read("docs/PROJECT-STATUS.md")
    outside = document.replace(removed_note(), "")

    for name in REMOVED:
        assert name not in outside, f"{name!r} is named as a status outside the Removed statuses note"
        assert name in removed_note(), f"the Removed statuses note names {name!r}"


def test_removed_status_names_are_not_statuses_in_the_executor_routing_section() -> None:
    adapters = read("docs/ADAPTERS.md")
    routing = adapters[adapters.index("## Executor Routing") : adapters.index("## Decisions")]

    for name in REMOVED:
        assert name not in routing, f"{name!r} is named in Executor Routing"


def test_there_is_no_intent_signal_table_or_transition_table() -> None:
    document = read("docs/PROJECT-STATUS.md")

    assert "Intent signal" not in document
    assert "## Allowed Transitions" not in document
    assert "| From | To |" not in document


def test_precedence_lists_the_derivation_order_of_the_facts() -> None:
    document = read("docs/PROJECT-STATUS.md")
    start = document.index("## Precedence")
    precedence = document[start : document.index("## Refined vs Ready to Implement", start)]

    order = ("**Done**", "**Blocked**", "**In specification**", "**No spec**", "**Refinement failed**",
             "**Not refined**", "**In progress**", "**Mergeable**", "**In review**", "**In implementation**",
             "**Ready to implement**", "**Refined**")
    positions = [precedence.index(name) for name in order]
    assert positions == sorted(positions)


def test_the_board_is_a_display_with_a_single_writer() -> None:
    document = read("docs/PROJECT-STATUS.md")
    start = document.index("## The Board Is a Display")
    display = document[start : document.index("## Statuses", start)]

    assert "`hpw dispatcher status sync`" in display
    assert "No command, executor or person moves a card" in display
    assert "No status authorizes work" in display


def test_the_executor_label_is_set_in_refinement_and_no_executor_pulls_from_a_status() -> None:
    adapters = read("docs/ADAPTERS.md")
    routing = adapters[adapters.index("## Executor Routing") : adapters.index("## Decisions")]

    assert "The `executor:` label is set in refinement" in routing
    assert "no executor pulls from a status" in routing
    assert 'Executors pull only from "Ready for implementation"' not in routing
