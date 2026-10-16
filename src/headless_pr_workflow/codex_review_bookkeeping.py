"""Deterministic follow-up to a Codex review of a Claude-implemented PR (ADR-006).

The job reads the current state of one PR and decides what to do next: move the
story on the board and post the Session Summary, post a bounded review
re-request, or post a blocker. No model is involved. The decision is a pure
function of GitHub facts and the current time; every mutation is idempotent
through a hidden marker in the comment it posts, so all state lives on GitHub.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .ci_summary import CiSummary, summarize_ci
from .github import (
    GHCommandError,
    LinkedIssue,
    PullRequestContext,
    TimelineEvent,
    fetch_pr_context,
    fetch_required_status_check_context,
)
from .github.pr_context import _gh_env
from .github.review_threads import fetch_review_threads_for_context, summarize_review_threads
from .review_policy import (
    CLAUDE_EXECUTOR_LABEL_PREFIX,
    CODEX_REVIEW_TRIGGER_PHRASE,
    EXECUTOR_LABEL_PREFIX,
    classify_codex_event,
    codex_reviewed_commit,
    evaluate_codex_review,
    is_codex_actor,
    is_codex_review_request,
    summarize_review_policy,
)


COMMAND_NAME = "codex-review-bookkeeping"
# The only failure that exits 3: two readings of the PR disagreed (issue #330).
CHANGED_DURING_RUN_STEP = "revalidate decision"
STATUS_IN_REVIEW = "In review"
STATUS_IN_MERGE = "In merge"
STATUS_IN_IMPLEMENTATION = "In implementation"

ACTION_MOVE_IN_MERGE = "move-in-merge"
ACTION_MOVE_IN_IMPLEMENTATION = "move-in-implementation"
ACTION_REQUEST_REVIEW = "request-review"
ACTION_BLOCKER_RESOURCE = "blocker-resource"
ACTION_BLOCKER_EXTERNAL = "blocker-external"
ACTION_NONE = "none"
MOVE_TARGETS = {
    ACTION_MOVE_IN_MERGE: STATUS_IN_MERGE,
    ACTION_MOVE_IN_IMPLEMENTATION: STATUS_IN_IMPLEMENTATION,
}

APPLIED = "applied"
ALREADY_PRESENT = "already-present"
DRY_RUN = "dry-run"
NOT_NEEDED = "not-needed"

RETRY_HOURS_ENV = "CODEX_REVIEW_RETRY_HOURS"
MAX_REQUESTS_ENV = "CODEX_REVIEW_MAX_REQUESTS"
DEFAULT_RETRY_HOURS = 5.0
DEFAULT_MAX_REQUESTS = 3

MARKER_PREFIX = f"<!-- hpw:{COMMAND_NAME}"
AUTOMATION_DEVIATION = f"produced by the {COMMAND_NAME} job without a model session"
DECLARED_BY = f"{COMMAND_NAME} (GitHub Actions)"
BLOCKER_OWNER = "PO (@LauraMardones)"
# The PO account whose standalone `/unblock` line lifts a decision blocker
# (docs/PROJECT-STATUS.md, Decision blockers; DECISION_BLOCKER_PO_LOGIN in the dispatcher).
DECISION_BLOCKER_PO_LOGIN = "LauraMardones"
SESSION_SUMMARY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "session-summary.sh"

# The one place that lists the scopes of the token the job runs with.
TOKEN_SCOPES_DOC = 'docs/DISPATCHER-CONFIG.md → "GitHub Token — `PROJECT_TOKEN`"'
# GitHub's wording for a token without a scope a query needs, one sentence per refused field.
MISSING_SCOPE_SIGNAL = "has not been granted the required scopes"
_REQUIRED_SCOPES = re.compile(r"requires one of the following scopes: \[([^\]]*)\]")
_GRANTED_SCOPES = re.compile(r"has only been granted the: \[([^\]]*)\]")

STORY_BOARD_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      title
      projectItems(first: 20) {
        pageInfo { hasNextPage }
        nodes {
          id
          isArchived
          project { id }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue {
              name
              field { ... on ProjectV2SingleSelectField { id options { id name } } }
            }
          }
        }
      }
    }
  }
}
"""
SET_STATUS_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: { singleSelectOptionId: $optionId }
  }) { projectV2Item { id } }
}
"""
RESOLVE_THREAD_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) { thread { id isResolved } }
}
"""

# Why the job did not resolve an unresolved review thread.
THREAD_NOT_CODEX = "not-codex"
THREAD_CURRENT_HEAD = "current-head"
THREAD_UNKNOWN_COMMIT = "unknown-commit"
THREAD_REASON_TEXT = {
    THREAD_NOT_CODEX: "opened by an account other than the Codex bot",
    THREAD_CURRENT_HEAD: "opened by Codex in a review of the current head",
    THREAD_UNKNOWN_COMMIT: "the commit of the Codex review that opened it could not be read",
}


class BookkeepingError(RuntimeError):
    """A read was incomplete, a board option is missing or a mutation failed."""

    def __init__(self, step: str, message: str) -> None:
        self.step = step
        self.message = message
        super().__init__(f"{step}: {message}")


