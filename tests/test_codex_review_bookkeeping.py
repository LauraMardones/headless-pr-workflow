"""Codex review loop: recognizer additions, the bookkeeping decision and its idempotency (#292)."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import datetime, timezone

import pytest

import headless_pr_workflow.codex_review_bookkeeping as bookkeeping
from headless_pr_workflow import cli
from headless_pr_workflow.catalog import find_command
from headless_pr_workflow.ci_summary import CiSummary, summarize_ci
from headless_pr_workflow.codex_review_bookkeeping import (
    ACTION_BLOCKER_EXTERNAL,
    ACTION_BLOCKER_RESOURCE,
    ACTION_MOVE_IN_IMPLEMENTATION,
    ACTION_MOVE_IN_MERGE,
    ACTION_NONE,
    ACTION_REQUEST_REVIEW,
    ALREADY_PRESENT,
    APPLIED,
    AUTOMATION_DEVIATION,
    DRY_RUN,
    NOT_NEEDED,
    BookkeepingConfig,
    BookkeepingError,
    TOKEN_SCOPES_DOC,
    GhCliGateway,
    RemainingThread,
    ResolvedThread,
    ReviewThreadFact,
    StoryBoard,
    action_marker,
    classify_threads,
    decide,
    missing_scope_message,
    open_decision_blocker,
    parse_story_board,
    review_request_body,
    run_bookkeeping,
    run_session_summary,
)
from headless_pr_workflow.github import GHCommandError, PullRequestContext, RequiredStatusChecks, TimelineEvent
from headless_pr_workflow.review_policy import (
    CODEX_REVIEW_TRIGGER_PHRASE,
    classify_codex_event,
    evaluate_codex_review,
    is_codex_review_request,
)

from tests.github_scenarios import (
    CODEX_BOT,
    CODEX_RECEIPT_MARKER,
    CODEX_TASK_REPLY_BODY,
    CODEX_USAGE_LIMIT_BODY,
    HEAD_SHA,
    OLD_SHA,
    build_check,
    build_pr_context,
    codex_clean_body,
    codex_comment,
    codex_findings_review,
    codex_pr_287_clean_body,
    codex_pr_298_findings_review,
    codex_pr_378_clean_receipt_body,
    codex_pr_378_findings_receipt_body,
    codex_status_body,
    commit_event,
    conversation_comment,
    force_push_event,
    linked_story,
)


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
SIX_HOURS_AGO = "2026-10-11T06:00:00Z"
TWO_HOURS_AGO = "2026-10-11T10:00:00Z"
STORY = 290
PR = 123
CLEAN_URL = "https://github.com/owner/repo/pull/123#issuecomment-1"
UNKNOWN_URL = "https://github.com/owner/repo/pull/123#issuecomment-77"
BOARD_OPTIONS = {"In implementation": "opt-impl", "In review": "opt-review", "In merge": "opt-merge"}


def request(created_at: str = TWO_HOURS_AGO, *, body: str | None = None, url: str | None = None) -> TimelineEvent:
    return conversation_comment(
        CODEX_REVIEW_TRIGGER_PHRASE if body is None else body,
        created_at=created_at,
        url=url or f"https://github.com/owner/repo/pull/123#issuecomment-request-{created_at}",
    )


def job_request(ordinal: int, created_at: str = SIX_HOURS_AGO, *, head: str = HEAD_SHA) -> TimelineEvent:
    return request(created_at, body=review_request_body(head, ordinal=ordinal), url=f"https://x/request-{ordinal}")


def usage_limit(created_at: str) -> TimelineEvent:
    return replace(codex_comment(CODEX_USAGE_LIMIT_BODY, url="https://x/usage-limit"), created_at=created_at)


def pr(*events: TimelineEvent, checks: str = "success", **overrides) -> PullRequestContext:
    """An open, ready PR on HEAD_SHA closing one Claude-implemented story, with `events` after the last push."""

    overrides.setdefault("linked_issues", (linked_story("executor:claude-code-sonnet", number=STORY),))
    overrides.setdefault("timeline", (commit_event(OLD_SHA), commit_event(HEAD_SHA), *events))
    statuses = {
        "success": ("COMPLETED", "SUCCESS"),
        "failure": ("COMPLETED", "FAILURE"),
        "pending": ("IN_PROGRESS", None),
    }
    status_checks = ()
    if checks in statuses:
        status, conclusion = statuses[checks]
        status_checks = (build_check(name="pytest", bucket=checks, status=status, conclusion=conclusion),)
    return build_pr_context(number=PR, head_ref_oid=HEAD_SHA, status_checks=status_checks, **overrides)


def ci_of(context: PullRequestContext, *, required: RequiredStatusChecks | None = None) -> CiSummary:
    required = required or RequiredStatusChecks(names=("pytest",), status="configured")
    return summarize_ci(context, required_checks=required)


def decision_for(context: PullRequestContext, *, status: str | None = "In review", now: datetime = NOW, **config):
    return decide(context, story_status=status, ci=ci_of(context), now=now, config=BookkeepingConfig(**config))


class FakeGitHub:
    """In-memory GitHub: comments the job posts and moves it makes are visible to its next read."""

    def __init__(self, context: PullRequestContext, *, status: str | None = "In review", options=None) -> None:
        self.context = context
        self.status = status
        self.options = BOARD_OPTIONS if options is None else options
        self.story_comments: list[str] = []
        self.story_thread: list[dict] = []  # story comments with author and time, for the decision blocker
        self.pr_comments: list[str] = []
        self.moves: list[str] = []
        self.fail_next_move = False
        self.posted_at = "2026-10-11T12:00:00Z"
        self.threads: list[ReviewThreadFact] = []
        self.thread_reads = 0
        self.thread_read_error: BookkeepingError | None = None
        self.resolve_calls: list[str] = []
        self.fail_resolve_of: str | None = None
        self.order: list[str] = []

    def fetch_review_threads(self, context: PullRequestContext) -> tuple[ReviewThreadFact, ...]:
        self.thread_reads += 1
        if self.thread_read_error:
            raise self.thread_read_error
        return tuple(self.threads)

    def resolve_review_thread(self, thread_id: str) -> None:
        if thread_id == self.fail_resolve_of:
            self.fail_resolve_of = None
            raise BookkeepingError("resolve review thread", f"GitHub did not confirm that review thread {thread_id} is resolved")
        self.resolve_calls.append(thread_id)
        self.order.append(f"resolve:{thread_id}")
        self.threads = [replace(thread, is_resolved=True) if thread.id == thread_id else thread for thread in self.threads]

    def fetch_context(self, number: int) -> PullRequestContext:
        return self.context

    def fetch_ci(self, context: PullRequestContext) -> CiSummary:
        return ci_of(context)

    def fetch_story(self, number: int) -> StoryBoard:
        return StoryBoard(number, "Story: scenario", self.status, "item", "project", "field", self.options)

    def fetch_issue_comments(self, number: int) -> tuple[str, ...]:
        return tuple(self.story_comments)

    def fetch_open_decision_blocker(self, number: int) -> str | None:
        blocker = open_decision_blocker(self.story_thread)
        return blocker["url"] if blocker else None

    def post_comment(self, number: int, body: str) -> str:
        if number == PR:
            url = f"https://github.com/owner/repo/pull/{PR}#issuecomment-job-{len(self.pr_comments)}"
            self.pr_comments.append(body)
            self.order.append("summary")
            event = conversation_comment(body, created_at=self.posted_at, url=url)
            self.context = replace(self.context, timeline=(*self.context.timeline, event))
            return url
        self.story_comments.append(body)
        return f"https://github.com/owner/repo/issues/{number}#issuecomment-job-{len(self.story_comments)}"

    def set_story_status(self, story: StoryBoard, status: str) -> None:
        if self.fail_next_move:
            self.fail_next_move = False
            raise BookkeepingError(f"move story #{story.number} to `{status}`", "GitHub did not confirm the board update")
        self.moves.append(status)
        self.order.append("move")
        self.status = status

    def session_summary(self, arguments: tuple[str, ...]) -> str:
        return "## Session Summary\n" + " ".join(arguments) + "\n"

    def list_open_ready_prs(self) -> tuple[int, ...]:
        return (PR,)


# --- Recognizer additions ----------------------------------------------------


def test_classifier_recognizes_the_real_codex_payloads():
    assert classify_codex_event(codex_comment(codex_pr_287_clean_body())) == "clean"
    assert classify_codex_event(codex_comment(CODEX_USAGE_LIMIT_BODY)) == "usage-limit"
    assert classify_codex_event(codex_pr_298_findings_review()) == "findings"
    assert classify_codex_event(codex_comment(CODEX_TASK_REPLY_BODY)) == "unknown"


def test_classifier_recognizes_the_codex_progress_comment():
    running = codex_comment(codex_status_body())
    completed = codex_comment(codex_status_body(status="\u2705 **Completed** 2026-10-11T14:15:10Z"))

    assert classify_codex_event(running) == "status"
    assert classify_codex_event(completed) == "status"
    # Only the bot's own comment counts, and it never counts as a clean result.
    assert classify_codex_event(conversation_comment(codex_status_body(), created_at=TWO_HOURS_AGO)) is None
    assert evaluate_codex_review(pr(request(), running)).status == "missing"


def test_progress_comment_is_not_a_reply_so_the_request_stays_pending():
    # Codex posts the progress comment seconds after the first request and edits it afterwards.
    status = codex_comment(codex_status_body(HEAD_SHA[:7]), url=UNKNOWN_URL)
    waiting = decision_for(pr(request(TWO_HOURS_AGO), status))
    overdue = decision_for(pr(request(SIX_HOURS_AGO), status))
    answered = decision_for(pr(request(), status, codex_pr_298_findings_review(HEAD_SHA)))

    assert (waiting.action, waiting.codex_result) == (ACTION_NONE, "pending")
    assert (overdue.action, overdue.codex_result) == (ACTION_REQUEST_REVIEW, "pending")
    assert (answered.action, answered.codex_result) == (ACTION_MOVE_IN_IMPLEMENTATION, "findings")
    assert decision_for(pr(request(), status, codex_comment())).action == ACTION_MOVE_IN_MERGE


def test_classifier_ignores_other_authors_and_fails_closed_on_unknown_reviews():
    human = conversation_comment(codex_clean_body(), created_at=TWO_HOURS_AGO)
    impostor = codex_comment(codex_clean_body(), author_type="User")
    bare_review = replace(codex_findings_review(HEAD_SHA), body="")

    assert classify_codex_event(human) is None
    assert classify_codex_event(impostor) is None
    assert classify_codex_event(commit_event(HEAD_SHA)) is None
    assert classify_codex_event(bare_review) == "unknown"
    # A findings body posted as a comment, or a clean body submitted as a review, is not a known format.
    assert classify_codex_event(codex_comment(codex_pr_298_findings_review().body)) == "unknown"
    assert classify_codex_event(replace(codex_findings_review(HEAD_SHA), body=codex_clean_body())) == "unknown"


def test_review_request_is_a_non_bot_comment_whose_first_line_is_the_phrase():
    trailer = f"{CODEX_REVIEW_TRIGGER_PHRASE}\n\n---\n_Generated by [Claude Code](https://claude.ai/code)_"

    assert is_codex_review_request(request())
    assert is_codex_review_request(request(body=trailer))
    assert is_codex_review_request(job_request(1))
    assert not is_codex_review_request(request(body=f"Please {CODEX_REVIEW_TRIGGER_PHRASE}"))
    assert not is_codex_review_request(request(body=f"Note\n{CODEX_REVIEW_TRIGGER_PHRASE}"))
    assert not is_codex_review_request(request(body=f"{CODEX_REVIEW_TRIGGER_PHRASE} this file"))
    assert not is_codex_review_request(codex_comment(CODEX_REVIEW_TRIGGER_PHRASE))
    assert not is_codex_review_request(replace(codex_findings_review(HEAD_SHA), body=CODEX_REVIEW_TRIGGER_PHRASE))


def test_job_request_starts_with_the_phrase_and_carries_the_marker_on_a_later_line():
    lines = review_request_body(HEAD_SHA, ordinal=2).splitlines()

    assert lines[0] == CODEX_REVIEW_TRIGGER_PHRASE
    assert lines[-1] == action_marker(ACTION_REQUEST_REVIEW, HEAD_SHA, source="request-2")
    assert lines[-1].startswith("<!--") and lines[-1].endswith("-->")


# --- #382: receipt marker ------------------------------------------------------


def receipt_findings_review(body: str) -> TimelineEvent:
    return replace(codex_findings_review(HEAD_SHA), body=body)


def test_receipt_format_clean_comment_from_pr_378_is_a_clean_result():
    clean = codex_comment(codex_pr_378_clean_receipt_body(HEAD_SHA[:10]))
    summary = evaluate_codex_review(pr(request(), clean))
    decision = decision_for(pr(request(), clean))

    assert classify_codex_event(codex_comment(codex_pr_378_clean_receipt_body())) == "clean"
    assert (summary.status, summary.reason, summary.comment_url) == ("accepted", None, CLEAN_URL)
    assert summary.reviewed_commit_prefix == HEAD_SHA[:10]
    assert (decision.action, decision.codex_result) == (ACTION_MOVE_IN_MERGE, "clean")


def test_receipt_format_findings_review_from_pr_378_is_findings():
    findings = receipt_findings_review(codex_pr_378_findings_receipt_body(HEAD_SHA[:10]))
    decision = decision_for(pr(request(), findings))

    assert classify_codex_event(receipt_findings_review(codex_pr_378_findings_receipt_body())) == "findings"
    assert (decision.action, decision.codex_result) == (ACTION_MOVE_IN_IMPLEMENTATION, "findings")


def _receipt_clean(prefix: str = HEAD_SHA[:10]) -> str:
    return codex_pr_378_clean_receipt_body(prefix)


def _receipt_findings(prefix: str = HEAD_SHA[:10]) -> str:
    return codex_pr_378_findings_receipt_body(prefix)


UNKNOWN_RECEIPT_COMMENTS = [
    pytest.param(f"{CODEX_RECEIPT_MARKER}\n\nCodex could not complete this request.", id="marker-then-unknown-text"),
    pytest.param(CODEX_RECEIPT_MARKER, id="marker-only"),
    pytest.param(_receipt_clean().replace("receipt:v1", "receipt:v2"), id="v2-marker"),
    pytest.param(f"Note.\n\n{_receipt_clean()}", id="marker-not-at-start"),
    pytest.param(f"{CODEX_RECEIPT_MARKER}\n{_receipt_clean()}", id="marker-twice"),
    pytest.param(f"{CODEX_RECEIPT_MARKER}\n\n{CODEX_USAGE_LIMIT_BODY}", id="marker-then-usage-limit"),
    pytest.param(CODEX_TASK_REPLY_BODY, id="no-marker-unknown-text"),
]


@pytest.mark.parametrize("body", UNKNOWN_RECEIPT_COMMENTS)
def test_comment_in_an_unknown_receipt_format_stays_unknown_and_is_not_accepted(body):
    comment = codex_comment(body)
    summary = evaluate_codex_review(pr(request(), comment))

    assert classify_codex_event(comment) == "unknown"
    assert summary.status == "invalid"
    assert "in a format the recognizer does not know" in summary.reason


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(f"{CODEX_RECEIPT_MARKER}\n\n### Codex could not finish", id="marker-then-unknown-text"),
        pytest.param(CODEX_RECEIPT_MARKER, id="marker-only"),
        pytest.param(_receipt_findings().replace("receipt:v1", "receipt:v2"), id="v2-marker"),
        pytest.param(f"Note.\n\n{_receipt_findings()}", id="marker-not-at-start"),
        pytest.param(f"{CODEX_RECEIPT_MARKER}\n{_receipt_findings()}", id="marker-twice"),
        pytest.param(CODEX_TASK_REPLY_BODY, id="no-marker-unknown-text"),
    ],
)
def test_review_in_an_unknown_receipt_format_stays_unknown(body):
    assert classify_codex_event(receipt_findings_review(body)) == "unknown"


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(conversation_comment(_receipt_clean(), created_at=TWO_HOURS_AGO), id="human"),
        pytest.param(codex_comment(_receipt_clean(), author="github-actions[bot]"), id="other-bot"),
        pytest.param(codex_comment(_receipt_clean(), author_type="User"), id="user-with-the-bot-login"),
    ],
)
def test_receipt_format_clean_body_from_another_account_is_not_accepted(event):
    assert classify_codex_event(event) is None
    assert evaluate_codex_review(pr(request(), event)).status == "missing"


# --- AC3 / AC4: clean result --------------------------------------------------


def test_clean_result_with_green_required_checks_moves_to_in_merge():
    decision = decision_for(pr(request(), codex_comment()))

    assert decision.action == ACTION_MOVE_IN_MERGE
    assert decision.codex_result == "clean"
    assert decision.codex_url == CLEAN_URL
    assert decision.summary_next == "merge"
    assert decision.summary_blockers == "none"
    assert decision.story == STORY and decision.head_sha == HEAD_SHA


def test_clean_result_waits_while_a_required_check_is_running():
    for checks in ("pending", "absent"):
        decision = decision_for(pr(request(), codex_comment(), checks=checks))

        assert decision.action == ACTION_NONE, checks
        assert decision.codex_result == "clean"
        assert "still running" in decision.reason


def test_clean_result_with_a_failed_required_check_moves_to_in_implementation():
    decision = decision_for(pr(request(), codex_comment(), checks="failure"))

    assert decision.action == ACTION_MOVE_IN_IMPLEMENTATION
    assert decision.summary_next == "implementation"
    assert decision.summary_blockers == "required check failed: pytest"
    assert "pytest" in decision.reason


@pytest.mark.parametrize("status", ["unavailable", "policy_required_unconfigured", "policy_inconsistent", "unknown"])
def test_clean_result_fails_when_required_check_state_cannot_be_read(status):
    context = pr(request(), codex_comment())
    # Built directly: conftest pins the repository policy, which would turn `unavailable` into a pass.
    unreadable = replace(ci_of(context), required_check_status=status, required_checks_satisfied=None)

    with pytest.raises(BookkeepingError) as error:
        decide(context, story_status="In review", ci=unreadable, now=NOW)

    assert error.value.step == "read required checks"
    assert f"`{status}`" in error.value.message


def test_clean_result_fails_when_required_checks_were_not_read():
    with pytest.raises(BookkeepingError):
        decide(pr(request(), codex_comment()), story_status="In review", ci=None, now=NOW)


def test_clean_result_agrees_with_the_approval_gate_when_a_usage_limit_reply_follows():
    context = pr(request(), codex_comment(), request(), usage_limit(TWO_HOURS_AGO))

    assert evaluate_codex_review(context).status == "accepted"
    assert decision_for(context).action == ACTION_MOVE_IN_MERGE


def status_comment(created_at: str = "2026-10-15T16:12:13Z", **body) -> TimelineEvent:
    return replace(codex_comment(codex_status_body(HEAD_SHA[:7], **body), url=UNKNOWN_URL), created_at=created_at)


COMPLETED = {"status": "✅ **Completed** 2026-10-15T16:12:13Z"}


@pytest.mark.parametrize("body", [{}, COMPLETED], ids=["running", "completed"])
def test_status_comment_after_a_clean_result_does_not_supersede_it(body):
    # #376: on PR #375 Codex created its status comment 4 s after the clean result.
    clean = replace(codex_comment(), created_at="2026-10-15T16:12:09Z")
    summary = evaluate_codex_review(pr(request(), clean, status_comment(**body)))

    assert (summary.status, summary.reason, summary.comment_url) == ("accepted", None, CLEAN_URL)


def test_status_comment_then_findings_or_unknown_comment_still_supersedes_a_clean_result():
    findings = evaluate_codex_review(pr(request(), codex_comment(), status_comment(), codex_findings_review(HEAD_SHA)))
    unknown = evaluate_codex_review(pr(request(), codex_comment(), status_comment(), codex_comment(CODEX_TASK_REPLY_BODY)))

    assert findings.status == "superseded"
    assert "newer Codex review submission" in findings.reason
    assert unknown.status == "superseded"
    assert "not a clean result" in unknown.reason


def test_status_body_posted_by_another_account_after_a_clean_result_has_no_effect():
    human = conversation_comment(codex_status_body(HEAD_SHA[:7]), created_at=TWO_HOURS_AGO)
    other_bot = codex_comment(codex_status_body(HEAD_SHA[:7]), author="github-actions[bot]")

    assert evaluate_codex_review(pr(request(), codex_comment(), human, other_bot)).status == "accepted"


def test_without_a_clean_result_the_reason_names_the_latest_non_status_codex_comment():
    task_reply = codex_comment(CODEX_TASK_REPLY_BODY, url="https://x/task-reply")
    unknown = evaluate_codex_review(pr(request(), task_reply, status_comment()))
    limited = evaluate_codex_review(pr(request(), usage_limit(TWO_HOURS_AGO), status_comment()))
    only_status = evaluate_codex_review(pr(request(), status_comment(), status_comment(**COMPLETED)))

    assert (unknown.status, unknown.comment_url) == ("invalid", "https://x/task-reply")
    assert "in a format the recognizer does not know" in unknown.reason
    assert (limited.status, limited.comment_url) == ("invalid", "https://x/usage-limit")
    assert "a usage-limit reply" in limited.reason
    assert (only_status.status, only_status.comment_url) == ("missing", None)


def test_bookkeeping_moves_a_clean_result_on_when_the_status_comment_follows_it():
    # The PR #375 timeline: clean result at 16:12:09Z, status comment created at 16:12:13Z.
    clean = replace(codex_comment(), created_at="2026-10-15T16:12:09Z")
    after = decision_for(pr(request(), clean, status_comment()))
    # The usual order: status comment created before the result and edited after it.
    before = decision_for(pr(request(), status_comment("2026-10-15T16:00:00Z", **COMPLETED), clean))

    assert (after.action, after.codex_result, after.codex_url) == (ACTION_MOVE_IN_MERGE, "clean", CLEAN_URL)
    assert (before.action, before.codex_result) == (ACTION_MOVE_IN_MERGE, "clean")


def test_clean_result_after_findings_on_the_same_head_moves_to_in_merge():
    decision = decision_for(pr(request(), codex_findings_review(HEAD_SHA), request(), codex_comment()))

    assert decision.action == ACTION_MOVE_IN_MERGE


def test_requested_changes_block_the_move_to_in_merge_despite_a_clean_codex_result():
    for checks in ("success", "failure"):
        context = pr(request(), codex_comment(), checks=checks, review_decision="CHANGES_REQUESTED")
        decision = decision_for(context)

        assert evaluate_codex_review(context).status == "accepted"
        assert (decision.action, decision.codex_result) == (ACTION_NONE, "clean")
        assert "CHANGES_REQUESTED" in decision.reason
        assert decision.marker is None

    github = FakeGitHub(pr(request(), codex_comment(), review_decision="CHANGES_REQUESTED"))
    assert run_bookkeeping(PR, github=github, now=NOW).applied == NOT_NEEDED
    assert github.pr_comments == [] and github.moves == [] and github.status == "In review"


# --- AC5: findings ------------------------------------------------------------


def test_findings_review_on_the_current_head_moves_to_in_implementation():
    review = codex_pr_298_findings_review(HEAD_SHA)
    decision = decision_for(pr(request(), review))

    assert decision.action == ACTION_MOVE_IN_IMPLEMENTATION
    assert decision.codex_result == "findings"
    assert decision.codex_url == review.url
    assert decision.summary_next == "implementation"
    assert review.url in decision.summary_blockers


def test_findings_review_wins_over_an_earlier_clean_result_on_the_same_head():
    decision = decision_for(pr(request(), codex_comment(), request(), codex_pr_298_findings_review(HEAD_SHA)))

    assert decision.action == ACTION_MOVE_IN_IMPLEMENTATION


# --- AC6 / AC7: usage limit and no answer --------------------------------------


def test_usage_limit_reply_waits_for_the_back_off_then_re_requests_once():
    before = decision_for(pr(request(SIX_HOURS_AGO), usage_limit(TWO_HOURS_AGO)))
    after = decision_for(pr(request(SIX_HOURS_AGO), usage_limit(SIX_HOURS_AGO)))

    assert (before.action, before.codex_result) == (ACTION_NONE, "usage-limit")
    assert before.codex_url == "https://x/usage-limit"
    assert (after.action, after.codex_result) == (ACTION_REQUEST_REVIEW, "usage-limit")


def test_unanswered_request_waits_for_the_back_off_then_re_requests_once():
    before = decision_for(pr(request(TWO_HOURS_AGO)))
    after = decision_for(pr(request(SIX_HOURS_AGO)))

    assert (before.action, before.codex_result) == (ACTION_NONE, "pending")
    assert "pending" in before.reason
    assert (after.action, after.codex_result) == (ACTION_REQUEST_REVIEW, "pending")


def test_back_off_is_measured_from_exactly_the_configured_hours():
    context = pr(request("2026-10-11T07:00:00Z"))

    assert decision_for(context).action == ACTION_REQUEST_REVIEW
    assert decision_for(context, now=NOW.replace(hour=11, minute=59)).action == ACTION_NONE
    assert decision_for(context, retry_hours=6).action == ACTION_NONE
    assert decision_for(pr(request(TWO_HOURS_AGO)), retry_hours=2).action == ACTION_REQUEST_REVIEW


def test_new_request_after_a_usage_limit_reply_is_pending_again():
    decision = decision_for(pr(request(SIX_HOURS_AGO), usage_limit(SIX_HOURS_AGO), job_request(1, TWO_HOURS_AGO)))

    assert (decision.action, decision.codex_result) == (ACTION_NONE, "pending")
    assert decision.requests_posted == 1


# --- AC8: no request for the current head ---------------------------------------


def test_no_request_for_the_current_head_posts_one_request_and_no_move():
    decision = decision_for(pr())

    assert (decision.action, decision.codex_result) == (ACTION_REQUEST_REVIEW, "none")
    assert decision.summary_next is None


def test_codex_result_naming_an_older_commit_posts_one_request_and_no_move():
    stale_clean = codex_comment(codex_clean_body(OLD_SHA[:10]))
    stale_findings = codex_pr_298_findings_review(OLD_SHA)
    before_push = pr(
        timeline=(commit_event(OLD_SHA), request(SIX_HOURS_AGO), codex_comment(codex_clean_body(OLD_SHA[:10])), commit_event(HEAD_SHA))
    )
    force_pushed = pr(
        timeline=(commit_event(OLD_SHA), request(SIX_HOURS_AGO), codex_findings_review(OLD_SHA), force_push_event(before=OLD_SHA, after=HEAD_SHA))
    )

    for context in (pr(stale_clean), pr(stale_findings), before_push, force_pushed):
        decision = decision_for(context)

        assert (decision.action, decision.codex_result) == (ACTION_REQUEST_REVIEW, "none")
        assert decision.requests_posted == 0


# --- AC9: request bound ---------------------------------------------------------


def test_resource_blocker_once_the_job_posted_the_maximum_number_of_requests():
    posted = (request(SIX_HOURS_AGO), job_request(1), job_request(2), job_request(3))
    unanswered = decision_for(pr(*posted))
    usage_limited = decision_for(pr(*posted, usage_limit(SIX_HOURS_AGO)))

    for decision in (unanswered, usage_limited):
        assert decision.action == ACTION_BLOCKER_RESOURCE
        assert decision.requests_posted == 3 and decision.max_requests == 3
        assert decision.marker == action_marker(ACTION_BLOCKER_RESOURCE, HEAD_SHA)


def test_request_bound_counts_only_the_jobs_own_requests_for_the_current_head():
    # Requests by `/implement` and job requests for an older head do not count.
    old_head = (commit_event(OLD_SHA), job_request(1, head=OLD_SHA), job_request(2, head=OLD_SHA), commit_event(HEAD_SHA))
    context = pr(timeline=(*old_head, request(SIX_HOURS_AGO), request(SIX_HOURS_AGO), job_request(1), job_request(2)))
    decision = decision_for(context)

    assert decision.action == ACTION_REQUEST_REVIEW
    assert decision.requests_posted == 2


def test_request_bound_waits_for_the_back_off_and_follows_the_configured_maximum():
    posted = (request(SIX_HOURS_AGO), job_request(1), job_request(2), job_request(3, TWO_HOURS_AGO))

    assert decision_for(pr(*posted)).action == ACTION_NONE
    assert decision_for(pr(request(SIX_HOURS_AGO), job_request(1)), max_requests=1).action == ACTION_BLOCKER_RESOURCE
    assert decision_for(pr(*posted[:3], job_request(3)), max_requests=4).action == ACTION_REQUEST_REVIEW


# --- AC10: unknown reply ----------------------------------------------------------


def test_unknown_reply_posts_an_external_blocker_and_no_move():
    decision = decision_for(pr(request(), codex_comment(CODEX_TASK_REPLY_BODY, url=UNKNOWN_URL)))

    assert decision.action == ACTION_BLOCKER_EXTERNAL
    assert decision.codex_result == "unknown"
    assert decision.codex_url == UNKNOWN_URL
    assert UNKNOWN_URL in decision.reason
    assert decision.summary_next is None


def test_clean_comment_the_gate_rejects_is_an_external_blocker():
    edited = codex_comment(editor="someone-else", editor_type="User")
    decision = decision_for(pr(request(), edited))

    assert decision.action == ACTION_BLOCKER_EXTERNAL
    assert "edited by another account" in decision.reason


# --- AC11: when the job does not act -----------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "status", "expected"),
    [
        ({}, "In implementation", "Story #290 is `In implementation`, not `In review`."),
        ({}, "In merge", "Story #290 is `In merge`, not `In review`."),
        ({}, "in review", "not `In review`"),
        ({}, None, "not set on the project board"),
        ({"is_draft": True}, "In review", "PR is a draft."),
        ({"state": "CLOSED"}, "In review", "PR is closed, not open."),
        ({"state": "MERGED"}, "In review", "PR is merged, not open."),
        ({"linked_issues": (linked_story("executor:codex"),)}, "In review", "carries `executor:codex`"),
        ({"linked_issues": ()}, "In review", "PR has no linked story."),
        ({"linked_issues": (linked_story(),)}, "In review", "has no `executor:` label"),
        (
            {"linked_issues": (linked_story("executor:claude-code-sonnet", "executor:codex"),)},
            "In review",
            "has 2 `executor:` labels",
        ),
        (
            {"linked_issues": (linked_story("executor:claude-code-sonnet"), linked_story("executor:claude-code-haiku", number=291))},
            "In review",
            "more than one linked story",
        ),
    ],
)
def test_no_action_with_the_reason_reported(overrides, status, expected):
    decision = decision_for(pr(request(), codex_comment(), **overrides), status=status)

    assert decision.action == ACTION_NONE
    assert expected in decision.reason
    assert decision.marker is None


def test_incomplete_pr_read_is_an_error_not_a_decision():
    context = pr(request(), codex_comment(), review_evidence_error="PR timeline pagination could not be followed to the end")

    with pytest.raises(BookkeepingError) as error:
        decision_for(context)

    assert error.value.step == "read PR"
    with pytest.raises(BookkeepingError):
        run_bookkeeping(PR, github=FakeGitHub(context), now=NOW)


def test_unreadable_timestamp_is_an_error():
    with pytest.raises(BookkeepingError):
        decision_for(pr(request("not-a-time")))


# --- AC12: idempotency ---------------------------------------------------------------


def test_second_run_posts_no_second_session_summary_and_makes_no_second_move():
    github = FakeGitHub(pr(request(), codex_comment()))

    first = run_bookkeeping(PR, github=github, now=NOW)
    second = run_bookkeeping(PR, github=github, now=NOW)

    assert (first.decision.action, first.applied) == (ACTION_MOVE_IN_MERGE, APPLIED)
    assert first.comment_urls == (f"https://github.com/owner/repo/pull/{PR}#issuecomment-job-0",)
    assert (second.decision.action, second.applied) == (ACTION_NONE, NOT_NEEDED)
    assert len(github.pr_comments) == 1
    assert github.moves == ["In merge"]
    assert github.pr_comments[0].endswith(f"\n{first.decision.marker}\n")


def test_retry_after_a_failed_move_posts_no_second_session_summary():
    github = FakeGitHub(pr(request(), codex_pr_298_findings_review(HEAD_SHA)))
    github.fail_next_move = True

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)
    retry = run_bookkeeping(PR, github=github, now=NOW)

    assert "move story #290" in error.value.step
    assert (retry.decision.action, retry.applied, retry.comment_urls) == (ACTION_MOVE_IN_IMPLEMENTATION, APPLIED, ())
    assert len(github.pr_comments) == 1
    assert github.moves == ["In implementation"]


def test_second_run_posts_no_second_request():
    github = FakeGitHub(pr(request(SIX_HOURS_AGO)))

    first = run_bookkeeping(PR, github=github, now=NOW)
    second = run_bookkeeping(PR, github=github, now=NOW)

    assert (first.decision.action, first.applied) == (ACTION_REQUEST_REVIEW, APPLIED)
    assert (second.decision.action, second.decision.codex_result) == (ACTION_NONE, "pending")
    assert second.decision.requests_posted == 1
    assert github.pr_comments == [review_request_body(HEAD_SHA, ordinal=1)]
    assert github.moves == [] and github.story_comments == []


@pytest.mark.parametrize(
    ("events", "action", "blocker_type"),
    [
        ((request(), codex_comment(CODEX_TASK_REPLY_BODY, url=UNKNOWN_URL)), ACTION_BLOCKER_EXTERNAL, "external"),
        ((request(SIX_HOURS_AGO), job_request(1), job_request(2), job_request(3)), ACTION_BLOCKER_RESOURCE, "resource"),
    ],
)
def test_second_run_posts_no_second_blocker(events, action, blocker_type):
    github = FakeGitHub(pr(*events))

    first = run_bookkeeping(PR, github=github, now=NOW)
    second = run_bookkeeping(PR, github=github, now=NOW)

    assert (first.decision.action, first.applied) == (action, APPLIED)
    assert (second.decision.action, second.applied, second.comment_urls) == (action, ALREADY_PRESENT, ())
    assert len(github.story_comments) == 1
    assert github.pr_comments == [] and github.moves == [] and github.status == "In review"

    lines = github.story_comments[0].splitlines()
    assert lines[:2] == ["## Blocked Declaration", f"Type: {blocker_type}"]
    assert lines[2].startswith("Declared by: codex-review-bookkeeping")
    assert lines[3] == "Blocks: #290 — Story: scenario"
    assert lines[4].startswith("Unblocked when: ")
    assert lines[5] == "Owner: PO (@LauraMardones)"
    assert lines[6].startswith("State of in-progress work: PR #123 at head ")
    assert lines[7] == "Resume instruction: n/a"
    assert lines[8] == first.decision.marker
    if action == ACTION_BLOCKER_EXTERNAL:
        assert UNKNOWN_URL in github.story_comments[0]


def test_blocker_for_one_head_does_not_suppress_the_blocker_for_the_next_head():
    github = FakeGitHub(pr(request(), codex_comment(CODEX_TASK_REPLY_BODY, url=UNKNOWN_URL)))
    github.story_comments.append(f"old\n{action_marker(ACTION_BLOCKER_EXTERNAL, OLD_SHA, source=UNKNOWN_URL)}\n")

    assert run_bookkeeping(PR, github=github, now=NOW).applied == APPLIED
    assert len(github.story_comments) == 2


def test_dry_run_reports_the_decision_and_mutates_nothing():
    scenarios = (
        pr(request(), codex_comment()),
        pr(request(SIX_HOURS_AGO)),
        pr(request(), codex_comment(CODEX_TASK_REPLY_BODY, url=UNKNOWN_URL)),
    )
    for context in scenarios:
        github = FakeGitHub(context)

        outcome = run_bookkeeping(PR, github=github, now=NOW, dry_run=True)

        assert outcome.applied == DRY_RUN and outcome.comment_urls == ()
        assert outcome.decision.action != ACTION_NONE
        assert github.pr_comments == [] and github.story_comments == [] and github.moves == []


def test_missing_board_option_fails_before_anything_is_posted():
    github = FakeGitHub(pr(request(), codex_comment()), options={"In review": "opt-review", "In implementation": "opt-impl"})

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "look up board option"
    assert "`In merge`" in error.value.message
    assert github.pr_comments == [] and github.moves == []


def test_story_that_left_in_review_during_the_run_is_not_moved():
    github = FakeGitHub(pr(request(), codex_comment()))
    statuses = iter(["In review", "In implementation"])
    fetch_story = github.fetch_story

    def changing(number: int) -> StoryBoard:
        return replace(fetch_story(number), status=next(statuses))

    github.fetch_story = changing

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "revalidate decision"
    assert "Story #290 is `In implementation`" in error.value.message
    assert github.pr_comments == [] and github.moves == []


NEW_HEAD = "1823d34d30e18e32b8cab3519a1be75737fa873e"
MUTATING_SCENARIOS = {
    "move-in-merge": (request(), codex_comment()),
    "move-in-implementation": (request(), codex_pr_298_findings_review(HEAD_SHA)),
    "request-review": (request(SIX_HOURS_AGO),),
    "blocker-external": (request(), codex_comment(CODEX_TASK_REPLY_BODY, url=UNKNOWN_URL)),
    "blocker-resource": (request(SIX_HOURS_AGO), job_request(1), job_request(2), job_request(3)),
}


def _changing_pr(github: FakeGitHub, **changes) -> None:
    """The first read sees the fixture; every later read sees the PR with `changes`."""

    first = github.context
    reads = []

    def fetch_context(number: int) -> PullRequestContext:
        reads.append(number)
        return first if len(reads) == 1 else replace(first, **changes)

    github.fetch_context = fetch_context


@pytest.mark.parametrize("action", sorted(MUTATING_SCENARIOS))
def test_commit_pushed_during_the_run_stops_every_mutation(action):
    github = FakeGitHub(pr(*MUTATING_SCENARIOS[action]))
    assert run_bookkeeping(PR, github=github, now=NOW, dry_run=True).decision.action == action
    _changing_pr(github, head_ref_oid=NEW_HEAD)

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "revalidate decision"
    assert HEAD_SHA in error.value.message and NEW_HEAD in error.value.message
    assert "nothing was posted or moved" in error.value.message
    assert github.pr_comments == [] and github.story_comments == [] and github.moves == []
    assert github.status == "In review"


@pytest.mark.parametrize("changes", [{"is_draft": True}, {"state": "CLOSED"}, {"state": "MERGED"}])
def test_pr_that_stopped_being_open_and_ready_during_the_run_is_not_touched(changes):
    github = FakeGitHub(pr(request(), codex_comment()))
    _changing_pr(github, **changes)

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "revalidate decision"
    assert "now `none`" in error.value.message
    assert github.pr_comments == [] and github.moves == []


def test_required_check_that_fails_during_the_run_stops_the_move_to_in_merge():
    # A rerun flips the check on the same head: no push is involved.
    github = FakeGitHub(pr(request(), codex_comment()))
    _changing_pr(github, status_checks=pr(checks="failure").status_checks)

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "revalidate decision"
    assert "was `move-in-merge`" in error.value.message and "now `move-in-implementation`" in error.value.message
    assert github.pr_comments == [] and github.moves == [] and github.status == "In review"


def test_codex_findings_arriving_during_the_run_stop_the_move_to_in_merge():
    github = FakeGitHub(pr(request(), codex_comment()))
    later = pr(request(), codex_comment(), request(), codex_pr_298_findings_review(HEAD_SHA))
    _changing_pr(github, timeline=later.timeline)

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "revalidate decision"
    assert "was `move-in-merge`" in error.value.message and "now `move-in-implementation`" in error.value.message
    assert github.pr_comments == [] and github.moves == [] and github.status == "In review"


def test_codex_answer_arriving_during_the_run_stops_a_re_request():
    github = FakeGitHub(pr(request(SIX_HOURS_AGO)))
    _changing_pr(github, timeline=pr(request(SIX_HOURS_AGO), codex_comment()).timeline)

    with pytest.raises(BookkeepingError):
        run_bookkeeping(PR, github=github, now=NOW)

    assert github.pr_comments == []


def test_everything_is_read_and_decided_again_immediately_before_the_mutation():
    github = FakeGitHub(pr(request(), codex_comment()))
    calls = []
    for name in ("fetch_context", "fetch_story", "fetch_ci", "fetch_issue_comments", "post_comment", "set_story_status"):
        original = getattr(github, name)
        setattr(github, name, lambda *args, _name=name, _original=original: (calls.append(_name), _original(*args))[1])

    run_bookkeeping(PR, github=github, now=NOW)

    assert calls == [
        "fetch_context",
        "fetch_story",
        "fetch_ci",
        "fetch_issue_comments",
        "fetch_context",
        "fetch_story",
        "fetch_ci",
        "fetch_issue_comments",
        "post_comment",
        "set_story_status",
    ]


def test_ineligible_pr_reads_no_board_state_or_story_comments():
    github = FakeGitHub(pr(request(), codex_comment(), is_draft=True))
    github.fetch_story = github.fetch_ci = github.fetch_issue_comments = None  # must not be called

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert (outcome.decision.action, outcome.decision.reason) == (ACTION_NONE, "PR is a draft.")


# --- AC14: Session Summary -------------------------------------------------------------


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
@pytest.mark.parametrize(
    ("events", "checks", "next_step", "blockers"),
    [
        ((request(), codex_comment()), "success", "merge", "none"),
        ((request(), codex_comment()), "failure", "implementation", "required check failed: pytest"),
        (
            (request(), codex_findings_review(HEAD_SHA)),
            "success",
            "implementation",
            "Codex review findings: https://github.com/owner/repo/pull/123#pullrequestreview-1",
        ),
    ],
)
def test_posted_session_summary_is_the_unmodified_script_output(events, checks, next_step, blockers):
    github = FakeGitHub(pr(*events, checks=checks))
    github.session_summary = run_session_summary

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    decision = outcome.decision
    expected = run_session_summary((
        "--command", "review",
        "--issue", str(STORY),
        "--pr", str(PR),
        "--head", HEAD_SHA,
        "--checks", decision.summary_checks,
        "--blockers", blockers,
        "--next", next_step,
        "--deviation", AUTOMATION_DEVIATION,
    ))  # fmt: skip
    posted = github.pr_comments[0]
    # The script output is posted unchanged; only the hidden idempotency marker follows it.
    assert posted == f"{expected}\n{decision.marker}\n"
    assert expected.splitlines() == [
        "## Session Summary",
        "Command: review",
        f"Issue/PR: #{STORY} / #{PR}",
        f"Head: {HEAD_SHA}",
        f"Checks: {decision.summary_checks}",
        f"Blockers: {blockers}",
        f"Next: {next_step}",
        "Deviation: produced by the codex-review-bookkeeping job without a model session",
    ]


def test_session_summary_failure_is_reported_and_nothing_is_moved(monkeypatch):
    github = FakeGitHub(pr(request(), codex_comment()))

    def broken(arguments):
        raise BookkeepingError("generate Session Summary", "bash not found")

    github.session_summary = broken

    with pytest.raises(BookkeepingError):
        run_bookkeeping(PR, github=github, now=NOW)

    assert github.pr_comments == [] and github.moves == []


# --- Configuration ------------------------------------------------------------------------


def test_config_defaults_and_repository_variable_overrides():
    assert BookkeepingConfig.from_env({}) == BookkeepingConfig(retry_hours=5, max_requests=3)
    # An unset repository variable reaches the job as an empty string.
    assert BookkeepingConfig.from_env({"CODEX_REVIEW_RETRY_HOURS": "", "CODEX_REVIEW_MAX_REQUESTS": " "}) == BookkeepingConfig()
    configured = BookkeepingConfig.from_env({"CODEX_REVIEW_RETRY_HOURS": "1.5", "CODEX_REVIEW_MAX_REQUESTS": "5"})
    assert configured == BookkeepingConfig(retry_hours=1.5, max_requests=5)


@pytest.mark.parametrize(
    "env",
    [
        {"CODEX_REVIEW_RETRY_HOURS": "soon"},
        {"CODEX_REVIEW_RETRY_HOURS": "-1"},
        {"CODEX_REVIEW_RETRY_HOURS": "nan"},
        {"CODEX_REVIEW_RETRY_HOURS": "inf"},
        {"CODEX_REVIEW_MAX_REQUESTS": "2.5"},
        {"CODEX_REVIEW_MAX_REQUESTS": "-1"},
    ],
)
def test_invalid_configuration_is_an_error(env):
    with pytest.raises(BookkeepingError) as error:
        BookkeepingConfig.from_env(env)

    assert error.value.step == "read configuration"


# --- GitHub access --------------------------------------------------------------------------


def board_payload(*nodes, has_next_page: bool = False) -> dict:
    return {
        "data": {
            "repository": {
                "issue": {"title": "Story: scenario", "projectItems": {"pageInfo": {"hasNextPage": has_next_page}, "nodes": list(nodes)}}
            }
        }
    }


def board_item(status: str | None = "In review", *, archived: bool = False) -> dict:
    value = None
    if status is not None:
        options = [{"id": option_id, "name": name} for name, option_id in BOARD_OPTIONS.items()]
        value = {"name": status, "field": {"id": "field-1", "options": options}}
    return {"id": "item-1", "isArchived": archived, "project": {"id": "project-1"}, "fieldValueByName": value}


def test_story_board_state_is_parsed_with_options_by_name():
    story = parse_story_board(board_payload(board_item(archived=True), board_item()), number=STORY)

    assert story == StoryBoard(STORY, "Story: scenario", "In review", "item-1", "project-1", "field-1", BOARD_OPTIONS)
    assert parse_story_board(board_payload(), number=STORY).status is None
    assert parse_story_board(board_payload(board_item(None)), number=STORY).status is None


@pytest.mark.parametrize(
    "payload",
    [
        board_payload(board_item(), has_next_page=True),
        board_payload(board_item(), board_item()),
        {"data": {"repository": {"issue": None}}},
        {"errors": [{"message": "boom"}], **board_payload(board_item())},
    ],
)
def test_partial_or_ambiguous_story_board_state_is_an_error(payload):
    with pytest.raises(BookkeepingError) as error:
        parse_story_board(payload, number=STORY)

    assert error.value.step == "read story board state"


class Completed:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_gateway_posts_the_comment_body_as_json_on_stdin(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("input")))
        return Completed(json.dumps({"html_url": "https://github.com/owner/repo/pull/123#issuecomment-9"}))

    monkeypatch.setattr(bookkeeping.subprocess, "run", fake_run)
    body = review_request_body(HEAD_SHA, ordinal=1)

    url = GhCliGateway("owner/repo").post_comment(PR, body)

    command, stdin = calls[0]
    assert url.endswith("#issuecomment-9")
    assert command == ["gh", "api", "repos/owner/repo/issues/123/comments", "--method", "POST", "--input", "-"]
    # The body starts with `@`, which `gh api -F` would read as a file name; it never reaches argv.
    assert json.loads(stdin) == {"body": body}


def test_gateway_reports_the_failed_step_and_claims_no_comment(monkeypatch):
    monkeypatch.setattr(bookkeeping.subprocess, "run", lambda command, **kwargs: Completed(returncode=1, stderr="HTTP 403: nope"))
    gateway = GhCliGateway("owner/repo")

    with pytest.raises(BookkeepingError) as error:
        gateway.post_comment(PR, "body")

    assert (error.value.step, error.value.message) == ("post comment on #123", "HTTP 403: nope")
    with pytest.raises(BookkeepingError):
        gateway.fetch_story(STORY)
    with pytest.raises(BookkeepingError):
        gateway.fetch_issue_comments(STORY)


def test_gateway_requires_confirmation_of_comment_and_board_update(monkeypatch):
    monkeypatch.setattr(bookkeeping.subprocess, "run", lambda command, **kwargs: Completed("{}"))
    gateway = GhCliGateway("owner/repo")
    story = StoryBoard(STORY, "t", "In review", "item", "project", "field", BOARD_OPTIONS)

    with pytest.raises(BookkeepingError):
        gateway.post_comment(PR, "body")
    with pytest.raises(BookkeepingError):
        gateway.set_story_status(story, "In merge")


def test_gateway_lists_only_open_non_draft_prs_and_reads_story_comments(monkeypatch):
    outputs = iter(
        [
            json.dumps([{"number": 9, "isDraft": False}, {"number": 4, "isDraft": True}, {"number": 2, "isDraft": False}]),
            '{"body":"first"}\n{"body":null}\n',
        ]
    )
    monkeypatch.setattr(bookkeeping.subprocess, "run", lambda command, **kwargs: Completed(next(outputs)))
    gateway = GhCliGateway("owner/repo")

    assert gateway.list_open_ready_prs() == (2, 9)
    assert gateway.fetch_issue_comments(STORY) == ("first", "")
    with pytest.raises(BookkeepingError):
        GhCliGateway("not-a-repo")


# --- Command surface ----------------------------------------------------------------------------


def run_cli(monkeypatch, capsys, github, *arguments, env=None):
    monkeypatch.setattr(cli, "GhCliGateway", lambda repo: github)
    for name in ("CODEX_REVIEW_RETRY_HOURS", "CODEX_REVIEW_MAX_REQUESTS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    exit_code = cli.main(["codex-review-bookkeeping", *arguments, "--repo", "owner/repo"])
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_cli_json_output_follows_the_output_contract(monkeypatch, capsys):
    # The CLI reads the real clock, so the fixture times are far in the past.
    github = FakeGitHub(pr(request("2026-01-01T00:00:00Z"), usage_limit("2026-01-01T00:05:00Z")))

    exit_code, out, _ = run_cli(monkeypatch, capsys, github, str(PR), "--json", env={"CODEX_REVIEW_RETRY_HOURS": "0"})

    assert exit_code == 0
    assert json.loads(out) == {
        "pr": PR,
        "head_sha": HEAD_SHA,
        "story": STORY,
        "story_status": "In review",
        "codex_result": {"result": "usage-limit", "url": "https://x/usage-limit"},
        "action": "request-review",
        "reason": "Codex hit its usage limit and the 0-hour back-off has passed.",
        "requests_posted": 0,
        "max_requests": 3,
        "applied": {"state": "applied", "comment_urls": [f"https://github.com/owner/repo/pull/{PR}#issuecomment-job-0"]},
        "threads_resolved": [],
        "threads_remaining": [],
    }


def test_cli_human_output_is_one_line_per_pr(monkeypatch, capsys):
    github = FakeGitHub(pr(request(), codex_comment()), status="In merge")

    exit_code, out, _ = run_cli(monkeypatch, capsys, github, "--all", "--dry-run")

    assert exit_code == 0
    assert out.splitlines() == [
        "PR #123: result=clean action=none applied=not-needed - Story #290 is `In merge`, not `In review`."
    ]


def test_cli_dry_run_mutates_nothing(monkeypatch, capsys):
    github = FakeGitHub(pr(request(), codex_comment()))

    exit_code, out, _ = run_cli(monkeypatch, capsys, github, str(PR), "--dry-run", "--json")

    assert exit_code == 0
    assert json.loads(out)["applied"] == {"state": "dry-run", "comment_urls": []}
    assert github.pr_comments == [] and github.moves == []


def test_cli_exits_non_zero_and_names_the_failed_step(monkeypatch, capsys):
    github = FakeGitHub(pr(request(), codex_comment()))
    github.fail_next_move = True

    exit_code, out, err = run_cli(monkeypatch, capsys, github, str(PR))

    assert exit_code == 1
    assert out == ""
    assert "PR #123: failed at step `move story #290 to `In merge``" in err


def test_cli_all_continues_after_one_pr_fails(monkeypatch, capsys):
    good = FakeGitHub(pr(request(), codex_comment()))
    broken = pr(request(), review_evidence_error="PR head changed while the PR timeline was being read")
    good.list_open_ready_prs = lambda: (7, PR)
    fetch = good.fetch_context
    good.fetch_context = lambda number: broken if number == 7 else fetch(number)

    exit_code, out, _ = run_cli(monkeypatch, capsys, good, "--all", "--json")

    payload = json.loads(out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["errors"] == [
        {"pr": 7, "step": "read PR", "error": "PR head changed while the PR timeline was being read"}
    ]
    assert [result["action"] for result in payload["results"]] == ["move-in-merge"]
    assert good.moves == ["In merge"]


@pytest.mark.parametrize(
    ("arguments", "env"),
    [
        ((), None),
        ((str(PR), "--all"), None),
        (("abc",), None),
        ((str(PR),), {"CODEX_REVIEW_MAX_REQUESTS": "many"}),
    ],
)
def test_cli_rejects_bad_invocations_before_reading_github(monkeypatch, capsys, arguments, env):
    github = FakeGitHub(pr(request(), codex_comment()))
    github.fetch_context = None  # must not be called

    exit_code, out, _ = run_cli(monkeypatch, capsys, github, *arguments, "--json", env=env)

    payload = json.loads(out)
    assert exit_code == 1
    assert payload["ok"] is False and payload["results"] == []
    assert payload["errors"][0]["step"] in {"read arguments", "read configuration"}


def test_command_is_in_the_catalog():
    command = find_command("codex-review-bookkeeping")

    assert command is not None
    assert (command.status, command.command_type, command.layer) == ("implemented", "action", "core")


# --- Missing token scope (issue #329) -----------------------------------------------------------

GRANTED = "but your token has only been granted the: ['project', 'repo', 'workflow'] scopes."
SCOPE_HELP = "Please modify your token's scopes at: https://github.com/settings/tokens."


def refused(field: str, *scopes: str) -> str:
    """One refused field in GitHub's wording, as in the failed runs on PR #328."""

    return (
        "Your token has not been granted the required scopes to execute this query. "
        f"The '{field}' field requires one of the following scopes: {list(scopes)}, {GRANTED} {SCOPE_HELP}"
    )


