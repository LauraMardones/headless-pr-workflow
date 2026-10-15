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


def test_waiver_suppression_is_limited_to_out_of_scope_findings() -> None:
    assert_all(".claude/commands/review.md", ["a PO comment that names a Blocker is not a waiver"])
    assert_all("AGENTS.md", ["a Blocker is never waived"])


def test_mixed_round_defers_review_request_until_waiver() -> None:
    assert_all(
        ".claude/commands/implement.md",
        ["when the round also leaves an Out of scope finding unwaived, post no review request after the push"],
    )


def test_implement_fix_round_searches_the_defect_class() -> None:
    assert_all(
        ".claude/commands/implement.md",
        [
            "fix the defect class, not only the reported line",
            "name the class in one phrase",
            "search the PR's own change for every other instance of that class",
            "`git diff <base>...HEAD`",
            "that the changed code calls or that call it",
            "fix every instance that lies in code or text the PR added or changed, in the same round, before pushing",
            "found only in code the PR did not add or change is **Out of scope**",
            "not a reason to widen the PR",
            "one `--deviation` line per Blocker finding",
            '`Class check <finding link>: class "<class>"; checked <locations>; also fixed <locations | none>; out of scope <follow-up link | none>`',
            "also fixed none",
        ],
    )


def test_workflow_doc_describes_the_class_wide_search() -> None:
    assert_all(
        "docs/HEADLESS-PR-WORKFLOW.md",
        ["Fix round fixes the class", "searches the PR's own change", "Class check", "defect class", "in unchanged code is Out of scope"],
    )
