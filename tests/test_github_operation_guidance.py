"""Regression tests for assistant-side GitHub operation policy."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    "refine.md",
    "implement.md",
    "review.md",
    "merge.md",
    "cleanup.md",
)


def command_text(name: str) -> str:
    return (ROOT / ".claude" / "commands" / name).read_text(encoding="utf-8")


def test_all_mutating_commands_document_safe_fallback_order() -> None:
    for name in COMMANDS:
        text = command_text(name)
        plugin = text.index("Prefer the GitHub plugin/MCP integration")
        gh = text.index("authenticated `gh` CLI", plugin)
        direct_api = text.index("direct GitHub API request", gh)

        assert plugin < gh < direct_api, name
        assert "GitHub reads and mutations" in text, name
        assert "verify the target repository" in text, name
        assert "Never expose, print, log, persist, or commit GitHub credentials" in text, name
        assert "never bypasses workflow gates" in text, name


def test_review_guidance_preserves_current_head_and_thread_safety() -> None:
    text = command_text("review.md")

    assert "verify the target repository, PR number, and current head SHA" in text
    assert "record the documented separate-session review summary against that same verified SHA" in text
    assert "confirm it belongs to the verified PR" in text
    assert "never resolve a still-actionable thread" in text


def test_review_generates_and_verifies_separate_session_evidence() -> None:
    text = command_text("review.md")

    generate = text.index("hpw review-evidence-text <head-sha> --reviewer-provider <claude|codex>")
    template = text.index("copy this template verbatim, changing only `<head-sha>` and `<claude|codex>`", generate)
    check = text.index("hpw approval-check $ARGUMENTS --repo LauraMardones/headless-pr-workflow", template)

    assert generate < template < check
    assert "post its exact output" in text
    assert "full 40-character head SHA" in text
    assert "never compose or paraphrase these sentences" in text
    assert "as its own block at the top of the review body, above the Session Summary" in text
    assert "never rely on a `Deviation:` line as the only place that carries it" in text
    assert "confirm it prints `separate-session review: accepted`" in text
    assert "post one corrected review on the same head SHA and run the check again" in text
    assert "do not claim the PR is ready to merge" in text
    assert 'leave the story in "In review"' in text
    assert "name the rejected evidence in `--blockers`" in text
    assert "do not use `--next merge`" in text
    assert "only the review evidence needs redoing, not the code" in text
    assert "if `hpw approval-check` cannot be run in the session, say so with `--deviation`" in text
    assert "the verbatim-template rule still applies" in text


def test_merge_fallback_requires_fresh_gates() -> None:
    text = command_text("merge.md")

    assert "Refresh the head SHA and every required merge gate immediately before merging" in text
    assert "Merge only if" in text
    assert "all merge gates pass" in text


def test_merge_graphql_preflight_precedes_every_mutation() -> None:
    text = command_text("merge.md")

    preflight = text.index("gh api graphql -f query='{ viewer { login } }'")

    assert preflight < text.index("set the story status")
    assert preflight < text.index("merge the PR")
    assert "exits non-zero, stop" in text
    assert "Make no GitHub mutation" in text
    assert "GitHub Actions or a local session" in text
    assert "Do not fall back to the GitHub plugin/MCP integration or REST for the merge gate" in text


def test_implement_and_review_status_writes_are_best_effort() -> None:
    for name in ("implement.md", "review.md"):
        text = command_text(name)

        assert "Board status writes are best-effort" in text, name
        assert "do not stop: continue the command" in text, name
        assert "record the skipped status change in the Session Summary with `--deviation`" in text, name


def test_implement_falls_back_to_issue_body_when_ac_summary_lacks_gh() -> None:
    text = command_text("implement.md")

    assert "exits `1` because `gh` or GraphQL is unavailable" in text
    assert "read the AC/DoD checklist from the issue body" in text
    assert "AC/DoD coverage must still be verified" in text


def test_review_leaves_unresolvable_thread_open() -> None:
    text = command_text("review.md")

    assert "leave the thread open" in text
    assert "list it in the Session Summary with `--deviation`" in text
    assert "never treat it as resolved" in text


def test_agents_guidance_names_gh_graphql_dependency() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    operations = agents.split("## GitHub Operations", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    operations = " ".join(operations.split())

    for step in (
        "board status writes",
        "`scripts/ac-summary.sh`",
        "review-thread resolution",
        "`scripts/merge-gate-summary`",
    ):
        assert step in operations, step
    assert "2.50" in operations
    assert "GitHub Actions and a local session" in operations
    assert "Claude Code cloud session does not" in operations
    assert 'HTTP 403 "GraphQL is not available from Claude Code sessions"' in operations
    assert "Board writes are best-effort" in operations
    assert "The merge gate is never skipped" in operations

    sandbox_row = next(line for line in agents.splitlines() if line.startswith("| `gh` CLI blocked"))
    assert "Board status writes are skipped" in sandbox_row
    assert "`/merge` cannot run there" in sandbox_row


def test_required_outputs_are_transport_agnostic() -> None:
    for name in COMMANDS:
        required_output = command_text(name).split("## Required GitHub Output", maxsplit=1)[1]

        assert "using `mcp__github__" not in required_output, name
        assert "via `mcp__github__" not in required_output, name


def _one_line(text: str) -> str:
    return " ".join(text.split())


def test_implement_requests_one_codex_review_per_finished_head() -> None:
    """ADR-006 (#292): one request on ready, one per fix round, none anywhere else."""

    from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE

    text = command_text("implement.md")
    phrase = f"`{CODEX_REVIEW_TRIGGER_PHRASE}`"

    ready = text.index("mark the PR Ready for review, or explicitly request review")
    in_review = text.index('set the story status to "In review"', ready)
    request = text.index(
        "once the PR is Ready for review, post exactly one PR conversation comment "
        f"whose first line is {phrase}",
        in_review,
    )
    handoff = text.index("generate the handoff with `scripts/session-summary.sh --command implement", request)
    fix_round = text.index(
        "after a round of fixes for Codex findings on an `executor:claude-code-*` story, "
        f"post exactly one new review request (a PR conversation comment whose first line is {phrase}), "
        "after the round's last push and after the story is back in \"In review\"",
        handoff,
    )
    assert ready < in_review < request < handoff < fix_round

    assert "if the story carries an `executor:claude-code-*` label, request the Codex review (ADR-006)" in text
    assert "post no review request on intermediate pushes" in text
    assert "never post one on an issue" in text
    assert "post none for an `executor:codex` story" in text
    assert "write the trigger phrase only as that PR conversation comment" in text
    assert "do not run `/review` for a Claude-implemented story" in text
    # Exactly the two request instructions name the phrase.
    assert text.count(CODEX_REVIEW_TRIGGER_PHRASE) == 2


def test_agents_guidance_keeps_the_codex_trigger_phrase_in_pr_comments_only() -> None:
    from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    section = _one_line(agents.split("## Codex Review Requests", maxsplit=1)[1].split("\n## ", maxsplit=1)[0])

    assert "The Codex review trigger phrase (ADR-006 → Decision) starts a Codex task wherever it is written" in section
    assert "Post it only as a PR conversation comment that requests a review" in section
    assert "only from `/implement` or the `codex-review-bookkeeping` workflow" in section
    assert "Never write it in an issue body, an issue comment, a PR description or any other PR comment" in section
    # The rule is followed where it is stated: these files name ADR-006, not the phrase.
    adr = (ROOT / "docs" / "decisions" / "ADR-006-codex-review.md").read_text(encoding="utf-8")
    assert f"`{CODEX_REVIEW_TRIGGER_PHRASE}`" in adr
    for path in ("AGENTS.md", "docs/HEADLESS-PR-WORKFLOW.md", ".claude/commands/review.md"):
        assert CODEX_REVIEW_TRIGGER_PHRASE not in (ROOT / path).read_text(encoding="utf-8").lower(), path


def test_review_command_states_when_it_applies() -> None:
    text = command_text("review.md")
    section = text.split("## When this command applies", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]

    assert "It remains the review path for `executor:codex` stories, which Claude Opus reviews" in section
    assert "separate-session review under ADR-007 path 2" in section
    assert "A Claude-implemented story (`executor:claude-code-*`) is reviewed by Codex (ADR-006)" in section
    assert "The `codex-review-bookkeeping` workflow then moves the story and posts the review Session Summary" in section
    assert "do not run this command for it unless a path 2 review is explicitly wanted" in section


def test_workflow_docs_describe_the_codex_review_loop() -> None:
    from headless_pr_workflow.codex_review_bookkeeping import (
        DEFAULT_MAX_REQUESTS,
        DEFAULT_RETRY_HOURS,
        MAX_REQUESTS_ENV,
        RETRY_HOURS_ENV,
    )

    workflow_doc = (ROOT / "docs" / "HEADLESS-PR-WORKFLOW.md").read_text(encoding="utf-8")
    loop = workflow_doc.split("### Codex review loop", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]

    # Request on ready.
    assert "it posts one PR conversation comment that starts with the Codex review trigger phrase from ADR-006" in loop
    assert "exactly one new request, after the round's last push and after the story is back in `In review`" in loop
    assert "no request on intermediate pushes, none on an issue, and none for an `executor:codex` story" in loop
    # The job.
    assert "`.github/workflows/codex-review-bookkeeping.yml` runs `hpw codex-review-bookkeeping`" in loop
    assert "It calls no model" in loop
    assert "decides from current facts, so a redelivered event is a no-op" in loop
    assert "that story's board status is exactly `In review`" in loop
    assert "move the story `In review` → `In merge`" in loop
    assert loop.count("move `In review` → `In implementation`") == 2
    # Re-requests and the two blockers.
    assert "Usage-limit reply, or a request with no answer" in loop
    assert "one `Type: external` blocker on the story" in loop
    assert "One `Type: resource` blocker on the story; no further request for that head" in loop
    assert "the story stays in `In review`" in loop
    # The two repository variables, with the defaults the code uses.
    assert f"`{RETRY_HOURS_ENV}` (back-off, default `{DEFAULT_RETRY_HOURS:g}`" in loop
    assert f"`{MAX_REQUESTS_ENV}` (requests the job posts per head, default `{DEFAULT_MAX_REQUESTS}`" in loop
    assert "exact output of `scripts/session-summary.sh --command review`" in loop
    assert "`/review` remains the path for `executor:codex` stories" in loop

    automation_map = (ROOT / "docs" / "PR-AUTOMATION-MAP.md").read_text(encoding="utf-8")
    assert (
        "| `codex-review-bookkeeping` | Move the story and post the Session Summary after a Codex review, "
        "with no model. | orchestrator | P1-high | F-review | action | core | implemented |"
    ) in automation_map
