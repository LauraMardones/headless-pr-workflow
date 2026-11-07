"""Deterministic decision model for the verify-closure workflow command.

The assistant command remains responsible for collecting and citing evidence.
This module makes its safety gates executable and independently testable without
live GitHub access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


SUPPORTED_TYPES = frozenset({"type:feature", "type:epic"})
REQUIRED_CHECK_COMMANDS = frozenset(
    {"python -m pytest tests/test_verify_closure_command.py"}
)
CI_CHECK_NAME = "pytest"
REPOSITORY = "LauraMardones/headless-pr-workflow"
PO_CHECK = "PO CHECK"
NO_SPEC_LINE = "Spec: none found — criteria taken from the issue body"
_EPIC_CRITERION = re.compile(r"^- \*\*(E\d+):\*\*[ \t]*(.*)$")
_VERIFIED_BY = re.compile(r"^\s+- \*\*Verified by:\*\*[ \t]*(.*)$")
_THE_PO = re.compile(r"\bthe po\b", re.IGNORECASE)


class VerificationBlocked(ValueError):
    """Raised when technical closure verification must fail closed."""

    def __init__(self, *blockers: str) -> None:
        self.blockers = blockers
        super().__init__("; ".join(blockers))


class ClosurePartialFailure(RuntimeError):
    """Raised after close succeeds but its durable evidence cannot be posted."""


class ClosureDeclined(ValueError):
    """Raised when a fresh, scoped PO reply of exactly "no" declines closure."""

    def __init__(self, confirmation: "Comment") -> None:
        self.confirmation = confirmation
        super().__init__(
            f"closure declined by {confirmation.author} at {confirmation.created_at}"
        )


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
    po_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpecCriterion:
    id: str
    text: str
    verified_by: str

    @property
    def label(self) -> str:
        """The criterion as an evidence row names it: id and spec wording."""

        return f"{self.id}: {self.text}"

    @property
    def po_may_check(self) -> bool:
        return bool(_THE_PO.search(self.verified_by))


@dataclass(frozen=True)
class Spec:
    path: str
    goal: str
    criteria: tuple[SpecCriterion, ...]

    @property
    def declared_criteria(self) -> tuple[str, ...]:
        return tuple(criterion.label for criterion in self.criteria)


@dataclass(frozen=True)
class CheckResult:
    command: str
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class CiRun:
    """One attempt of the CI ``pytest`` check run for a commit."""

    sha: str
    status: str  # queued, in_progress or completed
    conclusion: str | None
    url: str
    attempt: int = 1
    run_id: int = 1  # workflow run id; grows with run creation, attempts count per run


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
    po_check_criteria: tuple[str, ...] = ()


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
    spec: Spec | None = None

    @property
    def should_post_summary(self) -> bool:
        return self.existing_summary_url is None

    @property
    def spec_line(self) -> str | None:
        """The summary header line naming the criteria source (Epics only)."""

        if self.target_type != "type:epic":
            return None
        if self.spec is None:
            return f"- {NO_SPEC_LINE}"
        return f"- Spec: {self.spec.path} @ {self.main_sha}"

    @property
    def po_check_criteria(self) -> tuple[str, ...]:
        return tuple(row.criterion for row in self.evidence if row.result == PO_CHECK)


class GitHubEvidence(Protocol):
    def repository_name(self) -> str: ...

    def issue(self, number: int) -> Issue: ...

    def native_children(self, number: int) -> Sequence[Issue]: ...

    def metadata_children(self, number: int) -> Sequence[Issue]: ...

    def linked_pull_requests(self, issue_number: int) -> Sequence[PullRequest]: ...

    def main_sha(self) -> str: ...

    def summary_urls(self, marker: str) -> Sequence[str]: ...

    def ci_pytest_runs(self, sha: str) -> Sequence[CiRun]:
        """Return every CI ``pytest`` run attempt for ``sha``; raise if unreadable."""
        ...


class LocalEvidence(Protocol):
    def head_sha(self) -> str: ...

    def epic_spec(self, number: int) -> Spec | None:
        """Return the Epic's merged spec from the checkout, or ``None`` if absent.

        Raise ``VerificationBlocked`` for several matches or an unusable spec;
        ``read_epic_spec`` implements the rules.
        """
        ...

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


def parse_spec(path: str, text: str) -> Spec:
    """Read the goal and the ``**E<n>:**`` criteria of one spec; fail closed."""

    lines = text.splitlines()
    goal: list[str] = []
    criteria: list[SpecCriterion] = []
    section = ""
    current: tuple[str, str] | None = None
    verified_by = ""

    def flush() -> None:
        nonlocal current, verified_by
        if current is not None:
            criteria.append(SpecCriterion(current[0], current[1], verified_by))
        current, verified_by = None, ""

    for line in lines:
        if line.startswith("## "):
            flush()
            section = line[3:].strip()
            continue
        if section == "Goal":
            goal.append(line)
        elif section == "Acceptance Criteria":
            criterion = _EPIC_CRITERION.match(line)
            if criterion:
                flush()
                current = (criterion.group(1), criterion.group(2).strip())
                continue
            nested = _VERIFIED_BY.match(line)
            if nested and current is not None and not verified_by:
                verified_by = nested.group(1).strip()
    flush()

    goal_text = "\n".join(goal).strip()
    blockers = []
    if not goal_text:
        blockers.append(f"spec has no Goal: {path}")
    if not criteria:
        blockers.append(f"spec has no Epic criterion (**E<n>:**): {path}")
    if blockers:
        raise VerificationBlocked(*blockers)
    return Spec(path, goal_text, tuple(criteria))


def read_epic_spec(root: Path, number: int) -> Spec | None:
    """Find ``specs/<number>-*/spec.md`` under ``root``.

    ``None`` means no spec folder exists. Several folders, a missing or
    unreadable ``spec.md``, or an unusable spec raise ``VerificationBlocked``.
    """

    specs = root / "specs"
    folders = (
        sorted(p for p in specs.glob(f"{number}-*") if p.is_dir())
        if specs.is_dir()
        else []
    )
    if not folders:
        return None
    if len(folders) > 1:
        names = ", ".join(f"specs/{p.name}" for p in folders)
        raise VerificationBlocked(f"more than one spec folder for Epic #{number}: {names}")
    path = f"specs/{folders[0].name}/spec.md"
    try:
        text = (folders[0] / "spec.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationBlocked(f"spec cannot be read: {path} ({error})") from error
    return parse_spec(path, text)


def criteria_check_command(number: int, sha: str) -> str:
    return (
        f"scripts/criteria-check --epic {number} --repo {REPOSITORY} --ref {sha}"
    )


def criteria_check_result(
    number: int, sha: str, exit_code: int, output: str
) -> CheckResult:
    """Map the coverage script's exit code to a check; its text is the detail."""

    outcome = {0: "PASS", 1: "FAIL"}.get(exit_code, "BLOCKED")
    return CheckResult(criteria_check_command(number, sha), outcome, output.strip())


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