# The PR read asks for a team's organisation login, name and slug (`reviewRequests`).
PR_READ_REFUSED = "GraphQL: " + ", ".join(
    [
        refused("login", "read:org"),
        refused("name", "read:org", "read:discussion"),
        refused("slug", "read:org", "read:discussion"),
    ]
)


def gateway_with_refused_pr_read(monkeypatch, stderr: str = PR_READ_REFUSED) -> GhCliGateway:
    def refuse(target, *, repo):
        raise GHCommandError(["gh", "pr", "view", target], 1, stderr)

    monkeypatch.setattr(bookkeeping, "fetch_pr_context", refuse)
    monkeypatch.setattr(
        bookkeeping.subprocess,
        "run",
        lambda command, **kwargs: Completed(json.dumps([{"number": PR, "isDraft": False}])),
    )
    return GhCliGateway("owner/repo")


def assert_names_read_org_and_the_docs(message: str) -> None:
    assert "`read:org`" in message
    assert "read:discussion" not in message  # it would not allow the `login` field
    assert "docs/DISPATCHER-CONFIG.md" in message and "GitHub Token — `PROJECT_TOKEN`" in message
    assert TOKEN_SCOPES_DOC in message


@pytest.mark.parametrize("target", [str(PR), "--all"])
def test_cli_names_the_missing_scope_and_the_docs_section_on_stderr(monkeypatch, capsys, target):
    github = gateway_with_refused_pr_read(monkeypatch)

    exit_code, out, err = run_cli(monkeypatch, capsys, github, target)

    assert exit_code == 1
    assert out == ""
    lines = err.splitlines()
    assert len(lines) == 1 and lines[0].startswith("PR #123: failed at step `read PR`: ")
    assert_names_read_org_and_the_docs(lines[0])


