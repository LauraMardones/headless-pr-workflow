from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_implement_command_uses_self_review_helpers_and_generated_handoff() -> None:
    command = read(".claude/commands/implement.md")

    assert "scripts/ac-summary.sh --issue $ARGUMENTS" in command
    assert "exit `2` means no checklist was found" in command
    assert "do not copy the extracted AC/DoD checklist" in command
    assert "bash scripts/dispatcher-change-check.sh --files <changed-files...>" in command
    assert "fix every `FAIL` (exit `1`)" in command
    assert "treat exit `2` as an invocation or input blocker" in command
    assert "scripts/session-summary.sh --command implement" in command
    assert "do not append “What was implemented”, “AC coverage”" in command


def test_review_command_uses_generated_handoff_and_phase_next_actions() -> None:
    command = read(".claude/commands/review.md")

    assert "scripts/session-summary.sh --command review" in command
    assert "--next implementation" in command
    assert "--next merge" in command
    assert "Do not add any recap section" in command


def test_merge_command_fails_closed_and_keeps_evidence_separate() -> None:
    command = read(".claude/commands/merge.md")

    assert "python3 scripts/merge-gate-summary --pr $ARGUMENTS" in command
    assert "only exit `0` permits a merge" in command
    assert "exit `1` means" in command
    assert "exit `2` means" in command
    assert "scripts/session-summary.sh --command merge" in command
    assert "Do not place the merge-gate line inside the generated block" in command


def test_refinement_pipeline_documents_compact_safe_handoffs() -> None:
    documentation = read("docs/REFINEMENT-PIPELINE.md")

    assert "## Token-saving conventions" in documentation
    assert "blockers, deviations, residual risk, and decisions" in documentation
    assert "issue and/or PR number" in documentation
    assert "relevant fresh head SHA" in documentation
    assert "checks, blockers, or next action" in documentation
    assert "must not be inserted as an unsupported field" in documentation