def ci_check_command(sha: str) -> str:
    return f"CI {CI_CHECK_NAME} on {sha}"


def ci_check_result(github: GitHubEvidence, sha: str) -> CheckResult:
    """Classify the CI ``pytest`` run for the exact ``sha``; fail closed.

    Only a successful, newest attempt for the same full SHA is ``PASS``. Runs
    for another SHA are ignored. Nothing here waits for or starts a run.
    """

    command = ci_check_command(sha)
    try:
        runs = tuple(github.ci_pytest_runs(sha))
    except Exception as error:
        return CheckResult(command, "BLOCKED", f"CI data cannot be read: {error}")
    matching = [run for run in runs if run.sha == sha]
    if not matching:
        others = sorted({run.sha for run in runs})
        suffix = f" (runs found only for {', '.join(others)})" if others else ""
        return CheckResult(command, "BLOCKED", f"no CI run for {sha}{suffix}")
    newest = max(matching, key=lambda run: (run.run_id, run.attempt))
    if newest.status != "completed":
        return CheckResult(
            command,
            "BLOCKED",
            f"{newest.url} is {newest.status}; run again when CI finishes",
        )
    if newest.conclusion == "success":
        return CheckResult(command, "PASS", newest.url)
    if newest.conclusion in {"failure", "timed_out"}:
        return CheckResult(command, "FAIL", newest.url)
    return CheckResult(
        command, "BLOCKED", f"{newest.url} concluded {newest.conclusion}"
    )