@pytest.mark.parametrize("target", [str(PR), "--all"])
def test_cli_names_the_missing_scope_and_the_docs_section_in_json(monkeypatch, capsys, target):
    github = gateway_with_refused_pr_read(monkeypatch)

    exit_code, out, err = run_cli(monkeypatch, capsys, github, target, "--json")

    payload = json.loads(out)
    assert exit_code == 1 and err == ""
    assert set(payload) == {"ok", "results", "errors"}
    assert payload["ok"] is False and payload["results"] == []
    (entry,) = payload["errors"]
    assert set(entry) == {"pr", "step", "error"}
    assert (entry["pr"], entry["step"]) == (PR, "read PR")
    assert_names_read_org_and_the_docs(entry["error"])


def test_missing_scope_in_a_later_step_is_reported_the_same_way(monkeypatch, capsys):
    stderr = "gh: " + refused("projectItems", "read:project")
    monkeypatch.setattr(bookkeeping, "fetch_pr_context", lambda target, *, repo: pr(request(), codex_comment()))
    monkeypatch.setattr(bookkeeping.subprocess, "run", lambda command, **kwargs: Completed(returncode=1, stderr=stderr))

    exit_code, out, err = run_cli(monkeypatch, capsys, GhCliGateway("owner/repo"), str(PR))

    assert exit_code == 1 and out == ""
    assert err.startswith("PR #123: failed at step `read story board state`: ")
    assert "`read:project`" in err and TOKEN_SCOPES_DOC in err


