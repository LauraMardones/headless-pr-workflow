"""Deterministic decision model for the verify-closure workflow command.

The assistant command remains responsible for collecting and citing evidence.
This module makes its safety gates executable and independently testable without
live GitHub access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


SUPPORTED_TYPES = frozenset({"type:feature", "type:epic"})
REQUIRED_CHECK_COMMANDS = frozenset(
    {
        "python -m pytest tests/test_verify_closure_command.py",
        "python -m pytest",
    }
)


class VerificationBlocked(ValueError):
    """Raised when technical closure verification must fail closed."""

    def __init__(self, *blockers: str) -> None:
        self.blockers = blockers
        super().__init__("; ".join(blockers))


class ClosurePartialFailure(RuntimeError):
    """Raised after close succeeds but its durable evidence cannot be posted."""


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    state: str
    labels: frozenset[str]
    parent_number: int | None = None
    declared_criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class PullRequest:
    number: int
    merged: bool
    merge_commit: str | None


@dataclass(frozen=True)
class EvidenceRow:
    criterion: str
    result: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    command: str
    outcome: str


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created_at: str
    url: str
    edited: bool = False


@dataclass(frozen=True)
class TechnicalSummary:
    issue_number: int
    target_type: str
    main_sha: str
    created_at: str
    url: str
    checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClosureResult:
    action: str
    target: Issue
    main_sha: str
    summary_url: str
    confirmation_url: str
    evidence_url: str | None


@dataclass(frozen=True)
class InventoryItem:
    issue: Issue
    merged_prs: tuple[PullRequest, ...]


@dataclass(frozen=True)
class VerificationResult:
    target: Issue
    target_type: str
    main_sha: str
    inventory: tuple[InventoryItem, ...]
    evidence: tuple[EvidenceRow, ...]
    checks: tuple[CheckResult, ...]
    existing_summary_url: str | None = None

    @property
    def should_post_summary(self) -> bool:
        return self.existing_summary_url is None


class GitHubEvidence(Protocol):
    def repository_name(self) -> str: ...

    def issue(self, number: int) -> Issue: ...

    def native_children(self, number: int) -> Sequence[Issue]: ...

    def metadata_children(self, number: int) -> Sequence[Issue]: ...

    def linked_pull_requests(self, issue_number: int) -> Sequence[PullRequest]: ...

    def main_sha(self) -> str: ...

    def summary_urls(self, marker: str) -> Sequence[str]: ...


class LocalEvidence(Protocol):
    def head_sha(self) -> str: ...

    def evidence_rows(self, target: Issue) -> Sequence[EvidenceRow]: ...

    def check_results(self) -> Sequence[CheckResult]: ...


class GitHubClosure(Protocol):
    """Fresh GitHub state and the two permitted continuation mutations."""

    def repository_name(self) -> str: ...

    def issue(self, number: int) -> Issue: ...

    def main_sha(self) -> str: ...

    def technical_summaries(self, number: int) -> Sequence[TechnicalSummary]: ...

    def comments(self, number: int) -> Sequence[Comment]: ...

    def child_features(self, number: int) -> Sequence[Issue]: ...

    def closing_evidence_urls(self, marker: str) -> Sequence[str]: ...

    def close_issue(self, number: int) -> None: ...

    def post_closing_evidence(self, number: int, body: str) -> str: ...


def parse_issue_number(argument_text: str) -> int:
    """Return one positive issue number or fail before any external mutation."""

    tokens = argument_text.split()
    if len(tokens) != 1 or not tokens[0].isdigit() or tokens[0].startswith("0"):
        raise VerificationBlocked(
            "exactly one positive integer issue number is required"
        )
    number = int(tokens[0])
    if number <= 0:
        raise VerificationBlocked(
            "exactly one positive integer issue number is required"
        )
    return number


def _target_type(issue: Issue) -> str:
    types = issue.labels & SUPPORTED_TYPES
    if len(types) != 1:
        raise VerificationBlocked("target must have exactly one supported type label")
    return next(iter(types))


def _inventory(github: GitHubEvidence, target: Issue) -> tuple[InventoryItem, ...]:
    native = {child.number: child for child in github.native_children(target.number)}
    metadata = {
        child.number: child for child in github.metadata_children(target.number)
    }

    blockers: list[str] = []
    for number in native.keys() & metadata.keys():
        if native[number] != metadata[number]:
            blockers.append(f"conflicting parent evidence for child #{number}")

    children = native | metadata
    for child in children.values():
        if child.parent_number not in (None, target.number):
            blockers.append(f"ambiguous parent for child #{child.number}")

    if blockers:
        raise VerificationBlocked(*blockers)

    items = []
    for child in sorted(children.values(), key=lambda value: value.number):
        merged = tuple(
            pr
            for pr in github.linked_pull_requests(child.number)
            if pr.merged and pr.merge_commit
        )
        items.append(InventoryItem(issue=child, merged_prs=merged))
    return tuple(items)


def _validate_evidence(
    declared_criteria: tuple[str, ...],
    rows: tuple[EvidenceRow, ...],
    checks: tuple[CheckResult, ...],
) -> None:
    blockers: list[str] = []
    if not declared_criteria:
        blockers.append("target has no declared criteria")
    row_criteria = tuple(row.criterion for row in rows)
    if row_criteria != declared_criteria:
        blockers.append("evidence rows do not exactly match declared criteria")
    for row in rows:
        if row.result != "PASS" or not row.evidence:
            blockers.append(f"criterion is not proven: {row.criterion}")
    check_commands = {check.command for check in checks}
    missing_commands = sorted(REQUIRED_CHECK_COMMANDS - check_commands)
    for command in missing_commands:
        blockers.append(f"required check is missing: {command}")
    for check in checks:
        if check.outcome != "PASS":
            blockers.append(f"check is {check.outcome}: {check.command}")
    if blockers:
        raise VerificationBlocked(*blockers)


def verify_closure(
    argument_text: str, github: GitHubEvidence, local: LocalEvidence
) -> VerificationResult:
    """Evaluate all deterministic gates and return a SHA-bound result.

    Collaborators are protocols so tests and callers can inject mocked GitHub,
    checkout, evidence, and command outcomes without network or credentials.
    """

    number = parse_issue_number(argument_text)
    if github.repository_name() != "LauraMardones/headless-pr-workflow":
        raise VerificationBlocked("repository identity does not match")

    target = github.issue(number)
    if target.state != "OPEN":
        raise VerificationBlocked("target issue must be open")
    target_type = _target_type(target)

    main_sha = github.main_sha()
    if not main_sha or local.head_sha() != main_sha:
        raise VerificationBlocked("local HEAD does not match remote main")

    inventory = _inventory(github, target)
    if target_type == "type:epic":
        open_features = [
            item.issue.number
            for item in inventory
            if item.issue.labels & SUPPORTED_TYPES != {"type:feature"}
            or item.issue.state != "CLOSED"
        ]
        if open_features:
            joined = ", ".join(f"#{number}" for number in open_features)
            raise VerificationBlocked(
                f"Epic has open or invalid child Features: {joined}"
            )

    evidence = tuple(local.evidence_rows(target))
    checks = tuple(local.check_results())
    _validate_evidence(target.declared_criteria, evidence, checks)

    # Model the command's mandatory final refresh immediately before mutation.
    refreshed = github.issue(number)
    refreshed_main = github.main_sha()
    if refreshed.state != "OPEN" or _target_type(refreshed) != target_type:
        raise VerificationBlocked("target changed during verification")
    if refreshed.declared_criteria != target.declared_criteria:
        raise VerificationBlocked("declared criteria changed during verification")
    if refreshed_main != main_sha:
        raise VerificationBlocked("main changed during verification")

    marker = f"<!-- verify-closure:issue={number};main={main_sha} -->"
    urls = tuple(github.summary_urls(marker))
    if len(urls) > 1:
        raise VerificationBlocked("multiple authoritative summaries exist")

    return VerificationResult(
        target=target,
        target_type=target_type,
        main_sha=main_sha,
        inventory=inventory,
        evidence=evidence,
        checks=checks,
        existing_summary_url=urls[0] if urls else None,
    )


def _confirmation_text(target_type: str, number: int) -> str:
    noun = "Feature" if target_type == "type:feature" else "Epic"
    verb = "confirmed" if target_type == "type:feature" else "approved"
    return f"Product {verb} for {noun} #{number}."


def _closing_marker(number: int, main_sha: str) -> str:
    return f"<!-- verify-closure-close:issue={number};main={main_sha} -->"


def _closing_body(
    target: Issue,
    target_type: str,
    summary: TechnicalSummary,
    confirmation: Comment,
) -> str:
    checks = (
        ", ".join(summary.checks) if summary.checks else "recorded in technical summary"
    )
    return "\n".join(
        (
            "## Closure Evidence",
            _closing_marker(target.number, summary.main_sha),
            "",
            "- Repository: `LauraMardones/headless-pr-workflow`",
            f"- Target: #{target.number} — {target.title}",
            f"- Type: `{target_type}`",
            f"- Verified `main`: `{summary.main_sha}`",
            f"- Technical summary: {summary.url}",
            f"- PO confirmation: `{confirmation.author}` at "
            f"`{confirmation.created_at}` — {confirmation.url}",
            f"- Checks: {checks}",
            "- Blockers: none",
            "- Close result: closed",
        )
    )


def continue_closure(argument_text: str, github: GitHubClosure) -> ClosureResult:
    """Validate fresh PO confirmation and safely complete or repair closure.

    All mutation-sensitive state is collected from ``github`` in this function,
    immediately before the close. Callers must not supply cached issue state.
    """

    number = parse_issue_number(argument_text)
    if github.repository_name() != "LauraMardones/headless-pr-workflow":
        raise VerificationBlocked("repository identity does not match")

    target = github.issue(number)
    target_type = _target_type(target)
    summaries = tuple(github.technical_summaries(number))
    if not summaries:
        raise VerificationBlocked("no authoritative technical summary exists")
    summary = max(summaries, key=lambda item: item.created_at)
    if summary.issue_number != number or summary.target_type != target_type:
        raise VerificationBlocked("technical summary target or type does not match")

    main_sha = github.main_sha()
    if not main_sha or summary.main_sha != main_sha:
        raise VerificationBlocked(
            "technical summary is stale; rerun technical verification"
        )

    expected = _confirmation_text(target_type, number)
    confirmations = tuple(
        comment
        for comment in github.comments(number)
        if comment.author == "LauraMardones"
        and not comment.edited
        and comment.created_at > summary.created_at
        and comment.body.strip() == expected
    )
    if len(confirmations) != 1:
        raise VerificationBlocked(
            "exactly one fresh, unedited, target-specific PO confirmation is required"
        )
    confirmation = confirmations[0]

    if target_type == "type:epic":
        children = tuple(github.child_features(number))
        invalid = [
            child.number
            for child in children
            if child.labels & SUPPORTED_TYPES != {"type:feature"}
            or child.state != "CLOSED"
        ]
        if invalid:
            joined = ", ".join(f"#{child}" for child in invalid)
            raise VerificationBlocked(
                f"Epic has open or invalid child Features: {joined}"
            )

    marker = _closing_marker(number, main_sha)
    evidence_urls = tuple(github.closing_evidence_urls(marker))
    if len(evidence_urls) > 1:
        raise VerificationBlocked("multiple authoritative closing comments exist")

    # Final refresh: no object collected above this point is trusted for the
    # mutation. This intentionally repeats every mutation-sensitive read.
    if github.repository_name() != "LauraMardones/headless-pr-workflow":
        raise VerificationBlocked("repository identity changed before closure")
    refreshed = github.issue(number)
    if _target_type(refreshed) != target_type or refreshed.state != target.state:
        raise VerificationBlocked("target type or state changed before closure")
    if github.main_sha() != main_sha:
        raise VerificationBlocked("main changed before closure; rerun verification")
    refreshed_summaries = tuple(github.technical_summaries(number))
    if (
        not refreshed_summaries
        or max(refreshed_summaries, key=lambda item: item.created_at) != summary
    ):
        raise VerificationBlocked("technical summary changed before closure")
    refreshed_confirmations = tuple(
        comment
        for comment in github.comments(number)
        if comment.author == "LauraMardones"
        and not comment.edited
        and comment.created_at > summary.created_at
        and comment.body.strip() == expected
    )
    if refreshed_confirmations != (confirmation,):
        raise VerificationBlocked("PO confirmation changed before closure")
    if tuple(github.closing_evidence_urls(marker)) != evidence_urls:
        raise VerificationBlocked("closing evidence changed before closure")
    if target_type == "type:epic":
        refreshed_children = tuple(github.child_features(number))
        invalid = [
            child.number
            for child in refreshed_children
            if child.labels & SUPPORTED_TYPES != {"type:feature"}
            or child.state != "CLOSED"
        ]
        if invalid:
            joined = ", ".join(f"#{child}" for child in invalid)
            raise VerificationBlocked(
                f"Epic has open or invalid child Features: {joined}"
            )
    target = refreshed

    if target.state == "CLOSED" and evidence_urls:
        return ClosureResult(
            "NOOP",
            target,
            main_sha,
            summary.url,
            confirmation.url,
            evidence_urls[0],
        )
    if target.state not in {"OPEN", "CLOSED"}:
        raise VerificationBlocked("target issue has an unsupported state")

    body = _closing_body(target, target_type, summary, confirmation)
    action = "REPAIRED" if target.state == "CLOSED" else "CLOSED"
    if target.state == "OPEN":
        github.close_issue(number)
    try:
        evidence_url = github.post_closing_evidence(number, body)
    except Exception as error:
        if action == "CLOSED":
            raise ClosurePartialFailure(
                "issue closed but closing evidence was not posted; rerun to repair"
            ) from error
        raise

    return ClosureResult(
        action,
        target,
        main_sha,
        summary.url,
        confirmation.url,
        evidence_url,
    )
