"""Content assertions for the decision-blocker resume contract in
``docs/PROJECT-STATUS.md`` (issue #262).

These deterministic assertions anchor the load-bearing rules of the
normative "## Blocked Declaration" template and the Resolution protocol so
they cannot silently drift away from what
``check_and_resolve_decision_blocker_comment()`` in
``scripts/dispatcher-invoke.sh`` actually implements: the declaration
template must itself disclose the ``/unblock`` resume instruction (an
executor following the template "exactly" must not be able to omit it),
and the Resolution section must describe the automated, standalone-line
detection rule.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_declaration_template_includes_resume_instruction_field() -> None:
    document = read("docs/PROJECT-STATUS.md")

    assert "## Blocked Declaration" in document
    assert "Resume instruction:" in document
    assert "/unblock" in document


def test_declaration_template_resume_instruction_is_inside_the_fenced_block() -> None:
    document = read("docs/PROJECT-STATUS.md")

    start = document.index("## Blocked Declaration")
    fence_end = document.index("```", start)
    template_block = document[start:fence_end]

    assert "Resume instruction:" in template_block, (
        "the Resume instruction field must be part of the normative "
        "template block itself, not only prose below it — an executor "
        "following 'this format exactly' must not be able to omit it"
    )


def test_resolution_section_documents_automated_unblock_detection() -> None:
    document = read("docs/PROJECT-STATUS.md")

    assert "### Resolution" in document
    resolution_start = document.index("### Resolution")
    resolution_end = document.index("### Cascading blocked rules", resolution_start)
    resolution_section = document[resolution_start:resolution_end]

    assert "/unblock" in resolution_section
    assert "automatically" in resolution_section
    assert "Ready for implementation" in resolution_section


def test_decision_blockers_section_requires_resume_field_be_filled_in() -> None:
    document = read("docs/PROJECT-STATUS.md")

    assert "### Decision blockers" in document
    decision_start = document.index("### Decision blockers")
    decision_end = document.index("### Monitoring and escalation", decision_start)
    decision_section = document[decision_start:decision_end]

    assert "Resume instruction" in decision_section