def test_missing_scope_is_named_for_every_gh_read_and_write(monkeypatch):
    stderr = "gh: " + refused("login", "read:org")
    monkeypatch.setattr(bookkeeping.subprocess, "run", lambda command, **kwargs: Completed(returncode=1, stderr=stderr))

    def refuse(*arguments, **kwargs):
        raise GHCommandError(["gh", "api"], 1, stderr)

    monkeypatch.setattr(bookkeeping, "fetch_required_status_check_context", refuse)
    gateway = GhCliGateway("owner/repo")
    story = StoryBoard(STORY, "t", "In review", "item", "project", "field", BOARD_OPTIONS)
    calls = {
        "read required checks": lambda: gateway.fetch_ci(pr(request(), codex_comment())),
        "read story comments": lambda: gateway.fetch_issue_comments(STORY),
        "post comment on #123": lambda: gateway.post_comment(PR, "body"),
        "move story #290 to `In merge`": lambda: gateway.set_story_status(story, "In merge"),
        "list open pull requests": gateway.list_open_ready_prs,
    }

    for step, call in calls.items():
        with pytest.raises(BookkeepingError) as error:
            call()
        assert error.value.step == step
        assert "`read:org`" in error.value.message and TOKEN_SCOPES_DOC in error.value.message


def test_gh_failure_that_is_not_a_missing_scope_keeps_its_message(monkeypatch, capsys):
    github = gateway_with_refused_pr_read(monkeypatch, "HTTP 502: Bad Gateway")

    exit_code, _, err = run_cli(monkeypatch, capsys, github, str(PR))

    assert exit_code == 1
    assert err == "PR #123: failed at step `read PR`: GitHub CLI failed with exit code 1: HTTP 502: Bad Gateway\n"


