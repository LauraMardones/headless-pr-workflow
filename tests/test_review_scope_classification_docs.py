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
            "proposed catch-all issue",
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
            'there is nothing left to fix; for an `executor:claude-code-*` story post exactly one new review request for the current head',
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
        ["when the round also leaves a waiver-kind Out of scope finding unwaived, post no review request after the push"],
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


# Issue #517: two kinds of Out of scope finding; the catch-all kind's thread is resolved by one helper.

CATCH_ALL_KIND = "**catch-all kind**: a finding about code the PR did not add or change (a defect or behaviour in a file or lines outside the PR's own change)"
WAIVER_KIND = "**waiver kind**: an edge case outside the story's acceptance criteria, or an Epic/Feature requirement the story did not take on"


def test_review_and_implement_define_the_two_kinds() -> None:
    for name in (".claude/commands/review.md", ".claude/commands/implement.md"):
        assert_all(
            name,
            [
                CATCH_ALL_KIND,
                WAIVER_KIND,
                "No `Type: decision` waiver blocker is declared for it",
                "a reply without resolving the thread, a `Type: decision` blocker, the PO's waiver, and the PO resolves the thread",
                "when in doubt whether an Out of scope finding is catch-all kind, it is waiver kind",
                "the helper does not recompute it from the diff",
                "hpw resolve-catch-all-thread",
                "--head <current-head-sha>",
                "copied verbatim and never paraphrased",
                "the finding counts as waiver kind for this round",
            ],
        )
    assert_all(".claude/commands/review.md", ["when in doubt, a finding is a Blocker"])
    assert_all(".claude/commands/implement.md", ["when in doubt whether it is Out of scope at all, it is a Blocker"])


def test_review_approves_when_only_resolved_catch_all_findings_are_left() -> None:
    assert_all(
        ".claude/commands/review.md",
        [
            "when the only findings left are catch-all-kind ones and the helper confirmed every one of their threads resolved, nothing waits for the PO: approve, or record the separate-session review summary, as above, so the story moves to `In merge`, and use `--next merge`",
            "if the helper exits non-zero or cannot run (`gh` or GraphQL unavailable), the finding counts as waiver kind for this round",
            "a waiver-kind finding keeps the PR unmergeable until the PO waives it",
            "Never resolve such a thread any other way",
        ],
    )


def test_implement_posts_the_review_request_after_resolved_catch_all_findings() -> None:
    text = read(".claude/commands/implement.md")
    assert "never resolve the thread of an Out of scope finding" not in text
    assert_all(
        ".claude/commands/implement.md",
        [
            "never resolve the thread of a waiver-kind finding, and resolve the thread of a catch-all-kind finding only with `hpw resolve-catch-all-thread`, never any other way",
            "when every Out of scope finding of the round is catch-all kind and the helper confirmed each of its threads resolved, declare no waiver blocker and post the round's single review request after the round's last push",
            "only for an `executor:claude-code-*` story, none for an `executor:codex` story",
            "when the round also leaves a waiver-kind Out of scope finding unwaived, post no review request after the push",
            "at least one of them is an unwaived waiver-kind finding, push nothing and post no review request",
        ],
    )


PATH_1_RESOLUTION = (
    "For a Claude-implemented story, the `codex-review-bookkeeping` job resolves a Codex-opened finding thread from an older head "
    "when Codex's clean result on the current head is accepted by the approval gate and the required checks are green"
)


def test_merge_policy_names_the_second_automated_resolution() -> None:
    text = read("docs/MERGE-POLICY.md")
    assert "The only resolution of a review thread by automation is the one described under Path 1" not in text
    assert PATH_1_RESOLUTION in text
    assert_all(
        "docs/MERGE-POLICY.md",
        [
            "the second resolution path next to Path 1's superseded Codex thread",
            "`hpw resolve-catch-all-thread`",
            "its head SHA equals the `--head` the classifying session passes",
            "the thread belongs to that PR and is unresolved",
            "a reply after the thread's first comment starts with the two fixed lines",
            "`Classification: Out of scope — code the PR did not add or change` and `Catch-all issue: #<n>`",
            "passes the `hpw catch-all-issue link` check",
            "one `resolveReviewThread` mutation, never retried",
            "only when a re-read shows the thread resolved",
            "the finding is handled as waiver kind for that round",
            "Automation resolves a review thread in exactly two cases: a superseded Codex thread, on Codex's own later clean result (Path 1), and the thread of a catch-all-kind finding",
            "Every other thread (a Blocker thread, a waiver-kind thread, a human's thread, a Codex thread of a Blocker) and every other merge gate is unchanged",
        ],
    )


def test_workflow_doc_carries_the_catch_all_resolution_rule() -> None:
    assert_all(
        "docs/HEADLESS-PR-WORKFLOW.md",
        [
            "A catch-all-kind finding (about code the PR did not add or change)",
            "`hpw resolve-catch-all-thread` resolves its thread after fresh checks",
            "when the helper fails or cannot run it is handled as waiver kind for that round",
            "A waiver-kind finding (an edge case outside the acceptance criteria, or an Epic or Feature requirement the story did not take on)",
            "In doubt, a finding is waiver kind, or a Blocker.",
        ],
    )
