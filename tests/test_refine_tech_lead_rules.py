from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ("refine", "refine-epic", "refine-feature")


def read(name: str) -> str:
    return (ROOT / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8")


def rules() -> str:
    text = read("refine")
    start = text.index("## Tech Lead rules")
    return text[start : text.index("\n## Step 2", start)]


def test_ac1_spec_check_runs_before_the_spec_is_used() -> None:
    text = rules()
    assert "python3 scripts/spec-check <spec-path> --ref <commit>" in text
    assert "fetch `main`" in text
    assert "Exit `1`: post a `Type: decision` Blocked Declaration" in text
    assert "names each failing check id" in text
    assert "leave the board and the body at `In refinement`" in text
    assert "Exit `2`: stop, write nothing, and report the spec as unreadable" in text


def test_ac2_no_merged_spec_stops_before_any_write() -> None:
    text = rules()
    assert "specs/<epic>-*/spec.md" in text
    assert "more than one matches" in text
    assert "Edit no issue body, label, board status or comment" in text
    assert "Issues already refined under that Epic are not touched" in text


@pytest.mark.parametrize("name", COMMANDS)
def test_ac3_no_question_to_the_po_inside_the_session(name: str) -> None:
    text = read(name)
    for phrase in (
        "Wait for the PO's response",
        "present its `## Lens Questions` to the user",
        "only after lens questions have been answered",
        "Would you like to incorporate it",
        "ask the PO:",
    ):
        assert phrase not in text, phrase
    assert "Type: decision" in text


def test_ac3_decision_blocker_uses_the_project_status_format() -> None:
    text = rules()
    assert "`## Blocked Declaration` with `Type: decision`" in text
    assert "docs/PROJECT-STATUS.md" in text
    assert "the affected criterion stays unchanged" in text


def test_ac4_resume_after_unblock() -> None:
    text = rules()
    assert "`/unblock` line" in text
    assert "under the issue's `## Decisions` with its date and comment link" in text
    assert "stays blocked until a spec PR with the change merges (ADR-009 item 5)" in text


@pytest.mark.parametrize("name", ["refine-epic", "refine-feature"])
def test_ac5_lens_questions_are_answered_by_the_tech_lead(name: str) -> None:
    text = read(name)
    assert "`## Tech Lead rules` in `.claude/commands/refine.md`" in text
    assert "answer its `## Lens Questions` yourself as the Tech Lead" in text
    assert "record the answers under `## Decisions`" in text
    assert "needs a product choice becomes a `Type: decision` Blocked Declaration" in text


def test_ac5_shared_rule_states_lens_handling() -> None:
    text = rules()
    assert "answers each active lens's `## Lens Questions` in the session" in text
    assert "needs a product choice becomes a decision blocker" in text


def test_ac6_step_0_classifies_usage_findings() -> None:
    text = read("refine")
    step0 = text[text.index("## Step 0") : text.index("## Tech Lead rules")]
    assert "Put no question to the PO in the session" in step0
    assert "Affects scope or an acceptance criterion" in step0
    assert "`Type: decision` Blocked Declaration" in step0
    assert "under `## Decisions` (issue/PR, date, observation)" in step0


@pytest.mark.parametrize("name", ["refine-epic", "refine-feature"])
def test_ac7_body_format_carries_the_spec_record(name: str) -> None:
    text = read(name)
    body = text[text.index("## Issue Body Format") :]
    assert "Spec: [`specs/{EPIC_NUMBER}-{slug}/spec.md`]({blob link at the commit}) at `{commit}`" in body
    assert "``Spec: [`<spec-path>`](<blob link at the commit>) at `<commit>` ``" in rules()
    assert "7–40 hex characters" in rules()


def test_ac9_feature_criterion_format_and_prefix() -> None:
    text = read("refine-feature")
    body = text[text.index("## Issue Body Format") :]
    assert "Criterion prefix: F{k}" in body
    assert "- [ ] **F{k}.{m}** (serves E{n}, ...) — text" in body
    assert "at least one Epic criterion id" in text


def test_ac9_epic_assigns_prefix_by_seed_feature_position() -> None:
    text = read("refine-epic")
    assert "place in the Seed Features list" in text
    assert "never renumbered" in text


def test_ac10_pipeline_gate_names_the_new_checks() -> None:
    text = (ROOT / "docs" / "REFINEMENT-PIPELINE.md").read_text(encoding="utf-8")
    assert "`spec_record`" in text and "`feature_criteria`" in text