def test_missing_scope_message_falls_back_when_the_wording_is_not_recognized():
    assert missing_scope_message("HTTP 403: Resource not accessible by personal access token") is None
    # The signal without a scope list: nothing to name, so the caller keeps today's text.
    assert missing_scope_message("Your token has not been granted the required scopes to execute this query.") is None


def test_missing_scope_message_names_every_listed_scope_when_no_single_one_is_enough():
    message = missing_scope_message(refused("a", "read:org") + ", " + refused("b", "read:discussion"))

    assert message is not None
    assert "`read:org`, `read:discussion`" in message
    assert "`project`, `repo`, `workflow`" in message  # what the token has


# --- Superseded Codex review threads (issue #331) -----------------------------------------------

OLDER_SHA = "ab667c97d9bec69cd3c444fc8f4a1c3cdbffc864"
MIDDLE_SHA = "1823d34d30e18e32b8cab3519a1be75737fa873e"
THREAD_BASE = f"https://github.com/owner/repo/pull/{PR}#discussion_r"


def thread(
    number: int,
    *,
    codex: bool = True,
    commit: str | None = OLD_SHA,
    resolved: bool = False,
    outdated: bool = False,
    opener: str | None = None,
) -> ReviewThreadFact:
    return ReviewThreadFact(
        id=f"PRRT_{number}",
        url=f"{THREAD_BASE}{number}",
        is_resolved=resolved,
        is_outdated=outdated,
        opener=opener or (CODEX_BOT if codex else "LauraMardones"),
        opener_is_codex=codex,
        review_commit=commit,
    )


