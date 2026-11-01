"""Content assertions for the pull-based human attention rule (issue #294).

``docs/HEADLESS-PR-WORKFLOW.md`` states that human attention is pulled, not
polled, as a non-negotiable invariant and in its own top-level section, and
``docs/ROLES.md`` → "## Human Approver" points at that section. These tests
fail if any load-bearing part of that statement is removed or drifts: the
invariant bullet, the section heading, the four clause terms, the closing
doorbell line, the gap-is-still-the-rule sentence, or the ROLES reference.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DOC = "docs/HEADLESS-PR-WORKFLOW.md"
ROLES_DOC = "docs/ROLES.md"
SECTION_HEADING = "## Human Attention Is Pulled, Not Polled"
SECTION_ANCHOR = "#human-attention-is-pulled-not-polled"


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def level_two_section(document: str, heading: str) -> str:
    start = document.index(heading)
    end = document.find("\n## ", start + len(heading))
    return document[start:] if end == -1 else document[start:end]


def test_invariant_bullet_states_no_polling_and_links_the_section() -> None:
    invariants = level_two_section(read(WORKFLOW_DOC), "## Non-Negotiable Invariants")

    assert "never requires the Human Approver to poll for state" in invariants
    assert SECTION_ANCHOR in invariants


def test_section_heading_is_top_level() -> None:
    document = read(WORKFLOW_DOC)

    assert SECTION_HEADING + "\n" in document
    assert document.count(SECTION_HEADING) == 1


def test_section_contains_the_principle_title_line() -> None:
    section = level_two_section(read(WORKFLOW_DOC), SECTION_HEADING)

    assert "**Human attention is pulled, not polled.**" in section
    assert "The workflow must never require the Human Approver to poll for state." in section


def test_section_contains_each_of_the_four_clause_terms() -> None:
    section = level_two_section(read(WORKFLOW_DOC), SECTION_HEADING)

    for clause_term in (
        "named escalation type",
        "actionable notification",
        "enough to decide",
        "recorded in GitHub",
    ):
        assert clause_term in section, clause_term


def test_section_contains_the_doorbell_line() -> None:
    section = level_two_section(read(WORKFLOW_DOC), SECTION_HEADING)

    assert "Notifications are the doorbell; GitHub is the record." in section


def test_section_states_a_gap_is_still_the_rule() -> None:
    section = level_two_section(read(WORKFLOW_DOC), SECTION_HEADING)

    assert "A clause marked as a gap or partly in place is still the rule." in section
    assert "New work must not widen the gap" in section


def test_human_approver_section_references_the_guarantee() -> None:
    roles = read(ROLES_DOC)
    human_approver = level_two_section(roles, "## Human Approver")

    assert "Human Attention Is Pulled, Not Polled" in human_approver
    assert "HEADLESS-PR-WORKFLOW.md" + SECTION_ANCHOR in human_approver
    assert "This role is triggered by AI escalation" in human_approver
    assert "does not perform code review" in human_approver