def missing_scope_message(detail: str) -> str | None:
    """Name the token scopes GitHub reported as missing, or None when `detail` is another failure.

    Each refused field lists the scopes that would allow it. A scope that would
    allow every refused field is the one to add; without such a scope, all the
    listed scopes are named.
    """

    if MISSING_SCOPE_SIGNAL not in detail:
        return None
    alternatives = [_scope_names(match) for match in _REQUIRED_SCOPES.findall(detail)]
    alternatives = [names for names in alternatives if names]
    if not alternatives:
        return None
    listed = list(dict.fromkeys(name for names in alternatives for name in names))
    missing = [name for name in listed if all(name in names for names in alternatives)] or listed
    granted = _GRANTED_SCOPES.search(detail)
    has = f" (the token has: {_quoted(_scope_names(granted.group(1)))})" if granted else ""
    return (
        f"the GitHub token lacks a required scope: {_quoted(missing)}{has}. "
        f"The scopes the token needs are listed in {TOKEN_SCOPES_DOC}."
    )


def _scope_names(listing: str) -> list[str]:
    return [name for name in (part.strip().strip("'\"") for part in listing.split(",")) if name]


def _quoted(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names) or "none"


def _gh_failure(step: str, detail: str, fallback: str) -> "BookkeepingError":
    """The error for a failed `gh` call: a missing token scope is named, anything else keeps its text."""

    return BookkeepingError(step, missing_scope_message(detail) or fallback)


@dataclass(frozen=True)
class BookkeepingConfig:
    retry_hours: float = DEFAULT_RETRY_HOURS
    max_requests: int = DEFAULT_MAX_REQUESTS

    @property
    def back_off(self) -> timedelta:
        return timedelta(hours=self.retry_hours)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BookkeepingConfig":
        """Read the bounds from the environment; an unset or empty variable keeps its default."""

        env = os.environ if env is None else env
        retry_text = (env.get(RETRY_HOURS_ENV) or "").strip()
        max_text = (env.get(MAX_REQUESTS_ENV) or "").strip()
        try:
            retry_hours = float(retry_text) if retry_text else DEFAULT_RETRY_HOURS
        except ValueError:
            retry_hours = -1.0
        # `not >= 0` also rejects NaN.
        if not retry_hours >= 0 or retry_hours == float("inf"):
            raise BookkeepingError("read configuration", f"{RETRY_HOURS_ENV} must be a number of hours, 0 or more")
        try:
            max_requests = int(max_text) if max_text else DEFAULT_MAX_REQUESTS
        except ValueError:
            max_requests = -1
        if max_requests < 0:
            raise BookkeepingError("read configuration", f"{MAX_REQUESTS_ENV} must be a whole number, 0 or more")
        return cls(retry_hours=retry_hours, max_requests=max_requests)


@dataclass(frozen=True)
class StoryBoard:
    """A story's title and its position on the project board."""

    number: int
    title: str
    status: str | None
    item_id: str | None = None
    project_id: str | None = None
    field_id: str | None = None
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewThreadFact:
    """One review thread as the job needs it: who opened it, in a review of which commit, and its state."""

    id: str
    url: str | None
    is_resolved: bool
    is_outdated: bool
    opener: str | None
    opener_is_codex: bool
    review_commit: str | None

    @property
    def blocks_gate(self) -> bool:
        """The review-thread gate blocks on unresolved threads that are not outdated."""

        return not self.is_resolved and not self.is_outdated


@dataclass(frozen=True)
class ResolvedThread:
    id: str
    url: str | None
    review_commit: str
    superseded_by: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "review_commit": self.review_commit,
            "superseded_by": self.superseded_by,
        }


@dataclass(frozen=True)
class RemainingThread:
    id: str
    url: str | None
    opener: str | None
    review_commit: str | None
    reason: str
    blocks_gate: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "opener": self.opener,
            "review_commit": self.review_commit,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Decision:
    pr: int
    head_sha: str
    story: int | None
    story_status: str | None
    codex_result: str
    codex_url: str | None
    action: str
    reason: str
    requests_posted: int
    max_requests: int
    # Hidden marker that makes the action's comment idempotent; None for `none`.
    marker: str | None = None
    summary_checks: str | None = None
    summary_blockers: str | None = None
    blocker_unblocked_when: str | None = None
    blocker_detail: str | None = None
    # Review threads: read only on a clean result that the approval gate and the required checks accept.
    threads_read: bool = False
    threads_resolved: tuple[ResolvedThread, ...] = ()
    threads_remaining: tuple[RemainingThread, ...] = ()
    # Superseded Codex threads that were already resolved before this run (an earlier run or a person).
    threads_already_resolved: tuple[ResolvedThread, ...] = ()

    @property
    def summary_next(self) -> str | None:
        if self.action == ACTION_MOVE_IN_MERGE:
            return "merge"
        if self.action == ACTION_MOVE_IN_IMPLEMENTATION:
            return "implementation"
        return None


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    applied: str
    comment_urls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        decision = self.decision
        return {
            "pr": decision.pr,
            "head_sha": decision.head_sha,
            "story": decision.story,
            "story_status": decision.story_status,
            "codex_result": {"result": decision.codex_result, "url": decision.codex_url},
            "action": decision.action,
            "reason": decision.reason,
            "requests_posted": decision.requests_posted,
            "max_requests": decision.max_requests,
            "applied": {"state": self.applied, "comment_urls": list(self.comment_urls)},
            "threads_resolved": [thread.to_dict() for thread in decision.threads_resolved],
            "threads_remaining": [thread.to_dict() for thread in decision.threads_remaining],
        }

    def to_line(self) -> str:
        decision = self.decision
        counts = ""
        if decision.threads_read and decision.action == ACTION_MOVE_IN_MERGE:
            counts = f" threads_resolved={len(decision.threads_resolved)}"
        elif decision.threads_read and decision.action == ACTION_BLOCKER_EXTERNAL:
            counts = f" threads_remaining={len(decision.threads_remaining)}"
        return (
            f"PR #{decision.pr}: result={decision.codex_result} action={decision.action} "
            f"applied={self.applied}{counts} - {decision.reason}"
        )