def _validate_evidence(
    declared_criteria: tuple[str, ...],
    rows: tuple[EvidenceRow, ...],
    checks: tuple[CheckResult, ...],
    main_sha: str,
    required_commands: frozenset[str] = frozenset(),
    po_checkable: frozenset[str] = frozenset(),
) -> None:
    blockers: list[str] = []
    if not declared_criteria:
        blockers.append("target has no declared criteria")
    row_criteria = tuple(row.criterion for row in rows)
    if row_criteria != declared_criteria:
        blockers.append("evidence rows do not exactly match declared criteria")
    for row in rows:
        if row.result == PO_CHECK:
            if row.criterion not in po_checkable:
                blockers.append(
                    f"{PO_CHECK} is not allowed for criterion: {row.criterion}"
                )
            elif not row.po_steps:
                blockers.append(f"{PO_CHECK} has no steps for the PO: {row.criterion}")
            if not row.evidence:
                blockers.append(
                    f"{PO_CHECK} has no evidence for its provable parts: {row.criterion}"
                )
        elif row.result != "PASS" or not row.evidence:
            blockers.append(f"criterion is not proven: {row.criterion}")
    check_commands = {check.command for check in checks}
    missing_commands = sorted(
        (REQUIRED_CHECK_COMMANDS | required_commands | {ci_check_command(main_sha)})
        - check_commands
    )
    for command in missing_commands:
        blockers.append(f"required check is missing: {command}")
    for check in checks:
        if check.outcome != "PASS":
            detail = f" ({check.detail})" if check.detail else ""
            blockers.append(f"check is {check.outcome}: {check.command}{detail}")
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
    if github.repository_name() != REPOSITORY:
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

    # An Epic with a merged spec takes its criteria from the spec, never from
    # its issue body; a Feature, or an Epic with no spec, keeps the issue body.
    spec = local.epic_spec(number) if target_type == "type:epic" else None
    if spec is not None and (not spec.goal.strip() or not spec.criteria):
        raise VerificationBlocked(f"spec has no Goal or Epic criterion: {spec.path}")
    declared = spec.declared_criteria if spec else target.declared_criteria
    required = (
        frozenset({criteria_check_command(number, main_sha)})
        if spec
        else frozenset()
    )
    po_checkable = (
        frozenset(c.label for c in spec.criteria if c.po_may_check)
        if spec
        else frozenset()
    )

    evidence = tuple(local.evidence_rows(target))
    checks = (*local.check_results(), ci_check_result(github, main_sha))
    _validate_evidence(declared, evidence, checks, main_sha, required, po_checkable)

    # Model the command's mandatory final refresh immediately before mutation.
    refreshed = github.issue(number)
    refreshed_main = github.main_sha()
    if refreshed.state != "OPEN" or _target_type(refreshed) != target_type:
        raise VerificationBlocked("target changed during verification")
    if spec is None and refreshed.declared_criteria != target.declared_criteria:
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
        spec=spec,
    )


def _matches_reply(comment: Comment, author: str, after: str, word: str) -> bool:
    return (
        comment.author == author
        and not comment.edited
        and comment.created_at > after
        and comment.body.strip().lower() == word
    )


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
    po_line = (
        (
            f"- PO hands-on checks: {', '.join(summary.po_check_criteria)} "
            "confirmed by the PO's reply",
        )
        if summary.po_check_criteria
        else ()
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
            *po_line,
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
    if target.number != number:
        raise VerificationBlocked("target issue number does not match")
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

    comments = tuple(github.comments(number))
    confirmations = tuple(
        comment
        for comment in comments
        if _matches_reply(comment, "LauraMardones", summary.created_at, "yes")
    )
    declines = tuple(
        comment
        for comment in comments
        if _matches_reply(comment, "LauraMardones", summary.created_at, "no")
    )
    if len(declines) == 1 and not confirmations:
        raise ClosureDeclined(declines[0])
    if len(confirmations) != 1 or declines:
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
    if refreshed.number != number:
        raise VerificationBlocked("target issue number changed before closure")
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
    refreshed_comments = tuple(github.comments(number))
    refreshed_confirmations = tuple(
        comment
        for comment in refreshed_comments
        if _matches_reply(comment, "LauraMardones", summary.created_at, "yes")
    )
    refreshed_declines = tuple(
        comment
        for comment in refreshed_comments
        if _matches_reply(comment, "LauraMardones", summary.created_at, "no")
    )
    if len(refreshed_declines) == 1 and not refreshed_confirmations:
        raise ClosureDeclined(refreshed_declines[0])
    if refreshed_confirmations != (confirmation,) or refreshed_declines:
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
