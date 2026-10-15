from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_all(relative_path: str, phrases: list[str]) -> None:
    text = read(relative_path)
    for phrase in phrases:
        assert phrase in text, f"{relative_path}: {phrase}"


def test_review_command_classifies_findings_against_story_scope() -> None:
    assert_all(
        ".claude/commands/review.md",
        [
            "**Blocker**",
            "**Out of scope**",
            "`## Acceptance Criteria`, `## Scope`, `## Out of Scope`",
            "when in doubt, a finding is a Blocker",
            "classifying is not a waiver",
            "only the PO waives",
            "proposed follow-up issue",
            "not listed in `--blockers`",
            "`--deviation`",
            "`Type: decision`",
            "event `COMMENT`",
            "`--blockers none` and `--next implementation`",
            "this session records them itself and does not rely on a later `/implement` round",
            "keep `/merge` from succeeding",
        ],
    )


def test_implement_fix_round_classifies_before_changing_code() -> None:
    assert_all(
        ".claude/commands/implement.md",
        [
            "classify every unresolved finding on the current head",
            "**Out of scope**",
            "do not start a fix",
            "follow-up issue",
            "without resolving it",
            "`Type: decision`",
            "`--deviation`",
            "push nothing and post no review request",
            "`/unblock`",
            'return the story to "In review"; for an `executor:claude-code-*` story post exactly one new review request for the current head',
            "for an `executor:codex` story post none",
            "only the PO waives",
            "the waived thread is resolved by the PO",
        ],
    )


def test_implement_keeps_one_review_request_after_blocker_fixes() -> None:
    assert_all(".claude/commands/implement.md", ["post exactly one new review request"])


def test_agents_review_guidelines_mark_findings() -> None:
    assert_all("AGENTS.md", ["`Blocker`", "`Out of scope`", "waived on the PR"])


def test_workflow_docs_describe_classification_and_waiver() -> None:
    assert_all(
        "docs/HEADLESS-PR-WORKFLOW.md",
        ["Out of scope", "Only the PO waives", "not a waiver", "follow-up issue", "PO also resolves each waived thread"],
    )
    assert_all(
        "docs/MERGE-POLICY.md",
        ["Out of scope", "Only the PO waives", "not a waiver", "follow-up issue"],
    )