class GitHubGateway(Protocol):
    """Everything the job reads from or writes to GitHub, so tests can replace it."""

    def fetch_context(self, pr: int) -> PullRequestContext: ...

    def fetch_ci(self, context: PullRequestContext) -> CiSummary: ...

    def fetch_story(self, number: int) -> StoryBoard: ...

    def fetch_issue_comments(self, number: int) -> tuple[str, ...]: ...

    def fetch_open_decision_blocker(self, number: int) -> str | None: ...

    def fetch_review_threads(self, context: PullRequestContext) -> tuple[ReviewThreadFact, ...]: ...

    def resolve_review_thread(self, thread_id: str) -> None: ...

    def post_comment(self, number: int, body: str) -> str: ...

    def set_story_status(self, story: StoryBoard, status: str) -> None: ...

    def session_summary(self, arguments: tuple[str, ...]) -> str: ...

    def list_open_ready_prs(self) -> tuple[int, ...]: ...


def action_marker(action: str, head_sha: str, *, source: str | None = None) -> str:
    """Hidden marker naming the PR head and the kind of action a comment records."""

    suffix = f" source={source}" if source else ""
    return f"{MARKER_PREFIX} action={action} head={head_sha}{suffix} -->"


def review_request_body(head_sha: str, *, ordinal: int) -> str:
    """A review request posted by the job: the trigger phrase first, the marker on a later line."""

    marker = action_marker(ACTION_REQUEST_REVIEW, head_sha, source=f"request-{ordinal}")
    return f"{CODEX_REVIEW_TRIGGER_PHRASE}\n\n{marker}\n"


def linked_story_or_reason(context: PullRequestContext) -> tuple[LinkedIssue | None, str | None]:
    """Return the one Claude-implemented story the PR closes, or why the job does not act."""

    if context.state != "OPEN":
        return None, f"PR is {context.state.lower() or 'not open'}, not open."
    if context.is_draft:
        return None, "PR is a draft."
    if not context.linked_issues:
        return None, "PR has no linked story."
    if len(context.linked_issues) != 1:
        numbers = ", ".join(f"#{issue.number}" for issue in context.linked_issues)
        return None, f"PR has more than one linked story ({numbers})."
    story = context.linked_issues[0]
    labels = [label for label in story.labels if label.lower().startswith(EXECUTOR_LABEL_PREFIX)]
    if not labels:
        return None, f"Linked story #{story.number} has no `executor:` label."
    if len(labels) != 1:
        return None, f"Linked story #{story.number} has {len(labels)} `executor:` labels."
    if not labels[0].lower().startswith(CLAUDE_EXECUTOR_LABEL_PREFIX):
        return None, f"Linked story #{story.number} carries `{labels[0]}`, so `/review` handles it, not this job."
    return story, None