def with_threads(*threads: ReviewThreadFact, events=None) -> FakeGitHub:
    github = FakeGitHub(pr(*(events or (request(), codex_comment()))))
    github.threads = list(threads)
    return github


def blocker_of(github: FakeGitHub) -> str:
    (body,) = github.story_comments
    return body


def test_superseded_codex_threads_are_resolved_then_summarized_then_the_story_moves():
    github = with_threads(thread(1, commit=OLDER_SHA), thread(2, commit=MIDDLE_SHA), thread(3, commit=OLD_SHA))

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert (outcome.decision.action, outcome.applied) == (ACTION_MOVE_IN_MERGE, APPLIED)
    assert github.order == ["resolve:PRRT_1", "resolve:PRRT_2", "resolve:PRRT_3", "summary", "move"]
    (summary,) = github.pr_comments
    for number, commit in ((1, OLDER_SHA), (2, MIDDLE_SHA), (3, OLD_SHA)):
        assert f"{THREAD_BASE}{number} (opened in the Codex review of {commit}, superseded by the clean Codex result {CLEAN_URL})" in summary
    assert [thread.id for thread in outcome.decision.threads_resolved] == ["PRRT_1", "PRRT_2", "PRRT_3"]
    assert outcome.decision.threads_remaining == ()


def test_clean_result_without_threads_says_so_and_otherwise_behaves_as_before():
    github = with_threads()

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert (outcome.decision.action, outcome.applied) == (ACTION_MOVE_IN_MERGE, APPLIED)
    assert github.order == ["summary", "move"] and github.resolve_calls == []
    assert "no Codex threads resolved" in github.pr_comments[0]


def test_threads_already_resolved_are_left_alone_and_resolved_ones_are_not_called_again():
    github = with_threads(thread(1, resolved=True), thread(2, codex=False, resolved=True))

    run_bookkeeping(PR, github=github, now=NOW)

    assert github.resolve_calls == []


def test_human_threads_are_never_resolved_and_block_the_move():
    github = with_threads(
        thread(1, commit=OLDER_SHA),
        thread(2, codex=False, commit=OLD_SHA),
        thread(3, codex=False, commit=OLD_SHA, opener=CODEX_BOT),  # a `User` account whose login looks like the bot's
    )

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert (outcome.decision.action, outcome.applied) == (ACTION_BLOCKER_EXTERNAL, APPLIED)
    assert github.resolve_calls == [] and github.moves == [] and github.pr_comments == []
    assert [(t.id, t.reason) for t in outcome.decision.threads_remaining] == [
        ("PRRT_2", "not-codex"),
        ("PRRT_3", "not-codex"),
    ]


def test_human_thread_is_not_resolved_when_outdated_or_when_codex_replied_in_it():
    # An outdated human thread does not block the gate and stays untouched; the superseded Codex thread is resolved.
    github = with_threads(thread(1, codex=False, outdated=True), thread(2, commit=OLDER_SHA))

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert outcome.decision.action == ACTION_MOVE_IN_MERGE
    assert github.resolve_calls == ["PRRT_2"]
    assert [(t.id, t.reason) for t in outcome.decision.threads_remaining] == [("PRRT_1", "not-codex")]


def test_codex_thread_on_the_current_head_is_not_resolved_also_after_a_clean_result():
    github = with_threads(thread(1, commit=HEAD_SHA), events=(request(), codex_pr_298_findings_review(HEAD_SHA), codex_comment()))

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert (outcome.decision.action, outcome.applied) == (ACTION_BLOCKER_EXTERNAL, APPLIED)
    assert github.resolve_calls == [] and github.moves == []
    assert [t.reason for t in outcome.decision.threads_remaining] == ["current-head"]
    assert "current-head" in outcome.to_line()


def test_codex_thread_with_an_unknown_review_commit_fails_closed():
    github = with_threads(thread(1, commit=None))

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert outcome.decision.action == ACTION_BLOCKER_EXTERNAL
    assert github.resolve_calls == []
    assert [t.reason for t in outcome.decision.threads_remaining] == ["unknown-commit"]


