"""Content assertions for the decision-blocker resume contract in
``docs/PROJECT-STATUS.md`` (issue #262; the next-step field, issue #605).

These deterministic assertions anchor the load-bearing rules of the
normative "## Blocked Declaration" template and the Resolution protocol so
they cannot silently drift away from what
``check_and_resolve_decision_blocker_comment()`` in
``scripts/dispatcher-invoke.sh`` actually implements: the declaration
template must itself carry the ``Next step`` field, one per option for a
decision (an executor following the template "exactly" must not be able to
omit it),
and the Resolution section must describe the automated, standalone-line
detection rule.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _template_block() -> str:
    document = read("docs/PROJECT-STATUS.md")
    start = document.index("## Blocked Declaration")
    return document[start : document.index("```", start)]


def test_declaration_template_includes_next_step_fields_inside_the_fenced_block() -> None:
    template = _template_block()

    assert "\nNext step: " in template, (
        "the Next step field must be part of the normative template block "
        "itself, not only prose below it (issue #605)"
    )
    assert "\nNext step <letter>: " in template, (
        "a Type: decision declaration names one next step per option (issue #605)"
    )
    assert "hpw next-step render" in template


def test_next_step_field_covers_every_blocker_type() -> None:
    template = _template_block()
    plain = next(line for line in template.splitlines() if line.startswith("Next step: "))
    per_option = next(line for line in template.splitlines() if line.startswith("Next step <letter>: "))

    for kind in ("dependency", "external", "conflict", "resource"):
        assert kind in plain
    assert "decision" in per_option
    assert "one line per option" in per_option


def test_resume_instruction_is_no_longer_a_template_field() -> None:
    template = _template_block()

    assert "Resume instruction:" not in template
    document = read("docs/PROJECT-STATUS.md")
    decision_start = document.index("### Decision blockers")
    decision_section = document[decision_start : document.index("### Monitoring", decision_start)]
    assert "Resume instruction" not in decision_section
    assert "Next step <letter>" in decision_section


def test_unblock_rule_stays_discoverable_from_the_field_rules() -> None:
    document = read("docs/PROJECT-STATUS.md")
    start = document.index("All fields shown for the selected blocker type")
    paragraph = document[start : document.index("### Decision blockers", start)]

    assert "/unblock" in paragraph
    assert '"Next step"' in paragraph
    assert "docs/NEXT-STEP-KINDS.md" in paragraph


def test_resolution_section_documents_automated_unblock_detection() -> None:
    document = read("docs/PROJECT-STATUS.md")

    assert "### Resolution" in document
    resolution_start = document.index("### Resolution")
    resolution_end = document.index("### Cascading blocked rules", resolution_start)
    resolution_section = document[resolution_start:resolution_end]

    assert "/unblock" in resolution_section
    assert "automatically" in resolution_section
    assert "Ready to implement" in resolution_section
    assert "No manual board-status change is required" in resolution_section


def test_declaration_template_includes_decision_content_fields_after_checked() -> None:
    template = _template_block()

    for field in ("Question:", "Options:", "Recommendation:"):
        assert field in template, f"{field} must be in the fenced template block"
    assert (
        template.index("Checked:")
        < template.index("Question:")
        < template.index("Options:")
        < template.index("Recommendation:")
    )
    assert '<letter>) <option> — <consequence>' in template


def test_decision_content_fields_are_documented_as_decision_only() -> None:
    document = read("docs/PROJECT-STATUS.md")
    start = document.index("All fields shown for the selected blocker type")
    paragraph = document[start : document.index("### Decision blockers", start)]

    for field in ("Question", "Options", "Recommendation"):
        assert f'"{field}"' in paragraph
    assert "omitted for the other four types" in paragraph

    decision_start = document.index("### Decision blockers")
    decision_section = document[decision_start : document.index("### Monitoring", decision_start)]
    for field in ("Question", "Options", "Recommendation"):
        assert field in decision_section


def test_implement_and_review_tell_executor_to_write_decision_fields() -> None:
    for path in (".claude/commands/implement.md", ".claude/commands/review.md"):
        text = read(path)
        for field in ("`Question:`", "`Options:`", "`Recommendation:`"):
            assert field in text, f"{path} must mention {field}"