def open_decision_blocker(comments: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The latest `Type: decision` Blocked Declaration that no later PO `/unblock` line lifts.

    Each comment carries `body`, `author`, `created_at` (ISO 8601, so strings of one
    format compare in time order) and `url`. The rule is the one of
    `docs/PROJECT-STATUS.md` (Decision blockers): the PO account, a comment strictly
    after the declaration, a line that trims to exactly `/unblock`.
    """

    items = list(comments)
    declared = [
        c
        for c in items
        if "## Blocked Declaration" in (c.get("body") or "") and "Type: decision" in (c.get("body") or "")
    ]
    if not declared:
        return None
    latest = declared[-1]
    for c in items:
        if (
            c.get("author") == DECISION_BLOCKER_PO_LOGIN
            and (c.get("created_at") or "") > (latest.get("created_at") or "")
            and any(line.strip() == "/unblock" for line in (c.get("body") or "").split("\n"))
        ):
            return None
    return latest


def decide(
    context: PullRequestContext,
    *,
    story_status: str | None,
    ci: CiSummary | None,
    now: datetime,
    config: BookkeepingConfig = BookkeepingConfig(),
    threads: tuple[ReviewThreadFact, ...] | None = None,
    decision_blocker: str | None = None,
) -> Decision:
    """Decide the one action for a PR from GitHub facts and the current time.

    Raises `BookkeepingError` when the facts are incomplete, so the job never
    acts on a partial read.

    `threads` are the PR's review threads. They are read only for a clean result
    that the approval gate and the required checks accept, so `None` there
    yields the preliminary `move-in-merge` that `_read` completes with the
    threads before anything is applied.

    `decision_blocker` names the story's open `Type: decision` blocker (a PO
    waiver decision is pending). While it is set, an `In review` story is held:
    the action is `none`, so no review request, thread resolution or move
    happens until the PO's `/unblock`.
    """

    if context.review_evidence_error:
        raise BookkeepingError("read PR", context.review_evidence_error)

    head_sha = context.head_ref_oid
    head_events = _current_head_events(context)
    requests = [event for event in head_events if is_codex_review_request(event)]
    request_marker = f"{MARKER_PREFIX} action={ACTION_REQUEST_REVIEW} head={head_sha} "
    requests_posted = sum(1 for event in requests if request_marker in (event.body or ""))
    story, reason = linked_story_or_reason(context)
    sole_linked = context.linked_issues[0].number if len(context.linked_issues) == 1 else None

    def result(action: str, codex_result: str, codex_url: str | None, why: str, **extra: Any) -> Decision:
        return Decision(
            pr=context.number,
            head_sha=head_sha,
            story=sole_linked,
            story_status=story_status,
            codex_result=codex_result,
            codex_url=codex_url,
            action=action,
            reason=why,
            requests_posted=requests_posted,
            max_requests=config.max_requests,
            **extra,
        )

    codex_result, codex_event = _codex_result(context, head_events)
    codex_url = codex_event.url if codex_event else None

    if story is None:
        return result(ACTION_NONE, codex_result, codex_url, reason or "PR has no linked story.")
    if story_status != STATUS_IN_REVIEW:
        shown = f"`{story_status}`" if story_status else "not set on the project board"
        return result(
            ACTION_NONE,
            codex_result,
            codex_url,
            f"Story #{story.number} is {shown}, not `{STATUS_IN_REVIEW}`.",
        )
    if not head_sha:
        raise BookkeepingError("read PR", "current PR head SHA is unknown")
    if decision_blocker:
        return result(
            ACTION_NONE,
            codex_result,
            codex_url,
            f"Story #{story.number} has an open decision blocker ({decision_blocker}); "
            "it waits for the PO's `/unblock` line before a review request, a thread resolution or a move.",
        )

    def request_or_blocker(why: str) -> Decision:
        if requests_posted >= config.max_requests:
            marker = action_marker(ACTION_BLOCKER_RESOURCE, head_sha)
            return result(
                ACTION_BLOCKER_RESOURCE,
                codex_result,
                codex_url,
                f"{why} The job already posted {requests_posted} of {config.max_requests} review requests for this head.",
                marker=marker,
                blocker_unblocked_when=(
                    f"Codex posts a review result for head {head_sha} on PR #{context.number}; "
                    "after the Codex usage limit has reset, a new review request comment on the PR starts one"
                ),
            )
        return result(ACTION_REQUEST_REVIEW, codex_result, codex_url, why)

    if codex_result == "clean":
        # A clean Codex result is not enough: the whole approval gate must pass. A reviewer's
        # "changes requested" overrides it (docs/MERGE-POLICY.md), and that reviewer owns the next step.
        policy = summarize_review_policy(context)
        if not policy.hard_gate_passed:
            return result(
                ACTION_NONE,
                codex_result,
                codex_url,
                f"Codex found no major issues, but the approval gate does not pass: {policy.blocking_reason}",
            )
        return _clean_decision(context, ci, result, codex_url, threads)

    if codex_result == "findings":
        return result(
            ACTION_MOVE_IN_IMPLEMENTATION,
            codex_result,
            codex_url,
            "Codex submitted a review with findings for the current head.",
            marker=action_marker(ACTION_MOVE_IN_IMPLEMENTATION, head_sha, source=codex_url),
            summary_checks=f"Codex review of the current head: findings ({codex_url})",
            summary_blockers=f"Codex review findings: {codex_url}",
        )

    if codex_result == "unknown":
        detail = evaluate_codex_review(context).reason if _is_clean(codex_event) else None
        why = f"The latest Codex reply ({codex_url}) is not a result the recognizer accepts"
        return result(
            ACTION_BLOCKER_EXTERNAL,
            codex_result,
            codex_url,
            f"{why}: {detail}." if detail else f"{why}: its format is unknown.",
            marker=action_marker(ACTION_BLOCKER_EXTERNAL, head_sha, source=codex_url),
            blocker_unblocked_when=(
                f"the Codex reply at {codex_url} is understood (the recognizer in "
                f"`src/headless_pr_workflow/review_policy.py` knows its format) or a newer Codex review result "
                f"for head {head_sha} is posted on PR #{context.number}"
            ),
        )

    if codex_result == "usage-limit":
        if not _back_off_passed(codex_event, now, config):
            return result(
                ACTION_NONE,
                codex_result,
                codex_url,
                f"Codex hit its usage limit; a new request is posted {config.retry_hours:g} hours after that reply.",
            )
        return request_or_blocker(f"Codex hit its usage limit and the {config.retry_hours:g}-hour back-off has passed.")

    if codex_result == "pending":
        if not _back_off_passed(codex_event, now, config):
            return result(
                ACTION_NONE,
                codex_result,
                codex_url,
                "A review request for the current head is pending and the back-off has not passed.",
            )
        return request_or_blocker(
            f"The latest review request got no answer within the {config.retry_hours:g}-hour back-off."
        )

    return request_or_blocker("No review request exists for the current head.")


@dataclass(frozen=True)
class _Reading:
    """One complete read of a PR and the decision it leads to."""

    decision: Decision
    context: PullRequestContext
    story: StoryBoard | None
    story_comments: tuple[str, ...]


def run_bookkeeping(
    pr: int,
    *,
    github: GitHubGateway,
    now: datetime,
    config: BookkeepingConfig = BookkeepingConfig(),
    dry_run: bool = False,
) -> Outcome:
    """Read one PR fresh, decide and apply the decision.

    Nothing is posted or moved on the strength of one read. Immediately before
    the first mutation the job reads everything again and decides again; it
    acts only when both readings lead to the same decision, so a push, a new
    Codex result, a changed check or a board move during the run stops it.
    """

    first = _read(pr, github=github, now=now, config=config)

    def confirm() -> None:
        second = _read(pr, github=github, now=now, config=config).decision
        if second != first.decision:
            raise BookkeepingError(
                CHANGED_DURING_RUN_STEP,
                f"PR #{pr} changed during the run: the decision was `{first.decision.action}` for head "
                f"{first.decision.head_sha} and is now `{second.action}` for head {second.head_sha or 'unknown'} "
                f"({second.reason}); nothing was posted or moved",
            )

    return _apply(first, confirm=confirm, github=github, dry_run=dry_run)


def _read(pr: int, *, github: GitHubGateway, now: datetime, config: BookkeepingConfig) -> _Reading:
    context = github.fetch_context(pr)
    if context.review_evidence_error:
        raise BookkeepingError("read PR", context.review_evidence_error)
    linked, _ = linked_story_or_reason(context)

    story: StoryBoard | None = None
    ci: CiSummary | None = None
    story_comments: tuple[str, ...] = ()
    blocker: str | None = None
    if linked is not None:
        story = github.fetch_story(linked.number)
        if story.status == STATUS_IN_REVIEW:
            ci = github.fetch_ci(context)
            story_comments = github.fetch_issue_comments(linked.number)
            blocker = github.fetch_open_decision_blocker(linked.number)

    status = story.status if story else None
    decision = decide(context, story_status=status, ci=ci, now=now, config=config, decision_blocker=blocker)
    if decision.action == ACTION_MOVE_IN_MERGE and decision.codex_result == "clean":
        threads = github.fetch_review_threads(context)
        decision = decide(
            context, story_status=status, ci=ci, now=now, config=config, threads=threads, decision_blocker=blocker
        )
    return _Reading(decision, context, story, story_comments)


def _apply(reading: _Reading, *, confirm: Any, github: GitHubGateway, dry_run: bool) -> Outcome:
    """Apply a decision. `confirm` re-reads and re-decides; it is called right before the first mutation."""

    decision, context, story, story_comments = reading.decision, reading.context, reading.story, reading.story_comments
    if decision.action == ACTION_NONE:
        return Outcome(decision, NOT_NEEDED)
    if story is None or (decision.marker is None and decision.action != ACTION_REQUEST_REVIEW):
        raise BookkeepingError("apply decision", f"decision `{decision.action}` is incomplete")

    if decision.action in MOVE_TARGETS:
        target = MOVE_TARGETS[decision.action]
        # Checked before any mutation, so a missing option never leaves a summary without its move.
        if not story.item_id or not story.project_id or not story.field_id or target not in story.options:
            raise BookkeepingError("look up board option", f"the project board has no `{target}` status option")
        summary_present = any(decision.marker in (event.body or "") for event in context.conversation_comments)
        if dry_run:
            return Outcome(decision, DRY_RUN)
        confirm()
        # Order of mutations: resolve the threads, post the summary that lists them, move the story.
        for thread in decision.threads_resolved:
            github.resolve_review_thread(thread.id)
        urls: tuple[str, ...] = ()
        if not summary_present:
            summary = github.session_summary(_session_summary_arguments(decision))
            urls = (github.post_comment(decision.pr, f"{summary}\n{decision.marker}\n"),)
        github.set_story_status(story, target)
        return Outcome(decision, APPLIED, urls)

    if decision.action == ACTION_REQUEST_REVIEW:
        if dry_run:
            return Outcome(decision, DRY_RUN)
        confirm()
        body = review_request_body(decision.head_sha, ordinal=decision.requests_posted + 1)
        return Outcome(decision, APPLIED, (github.post_comment(decision.pr, body),))

    if any(decision.marker in body for body in story_comments):
        return Outcome(decision, ALREADY_PRESENT)
    if dry_run:
        return Outcome(decision, DRY_RUN)
    confirm()
    return Outcome(decision, APPLIED, (github.post_comment(story.number, blocker_body(decision, story)),))


def blocker_body(decision: Decision, story: StoryBoard) -> str:
    """A Blocked Declaration in the format of `docs/PROJECT-STATUS.md`, posted on the story."""

    blocker_type = "resource" if decision.action == ACTION_BLOCKER_RESOURCE else "external"
    return (
        "## Blocked Declaration\n"
        f"Type: {blocker_type}\n"
        f"Declared by: {DECLARED_BY}\n"
        f"Blocks: #{story.number} — {story.title}\n"
        f"Unblocked when: {decision.blocker_unblocked_when}\n"
        f"Owner: {BLOCKER_OWNER}\n"
        f"State of in-progress work: PR #{decision.pr} at head {decision.head_sha}; "
        f"the story stays in `{STATUS_IN_REVIEW}`. {decision.blocker_detail or decision.reason}\n"
        "Resume instruction: n/a\n"
        f"{decision.marker}\n"
    )


def _session_summary_arguments(decision: Decision) -> tuple[str, ...]:
    return (
        "--command", "review",
        "--issue", str(decision.story),
        "--pr", str(decision.pr),
        "--head", decision.head_sha,
        "--checks", decision.summary_checks or "none",
        "--blockers", decision.summary_blockers or "none",
        "--next", decision.summary_next or "implementation",
        "--deviation", AUTOMATION_DEVIATION,
    )  # fmt: skip


def _clean_decision(
    context: PullRequestContext,
    ci: CiSummary | None,
    result: Any,
    codex_url: str | None,
    threads: tuple[ReviewThreadFact, ...] | None = None,
) -> Decision:
    if ci is None:
        raise BookkeepingError("read required checks", "required check state was not read")
    status = ci.required_check_status
    names = ", ".join(ci.required_checks.names) or "none configured"
    if ci.required_checks_satisfied or status == "not_configured":
        head_sha = context.head_ref_oid
        checks = f"Codex review of the current head: clean ({codex_url}); required checks: {status} ({names})"
        merge_reason = "Codex found no major issues on the current head and the required checks are green."
        merge_marker = action_marker(ACTION_MOVE_IN_MERGE, head_sha, source=codex_url)
        if threads is None:
            return result(
                ACTION_MOVE_IN_MERGE,
                "clean",
                codex_url,
                merge_reason,
                marker=merge_marker,
                summary_checks=checks,
                summary_blockers="none",
            )
        resolvable, remaining, already = classify_threads(threads, head_sha=head_sha, clean_url=codex_url)
        blocking = [thread for thread in remaining if thread.blocks_gate]
        if blocking:
            count = len(blocking)
            listing = "; ".join(f"{thread.url} ({thread.reason})" for thread in blocking)
            detail = "; ".join(f"{thread.url} - {THREAD_REASON_TEXT[thread.reason]}" for thread in blocking)
            return result(
                ACTION_BLOCKER_EXTERNAL,
                "clean",
                codex_url,
                f"Codex found no major issues, but {count} review thread(s) may not be resolved by the job: {listing}.",
                marker=action_marker(ACTION_BLOCKER_EXTERNAL, head_sha, source="review-threads"),
                blocker_unblocked_when=(
                    f"the listed threads are resolved on GitHub, or a new head is pushed to PR #{context.number}"
                ),
                blocker_detail=(
                    f"Codex found no major issues on this head, but {count} unresolved review thread(s) that the "
                    f"job may not resolve would fail the review-thread gate of the merge. Nothing was resolved. "
                    f"Threads: {detail}."
                ),
                threads_read=True,
                threads_remaining=remaining,
                threads_already_resolved=already,
            )
        resolved_note = f" {len(resolvable)} superseded Codex review thread(s) are resolved first." if resolvable else ""
        return result(
            ACTION_MOVE_IN_MERGE,
            "clean",
            codex_url,
            merge_reason + resolved_note,
            marker=merge_marker,
            summary_checks=f"{checks}; {_threads_summary(resolvable, already)}",
            summary_blockers="none",
            threads_read=True,
            threads_resolved=resolvable,
            threads_remaining=remaining,
            threads_already_resolved=already,
        )
    if status in {"pending", "missing"}:
        return result(
            ACTION_NONE,
            "clean",
            codex_url,
            f"Codex found no major issues, but a required check is still running ({status}: {names}).",
        )
    if status == "failing":
        failing = set(ci.check_buckets.get("failing", ()))
        failed = ", ".join(name for name in ci.required_checks.names if name in failing) or names
        return result(
            ACTION_MOVE_IN_IMPLEMENTATION,
            "clean",
            codex_url,
            f"Codex found no major issues, but a required check failed: {failed}.",
            marker=action_marker(ACTION_MOVE_IN_IMPLEMENTATION, context.head_ref_oid, source="required-checks"),
            summary_checks=f"Codex review of the current head: clean ({codex_url}); required checks: failing ({failed})",
            summary_blockers=f"required check failed: {failed}",
        )
    detail = "; ".join(ci.messages) or status
    raise BookkeepingError("read required checks", f"required check state is `{status}`: {detail}")


def classify_threads(
    threads: tuple[ReviewThreadFact, ...], *, head_sha: str, clean_url: str | None
) -> tuple[tuple[ResolvedThread, ...], tuple[RemainingThread, ...], tuple[ResolvedThread, ...]]:
    """Split the review threads into those to resolve, those that remain and those already resolved.

    Only an unresolved thread opened by the Codex bot in a review of a known
    commit other than the current head is resolvable. A reply never changes who
    opened a thread, and a thread whose opener or review commit is unknown stays.
    """

    head = head_sha.lower()
    resolvable: list[ResolvedThread] = []
    remaining: list[RemainingThread] = []
    already: list[ResolvedThread] = []
    for thread in threads:
        commit = (thread.review_commit or "").lower() or None
        superseded = thread.opener_is_codex and commit is not None and commit != head
        if thread.is_resolved:
            if superseded:
                already.append(ResolvedThread(thread.id, thread.url, commit, clean_url))
            continue
        if superseded:
            resolvable.append(ResolvedThread(thread.id, thread.url, commit, clean_url))
            continue
        if not thread.opener_is_codex:
            reason = THREAD_NOT_CODEX
        elif commit is None:
            reason = THREAD_UNKNOWN_COMMIT
        else:
            reason = THREAD_CURRENT_HEAD
        remaining.append(RemainingThread(thread.id, thread.url, thread.opener, commit, reason, thread.blocks_gate))
    return tuple(resolvable), tuple(remaining), tuple(already)


def _threads_summary(resolved: tuple[ResolvedThread, ...], already: tuple[ResolvedThread, ...]) -> str:
    if resolved:
        items = ", ".join(
            f"{thread.url} (opened in the Codex review of {thread.review_commit}, "
            f"superseded by the clean Codex result {thread.superseded_by})"
            for thread in resolved
        )
        text = f"Codex threads resolved by the job: {items}"
    else:
        text = "no Codex threads resolved"
    if already:
        items = ", ".join(f"{thread.url} (review of {thread.review_commit})" for thread in already)
        text += f"; superseded Codex threads already resolved before this run: {items}"
    return text


def _current_head_events(context: PullRequestContext) -> tuple[TimelineEvent, ...]:
    """Events after the last commit or force-push: the ordering rule the recognizer uses."""

    last_push = -1
    for index, event in enumerate(context.timeline):
        if event.kind in {"commit", "force-push"}:
            last_push = index
    return context.timeline[last_push + 1 :]


def _codex_result(
    context: PullRequestContext,
    head_events: tuple[TimelineEvent, ...],
) -> tuple[str, TimelineEvent | None]:
    """The Codex result for the current head and the event it was read from.

    `clean` exactly when the approval gate's recognizer accepts it, so the job
    and the gate cannot disagree. A clean comment it rejects for another reason
    than naming an older commit is `unknown`.
    """

    accepted = evaluate_codex_review(context)
    if accepted.status == "accepted":
        clean = [event for event in head_events if _is_clean(event) and event.url == accepted.comment_url]
        return "clean", clean[-1] if clean else TimelineEvent(kind="comment", url=accepted.comment_url)

    head_sha = context.head_ref_oid.lower()
    latest_index = -1
    latest: TimelineEvent | None = None
    latest_request_index = -1
    latest_request: TimelineEvent | None = None
    for index, event in enumerate(head_events):
        if is_codex_review_request(event):
            latest_request_index, latest_request = index, event
            continue
        kind = classify_codex_event(event)
        # Codex's progress comment is not a reply: a request stays pending while it says "Running".
        if kind is None or kind == "status" or _names_older_commit(event, kind, head_sha):
            continue
        latest_index, latest = index, event

    if latest is None or latest_request_index > latest_index:
        return ("pending", latest_request) if latest_request else ("none", None)

    kind = classify_codex_event(latest) or "unknown"
    # A clean comment reaching this point was rejected by the recognizer.
    return ("unknown" if kind == "clean" else kind), latest


def _names_older_commit(event: TimelineEvent, kind: str, head_sha: str) -> bool:
    if kind not in {"clean", "findings"}:
        return False
    if event.kind == "review" and event.oid:
        return event.oid.lower() != head_sha
    prefix = codex_reviewed_commit(event)
    return prefix is not None and not head_sha.startswith(prefix)


def _is_clean(event: TimelineEvent | None) -> bool:
    return event is not None and classify_codex_event(event) == "clean"


def _back_off_passed(event: TimelineEvent | None, now: datetime, config: BookkeepingConfig) -> bool:
    created_at = _parse_time(event.created_at if event else None)
    return now - created_at >= config.back_off


def _parse_time(value: str | None) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        raise BookkeepingError("read PR", f"a comment timestamp could not be read: {value!r}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class GhCliGateway:
    """`GitHubGateway` backed by the authenticated `gh` CLI."""

    def __init__(self, repo: str) -> None:
        owner, _, name = repo.partition("/")
        if not owner or not name or "/" in name:
            raise BookkeepingError("read arguments", "--repo must be in OWNER/REPO format")
        self.repo = repo
        self.owner = owner
        self.name = name

    def fetch_context(self, pr: int) -> PullRequestContext:
        try:
            return fetch_pr_context(str(pr), repo=self.repo)
        except GHCommandError as error:
            raise _gh_failure("read PR", str(error), str(error)) from error

    def fetch_ci(self, context: PullRequestContext) -> CiSummary:
        try:
            required_checks = fetch_required_status_check_context(self.repo, context.base_ref_name)
        except GHCommandError as error:
            raise _gh_failure("read required checks", str(error), str(error)) from error
        return summarize_ci(context, required_checks=required_checks)

    def fetch_story(self, number: int) -> StoryBoard:
        step = "read story board state"
        payload = self._json(
            step,
            ["api", "graphql", "-f", f"query={STORY_BOARD_QUERY}", "-f", f"owner={self.owner}",
             "-f", f"name={self.name}", "-F", f"number={number}"],
        )  # fmt: skip
        return parse_story_board(payload, number=number, step=step)

    def fetch_issue_comments(self, number: int) -> tuple[str, ...]:
        step = "read story comments"
        output = self._run(
            step,
            ["api", f"repos/{self.repo}/issues/{number}/comments", "--paginate", "--jq", ".[] | {body}"],
        )
        bodies: list[str] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            try:
                bodies.append(str(json.loads(line).get("body") or ""))
            except (json.JSONDecodeError, AttributeError):
                raise BookkeepingError(step, "GitHub returned a comment that could not be parsed") from None
        return tuple(bodies)

    def fetch_open_decision_blocker(self, number: int) -> str | None:
        step = "read story decision blocker"
        output = self._run(
            step,
            [
                "api", f"repos/{self.repo}/issues/{number}/comments", "--paginate",
                "--jq", ".[] | {body, author: .user.login, created_at, url: .html_url}",
            ],
        )  # fmt: skip
        comments: list[Mapping[str, Any]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            try:
                comments.append(json.loads(line))
            except (json.JSONDecodeError, AttributeError):
                raise BookkeepingError(step, "GitHub returned a comment that could not be parsed") from None
        blocker = open_decision_blocker(comments)
        if blocker is None:
            return None
        return str(blocker.get("url") or f"comment of {blocker.get('created_at')}")

    def fetch_review_threads(self, context: PullRequestContext) -> tuple[ReviewThreadFact, ...]:
        step = "read review threads"
        try:
            raw = fetch_review_threads_for_context(context, repo=self.repo)
        except GHCommandError as error:
            raise _gh_failure(step, str(error), str(error)) from error
        facts: list[ReviewThreadFact] = []
        for summary in summarize_review_threads(context, raw).threads:
            if not summary.id:
                raise BookkeepingError(step, "GitHub returned a review thread without an id")
            opening = summary.comments[0] if summary.comments else None
            facts.append(
                ReviewThreadFact(
                    id=summary.id,
                    url=opening.url if opening else None,
                    is_resolved=summary.is_resolved,
                    is_outdated=summary.is_outdated,
                    opener=opening.author if opening else None,
                    opener_is_codex=opening is not None and is_codex_actor(opening.author, opening.author_type),
                    review_commit=opening.review_commit_oid if opening else None,
                )
            )
        return tuple(facts)

    def resolve_review_thread(self, thread_id: str) -> None:
        step = "resolve review thread"
        payload = self._json(
            step,
            ["api", "graphql", "-f", f"query={RESOLVE_THREAD_MUTATION}", "-f", f"threadId={thread_id}"],
        )
        thread = ((payload.get("data") or {}).get("resolveReviewThread") or {}).get("thread") or {}
        if payload.get("errors") or thread.get("id") != thread_id or thread.get("isResolved") is not True:
            raise BookkeepingError(step, f"GitHub did not confirm that review thread {thread_id} is resolved")

    def post_comment(self, number: int, body: str) -> str:
        step = f"post comment on #{number}"
        payload = self._json(
            step,
            ["api", f"repos/{self.repo}/issues/{number}/comments", "--method", "POST", "--input", "-"],
            stdin=json.dumps({"body": body}),
        )
        url = payload.get("html_url")
        if not isinstance(url, str) or not url:
            raise BookkeepingError(step, "GitHub did not confirm the comment")
        return url

    def set_story_status(self, story: StoryBoard, status: str) -> None:
        step = f"move story #{story.number} to `{status}`"
        payload = self._json(
            step,
            ["api", "graphql", "-f", f"query={SET_STATUS_MUTATION}", "-f", f"projectId={story.project_id}",
             "-f", f"itemId={story.item_id}", "-f", f"fieldId={story.field_id}",
             "-f", f"optionId={story.options[status]}"],
        )  # fmt: skip
        item = (((payload.get("data") or {}).get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item") or {})
        if payload.get("errors") or not item.get("id"):
            raise BookkeepingError(step, "GitHub did not confirm the board update")

    def session_summary(self, arguments: tuple[str, ...]) -> str:
        return run_session_summary(arguments)

    def list_open_ready_prs(self) -> tuple[int, ...]:
        step = "list open pull requests"
        output = self._run(
            step,
            ["pr", "list", "--repo", self.repo, "--state", "open", "--limit", "1000", "--json", "number,isDraft"],
        )
        try:
            return tuple(sorted(int(pr["number"]) for pr in json.loads(output) if not pr.get("isDraft")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise BookkeepingError(step, "GitHub returned a pull request list that could not be parsed") from None

    def _json(self, step: str, arguments: list[str], *, stdin: str | None = None) -> dict[str, Any]:
        output = self._run(step, arguments, stdin=stdin)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            raise BookkeepingError(step, "GitHub returned a response that could not be parsed")
        return payload

    def _run(self, step: str, arguments: list[str], *, stdin: str | None = None) -> str:
        try:
            result = subprocess.run(
                ["gh", *arguments], input=stdin, capture_output=True, encoding="utf-8", check=False, env=_gh_env()
            )
        except FileNotFoundError:
            raise BookkeepingError(step, "GitHub CLI executable not found: gh") from None
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            detail = stderr.splitlines()
            raise _gh_failure(step, stderr, detail[-1] if detail else f"gh exited with code {result.returncode}")
        return result.stdout or ""


def parse_story_board(payload: dict[str, Any], *, number: int, step: str = "read story board state") -> StoryBoard:
    """Normalize the `STORY_BOARD_QUERY` response; fail when it is partial or ambiguous."""

    issue = (((payload.get("data") or {}).get("repository") or {}).get("issue")) if not payload.get("errors") else None
    items = issue.get("projectItems") if isinstance(issue, dict) else None
    nodes = items.get("nodes") if isinstance(items, dict) else None
    if not isinstance(nodes, list) or (items.get("pageInfo") or {}).get("hasNextPage") is not False:
        raise BookkeepingError(step, f"project items of story #{number} could not be read completely")
    title = str(issue.get("title") or "")
    active = [node for node in nodes if isinstance(node, dict) and not node.get("isArchived")]
    if not active:
        return StoryBoard(number=number, title=title, status=None)
    if len(active) != 1:
        raise BookkeepingError(step, f"story #{number} is on {len(active)} project boards; expected one")

    item = active[0]
    value = item.get("fieldValueByName") or {}
    status_field = value.get("field") or {}
    options = status_field.get("options") or []
    return StoryBoard(
        number=number,
        title=title,
        status=value.get("name"),
        item_id=item.get("id"),
        project_id=(item.get("project") or {}).get("id"),
        field_id=status_field.get("id"),
        options={option["name"]: option["id"] for option in options if isinstance(option, dict) and option.get("name")},
    )


def run_session_summary(arguments: tuple[str, ...]) -> str:
    """Return the exact stdout of `scripts/session-summary.sh` for the arguments."""

    step = "generate Session Summary"
    try:
        # Fed on stdin with LF endings so a CRLF checkout on Windows runs it too.
        script = SESSION_SUMMARY_SCRIPT.read_bytes().replace(b"\r\n", b"\n")
        result = subprocess.run(["bash", "-s", "--", *arguments], input=script, capture_output=True, check=False)
    except OSError as error:
        raise BookkeepingError(step, f"scripts/session-summary.sh could not be run: {error}") from error
    stdout = result.stdout.decode("utf-8")
    if result.returncode != 0 or not stdout.strip():
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise BookkeepingError(step, detail[0] if detail else f"session-summary.sh exited with code {result.returncode}")
    return stdout