def test_blocker_resolves_nothing_names_only_the_remaining_threads_and_follows_the_format():
    github = with_threads(
        thread(1, commit=OLDER_SHA),
        thread(2, codex=False),
        thread(3, codex=False, outdated=True),
        thread(4, commit=HEAD_SHA),
    )

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert github.resolve_calls == [] and github.moves == [] and github.status == "In review"
    lines = blocker_of(github).splitlines()
    assert lines[:2] == ["## Blocked Declaration", "Type: external"]
    assert lines[4] == f"Unblocked when: the listed threads are resolved on GitHub, or a new head is pushed to PR #{PR}"
    assert lines[5] == "Owner: PO (@LauraMardones)"
    assert lines[8] == action_marker(ACTION_BLOCKER_EXTERNAL, HEAD_SHA, source="review-threads")
    assert f"{THREAD_BASE}2 - opened by an account other than the Codex bot" in lines[6]
    assert f"{THREAD_BASE}4 - opened by Codex in a review of the current head" in lines[6]
    assert f"{THREAD_BASE}1" not in lines[6] and f"{THREAD_BASE}3" not in lines[6]
    assert "threads_remaining=3" in outcome.to_line()  # the outdated human thread is reported, not blocking


def test_recovery_after_the_blocker_moves_the_story_without_a_second_blocker():
    github = with_threads(thread(1, codex=False), thread(2, commit=OLDER_SHA))

    first = run_bookkeeping(PR, github=github, now=NOW)
    again = run_bookkeeping(PR, github=github, now=NOW)
    github.threads = [replace(t, is_resolved=True) if t.id == "PRRT_1" else t for t in github.threads]
    recovered = run_bookkeeping(PR, github=github, now=NOW)

    assert (first.applied, again.applied) == (APPLIED, ALREADY_PRESENT)
    assert (recovered.decision.action, recovered.applied) == (ACTION_MOVE_IN_MERGE, APPLIED)
    assert github.resolve_calls == ["PRRT_2"] and github.moves == ["In merge"]
    assert len(github.story_comments) == 1


def test_a_run_after_the_move_changes_nothing():
    github = with_threads(thread(1, commit=OLDER_SHA))

    run_bookkeeping(PR, github=github, now=NOW)
    reads = github.thread_reads
    second = run_bookkeeping(PR, github=github, now=NOW)

    assert (second.decision.action, second.applied) == (ACTION_NONE, NOT_NEEDED)
    assert github.resolve_calls == ["PRRT_1"] and len(github.pr_comments) == 1 and github.moves == ["In merge"]
    assert github.thread_reads == reads


def test_retry_after_a_failed_resolve_finishes_the_rest_with_one_summary_listing_every_thread():
    github = with_threads(thread(1, commit=OLDER_SHA), thread(2, commit=MIDDLE_SHA), thread(3, commit=OLD_SHA))
    github.fail_resolve_of = "PRRT_2"

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)
    assert error.value.step == "resolve review thread"
    assert github.pr_comments == [] and github.moves == [] and github.resolve_calls == ["PRRT_1"]

    retry = run_bookkeeping(PR, github=github, now=NOW)

    assert retry.applied == APPLIED
    assert github.resolve_calls == ["PRRT_1", "PRRT_2", "PRRT_3"]
    (summary,) = github.pr_comments
    assert all(f"{THREAD_BASE}{number}" in summary for number in (1, 2, 3))
    assert github.order == ["resolve:PRRT_1", "resolve:PRRT_2", "resolve:PRRT_3", "summary", "move"]


def test_retry_after_a_failed_move_does_not_resolve_or_summarize_again():
    github = with_threads(thread(1, commit=OLDER_SHA))
    github.fail_next_move = True

    with pytest.raises(BookkeepingError):
        run_bookkeeping(PR, github=github, now=NOW)
    run_bookkeeping(PR, github=github, now=NOW)

    assert github.resolve_calls == ["PRRT_1"] and len(github.pr_comments) == 1 and github.moves == ["In merge"]


@pytest.mark.parametrize("change", ["opened", "resolved", "unresolved"])
def test_a_thread_that_changes_between_the_two_reads_stops_the_run_with_no_mutation(change):
    github = with_threads(thread(1, commit=OLDER_SHA), thread(2, commit=OLD_SHA, resolved=change == "unresolved"))
    read = github.fetch_review_threads
    calls = []

    def changing(context):
        facts = read(context)
        calls.append(1)
        if len(calls) == 1:
            return facts
        if change == "opened":
            return (*facts, thread(3, commit=OLDER_SHA))
        if change == "resolved":
            return tuple(replace(t, is_resolved=True) if t.id == "PRRT_1" else t for t in facts)
        return tuple(replace(t, is_resolved=False) for t in facts)

    github.fetch_review_threads = changing

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "revalidate decision" and "changed during the run" in error.value.message
    assert github.resolve_calls == [] and github.pr_comments == [] and github.moves == []


def test_failed_thread_read_is_a_named_failure_with_no_mutation():
    github = with_threads(thread(1, commit=OLDER_SHA))
    github.thread_read_error = BookkeepingError("read review threads", "GitHub review-thread payload could not be parsed")

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "read review threads"
    assert github.resolve_calls == [] and github.pr_comments == [] and github.moves == []


@pytest.mark.parametrize(
    "events",
    [
        (request(), codex_pr_298_findings_review(HEAD_SHA)),
        (request(SIX_HOURS_AGO), usage_limit(TWO_HOURS_AGO)),
        (request(),),
        (request(), codex_comment(CODEX_TASK_REPLY_BODY, url=UNKNOWN_URL)),
    ],
)
def test_other_branches_neither_read_nor_resolve_threads(events):
    github = with_threads(thread(1, commit=OLDER_SHA), events=events)

    run_bookkeeping(PR, github=github, now=NOW, config=BookkeepingConfig(retry_hours=0))

    assert github.thread_reads == 0 and github.resolve_calls == []


def test_clean_result_with_a_failing_or_running_check_or_a_failed_gate_resolves_nothing():
    for checks in ("failure", "pending"):
        github = FakeGitHub(pr(request(), codex_comment(), checks=checks))
        github.threads = [thread(1, commit=OLDER_SHA)]
        run_bookkeeping(PR, github=github, now=NOW)
        assert github.thread_reads == 0 and github.resolve_calls == [], checks

    gate = FakeGitHub(pr(request(), codex_comment(), review_decision="CHANGES_REQUESTED"))
    gate.threads = [thread(1, commit=OLDER_SHA)]
    run_bookkeeping(PR, github=gate, now=NOW)
    assert gate.thread_reads == 0 and gate.resolve_calls == []


def test_dry_run_reports_the_threads_it_would_resolve_and_mutates_nothing():
    github = with_threads(thread(1, commit=OLDER_SHA))

    outcome = run_bookkeeping(PR, github=github, now=NOW, dry_run=True)

    assert outcome.applied == DRY_RUN
    assert [t.id for t in outcome.decision.threads_resolved] == ["PRRT_1"]
    assert github.resolve_calls == [] and github.pr_comments == [] and github.moves == []


def test_json_and_human_output_report_resolved_and_remaining_threads():
    github = with_threads(thread(1, commit=OLDER_SHA))

    outcome = run_bookkeeping(PR, github=github, now=NOW)
    payload = outcome.to_dict()

    assert payload["threads_resolved"] == [
        {"id": "PRRT_1", "url": f"{THREAD_BASE}1", "review_commit": OLDER_SHA, "superseded_by": CLEAN_URL}
    ]
    assert payload["threads_remaining"] == []
    assert outcome.to_line().startswith(f"PR #{PR}: result=clean action=move-in-merge applied=applied threads_resolved=1 - ")

    blocked = run_bookkeeping(PR, github=with_threads(thread(2, codex=False, opener="LauraMardones")), now=NOW)
    assert blocked.to_dict()["threads_remaining"] == [
        {"id": "PRRT_2", "url": f"{THREAD_BASE}2", "opener": "LauraMardones", "review_commit": OLD_SHA, "reason": "not-codex"}
    ]
    assert " threads_remaining=1 - " in blocked.to_line()


def test_classify_threads_ignores_replies_and_compares_commits_case_insensitively():
    resolvable, remaining, already = classify_threads(
        (
            thread(1, commit=OLDER_SHA.upper()),
            thread(2, commit=HEAD_SHA.upper()),
            thread(3, commit=OLDER_SHA, resolved=True),
            thread(4, codex=False, commit=OLDER_SHA, resolved=True),
        ),
        head_sha=HEAD_SHA,
        clean_url=CLEAN_URL,
    )

    assert [t.id for t in resolvable] == ["PRRT_1"]
    assert [(t.id, t.reason) for t in remaining] == [("PRRT_2", "current-head")]
    assert [t.id for t in already] == ["PRRT_3"]


# --- Thread read and resolve through `gh` ----------------------------------------------------------


def raw_thread(thread_id: str, *, login: str, kind: str, commit: str | None, resolved=False, outdated=False, replies=()) -> dict:
    def comment(number: int, who: str, who_type: str, review_commit: str | None) -> dict:
        return {
            "id": f"c{number}",
            "url": f"{THREAD_BASE}{thread_id}-{number}",
            "body": "text",
            "author": {"login": who, "__typename": who_type},
            "outdated": outdated,
            "pullRequestReview": {"state": "COMMENTED", "author": {"login": who}, "commit": {"oid": review_commit}}
            if review_commit
            else None,
        }

    nodes = [comment(0, login, kind, commit), *(comment(i + 1, *reply, commit) for i, reply in enumerate(replies))]
    return {"id": thread_id, "isResolved": resolved, "isOutdated": outdated, "comments": {"totalCount": len(nodes), "nodes": nodes}}


def test_gateway_decides_ownership_by_the_opener_account_and_type_not_by_replies(monkeypatch):
    raw = (
        raw_thread("T1", login=CODEX_BOT, kind="Bot", commit=OLDER_SHA),
        raw_thread("T2", login="chatgpt-codex-connector", kind="User", commit=OLDER_SHA),
        raw_thread("T3", login="LauraMardones", kind="User", commit=OLDER_SHA, replies=((CODEX_BOT, "Bot"),)),
        raw_thread("T4", login=CODEX_BOT, kind="Bot", commit=None),
        raw_thread("T5", login=CODEX_BOT, kind="Bot", commit=OLDER_SHA, replies=(("LauraMardones", "User"),)),
    )
    monkeypatch.setattr(bookkeeping, "fetch_review_threads_for_context", lambda context, *, repo: raw)

    facts = GhCliGateway("owner/repo").fetch_review_threads(pr(request(), codex_comment()))

    assert [(f.id, f.opener_is_codex, f.review_commit) for f in facts] == [
        ("T1", True, OLDER_SHA),
        ("T2", False, OLDER_SHA),
        ("T3", False, OLDER_SHA),
        ("T4", True, None),
        ("T5", True, OLDER_SHA),
    ]
    assert facts[0].url == f"{THREAD_BASE}T1-0"


def test_gateway_thread_read_failures_name_the_step_and_a_missing_scope(monkeypatch):
    def refuse(context, *, repo):
        raise GHCommandError(["gh", "api", "graphql"], 1, "gh: " + refused("reviewThreads", "repo"))

    monkeypatch.setattr(bookkeeping, "fetch_review_threads_for_context", refuse)

    with pytest.raises(BookkeepingError) as error:
        GhCliGateway("owner/repo").fetch_review_threads(pr(request(), codex_comment()))

    assert error.value.step == "read review threads"
    assert "`repo`" in error.value.message and TOKEN_SCOPES_DOC in error.value.message

    def partial(context, *, repo):
        raise GHCommandError(["gh", "api", "graphql"], 0, "page without cursor", error="gh-parse-failed")

    monkeypatch.setattr(bookkeeping, "fetch_review_threads_for_context", partial)
    with pytest.raises(BookkeepingError) as error:
        GhCliGateway("owner/repo").fetch_review_threads(pr(request(), codex_comment()))
    assert error.value.step == "read review threads"


def test_gateway_resolves_a_thread_through_the_mutation_and_requires_confirmation(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return Completed(json.dumps({"data": {"resolveReviewThread": {"thread": {"id": "T1", "isResolved": True}}}}))

    monkeypatch.setattr(bookkeeping.subprocess, "run", fake_run)
    GhCliGateway("owner/repo").resolve_review_thread("T1")
    assert "resolveReviewThread" in " ".join(commands[0]) and "threadId=T1" in commands[0]

    for stdout in (
        json.dumps({"data": {"resolveReviewThread": {"thread": {"id": "T1", "isResolved": False}}}}),
        json.dumps({"data": None, "errors": [{"message": "no"}]}),
        "not json",
    ):
        monkeypatch.setattr(bookkeeping.subprocess, "run", lambda command, stdout=stdout, **kwargs: Completed(stdout))
        with pytest.raises(BookkeepingError) as error:
            GhCliGateway("owner/repo").resolve_review_thread("T1")
        assert error.value.step == "resolve review thread"


def test_gateway_resolve_failure_names_a_missing_scope(monkeypatch):
    stderr = "gh: " + refused("resolveReviewThread", "repo")
    monkeypatch.setattr(bookkeeping.subprocess, "run", lambda command, **kwargs: Completed(returncode=1, stderr=stderr))

    with pytest.raises(BookkeepingError) as error:
        GhCliGateway("owner/repo").resolve_review_thread("T1")

    assert error.value.step == "resolve review thread"
    assert "`repo`" in error.value.message and TOKEN_SCOPES_DOC in error.value.message


# --- Exit code 3 and the errors file (issue #330) ------------------------------------------------


def _changing_story(github):
    statuses = iter(["In review", "In implementation"])
    fetch_story = github.fetch_story
    github.fetch_story = lambda number: replace(fetch_story(number), status=next(statuses))


def test_cli_exits_3_when_the_only_failure_is_the_pr_changing_during_the_run(monkeypatch, capsys, tmp_path):
    github = FakeGitHub(pr(request(), codex_comment()))
    _changing_story(github)
    errors_file = tmp_path / "errors.json"

    exit_code, out, err = run_cli(monkeypatch, capsys, github, str(PR), "--json", "--errors-file", str(errors_file))

    assert exit_code == 3
    assert json.loads(out)["ok"] is False
    assert "failed at step `revalidate decision`" not in err  # --json keeps stderr empty
    saved = json.loads(errors_file.read_text())
    assert saved["dry_run"] is False
    assert [error["step"] for error in saved["errors"]] == ["revalidate decision"]
    assert github.pr_comments == [] and github.moves == []


def test_cli_exits_1_when_the_pr_change_comes_with_another_failure(monkeypatch, capsys):
    github = FakeGitHub(pr(request(), codex_comment()))
    _changing_story(github)
    broken = pr(request(), review_evidence_error="PR head changed while the PR timeline was being read")
    github.list_open_ready_prs = lambda: (7, PR)
    fetch = github.fetch_context
    github.fetch_context = lambda number: broken if number == 7 else fetch(number)

    exit_code, out, _ = run_cli(monkeypatch, capsys, github, "--all", "--json")

    assert exit_code == 1
    assert [error["step"] for error in json.loads(out)["errors"]] == ["read PR", "revalidate decision"]


@pytest.mark.parametrize("arguments", [(), ("12x",), ("--all", "5")])
def test_cli_exits_1_for_a_failure_before_any_pr_is_read(monkeypatch, capsys, tmp_path, arguments):
    github = FakeGitHub(pr(request(), codex_comment()))
    errors_file = tmp_path / "errors.json"

    exit_code, _, _ = run_cli(monkeypatch, capsys, github, *arguments, "--errors-file", str(errors_file))

    assert exit_code == 1
    assert json.loads(errors_file.read_text())["errors"][0]["pr"] is None


# --- Hold on an open decision blocker (issue #357) ----------------------------------------------

PO = "LauraMardones"
DECLARATION = "## Blocked Declaration\nType: decision\nQuestion: waive the listed findings for this PR?\n"
BLOCKER_URL = "https://github.com/owner/repo/issues/290#issuecomment-1"


def story_comment(body: str, created_at: str, author: str = "claude-bot", url: str | None = None) -> dict:
    return {"body": body, "author": author, "created_at": created_at, "url": url or BLOCKER_URL}


def held(github: FakeGitHub, *extra: dict) -> FakeGitHub:
    github.story_thread = [story_comment(DECLARATION, "2026-10-11T09:00:00Z"), *extra]
    return github


def assert_untouched(github: FakeGitHub) -> None:
    assert github.pr_comments == [] and github.story_comments == [] and github.moves == []
    assert github.resolve_calls == [] and github.status == "In review"


def test_open_decision_blocker_holds_a_head_with_no_review_request():
    github = held(FakeGitHub(pr()))

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert (outcome.decision.action, outcome.applied) == (ACTION_NONE, NOT_NEEDED)
    assert BLOCKER_URL in outcome.decision.reason and "decision blocker" in outcome.decision.reason
    assert_untouched(github)


def test_open_decision_blocker_keeps_an_old_head_thread_unresolved_after_a_clean_result():
    github = held(with_threads(thread(1, commit=OLDER_SHA)))

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert outcome.decision.action == ACTION_NONE
    assert_untouched(github)
    assert github.thread_reads == 0 and not github.threads[0].is_resolved


@pytest.mark.parametrize("events", [(request(), codex_comment()), ()])
def test_open_decision_blocker_dry_run_reports_none(events):
    github = held(FakeGitHub(pr(*events)))

    outcome = run_bookkeeping(PR, github=github, now=NOW, dry_run=True)

    assert (outcome.decision.action, outcome.applied) == (ACTION_NONE, NOT_NEEDED)
    assert_untouched(github)


def test_decision_blocker_holds_a_codex_findings_result_too():
    github = held(FakeGitHub(pr(request(), codex_findings_review(HEAD_SHA))))

    assert run_bookkeeping(PR, github=github, now=NOW).decision.action == ACTION_NONE
    assert_untouched(github)


def test_po_unblock_line_after_the_blocker_restores_todays_decision():
    github = held(FakeGitHub(pr()), story_comment("Waived F1 (#400).\n  /unblock  \n", "2026-10-11T10:00:00Z", PO))

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert (outcome.decision.action, outcome.applied) == (ACTION_REQUEST_REVIEW, APPLIED)
    assert len(github.pr_comments) == 1


def test_po_unblock_line_restores_the_move_and_the_thread_resolution():
    github = with_threads(thread(1, commit=OLDER_SHA))
    held(github, story_comment("/unblock", "2026-10-11T10:00:00Z", PO))

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert outcome.decision.action == ACTION_MOVE_IN_MERGE
    assert github.resolve_calls == ["PRRT_1"] and github.moves == ["In merge"]


@pytest.mark.parametrize(
    "comment",
    [
        story_comment("please do not /unblock yet", "2026-10-11T10:00:00Z", PO),  # inside prose
        story_comment("/unblock", "2026-10-11T10:00:00Z", "someone-else"),  # another account
        story_comment("/unblock", "2026-10-11T08:00:00Z", PO),  # before the declaration
        story_comment("/unblock", "2026-10-11T09:00:00Z", PO),  # not strictly after it
        story_comment("/unblock now", "2026-10-11T10:00:00Z", PO),  # not a line of its own
    ],
)
def test_only_a_po_unblock_line_after_the_latest_blocker_lifts_the_hold(comment):
    github = held(FakeGitHub(pr()), comment)

    assert run_bookkeeping(PR, github=github, now=NOW).decision.action == ACTION_NONE
    assert_untouched(github)


def test_unblock_of_an_earlier_blocker_does_not_lift_a_newer_one():
    github = FakeGitHub(pr())
    github.story_thread = [
        story_comment(DECLARATION, "2026-10-11T09:00:00Z"),
        story_comment("/unblock", "2026-10-11T10:00:00Z", PO),
        story_comment(DECLARATION, "2026-10-11T11:00:00Z", url="https://github.com/owner/repo/issues/290#issuecomment-2"),
    ]

    outcome = run_bookkeeping(PR, github=github, now=NOW)

    assert outcome.decision.action == ACTION_NONE and "issuecomment-2" in outcome.decision.reason


def test_resource_and_external_blockers_do_not_hold():
    github = FakeGitHub(pr())
    github.story_thread = [story_comment("## Blocked Declaration\nType: resource\n", "2026-10-11T09:00:00Z")]

    assert run_bookkeeping(PR, github=github, now=NOW).decision.action == ACTION_REQUEST_REVIEW


def test_decision_blocker_appearing_between_the_two_reads_changes_nothing():
    github = FakeGitHub(pr())
    reads = iter([None, BLOCKER_URL])
    github.fetch_open_decision_blocker = lambda number: next(reads)

    with pytest.raises(BookkeepingError) as error:
        run_bookkeeping(PR, github=github, now=NOW)

    assert error.value.step == "revalidate decision"
    assert_untouched(github)


def test_decision_blocker_on_a_story_that_is_not_in_review_is_not_read():
    github = held(FakeGitHub(pr(), status="In implementation"))
    github.fetch_open_decision_blocker = None  # must not be called

    assert run_bookkeeping(PR, github=github, now=NOW).decision.action == ACTION_NONE


def test_gateway_reads_the_open_decision_blocker_with_author_time_and_url(monkeypatch):
    lines = [
        {"body": DECLARATION, "author": "bot", "created_at": "2026-10-11T09:00:00Z", "url": BLOCKER_URL},
        {"body": "/unblock", "author": PO, "created_at": "2026-10-11T10:00:00Z", "url": "u2"},
    ]
    outputs = iter(["\n".join(json.dumps(x) for x in lines[:1]) + "\n", "\n".join(json.dumps(x) for x in lines) + "\n"])
    monkeypatch.setattr(bookkeeping.subprocess, "run", lambda command, **kwargs: Completed(next(outputs)))
    gateway = GhCliGateway("owner/repo")

    assert gateway.fetch_open_decision_blocker(STORY) == BLOCKER_URL
    assert gateway.fetch_open_decision_blocker(STORY) is None
