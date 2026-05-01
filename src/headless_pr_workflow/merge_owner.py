"""Merge ownership gate evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .github import PullRequestContext


DIRECT_OWNER = "direct_owner"
TAKEOVER_REQUIRED = "takeover_required"
UNKNOWN_OWNER = "unknown_owner"
NOT_ALLOWED = "not_allowed"

OWNERSHIP_STATUSES = (DIRECT_OWNER, TAKEOVER_REQUIRED, UNKNOWN_OWNER, NOT_ALLOWED)


@dataclass(frozen=True)
class IdentityEvidence:
    identity: str | None
    source: str

    @property
    def available(self) -> bool:
        return bool(self.identity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "source": self.source,
            "available": self.available,
        }


@dataclass(frozen=True)
class ExpectedOwnerEvidence(IdentityEvidence):
    head_sha: str | None = None
    head_sha_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["head_sha"] = self.head_sha
        payload["head_sha_source"] = self.head_sha_source
        return payload


@dataclass(frozen=True)
class MergeOwnerSummary:
    number: int
    title: str
    url: str
    state: str
    current_head_sha: str
    current_session: IdentityEvidence
    expected_owner: ExpectedOwnerEvidence
    ownership_status: str
    blocking_reasons: tuple[str, ...]
    next_safe_action: str
    hard_gate_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": "merge-owner",
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "state": self.state,
            "current_head_sha": self.current_head_sha,
            "pr": {
                "number": self.number,
                "title": self.title,
                "url": self.url,
                "state": self.state,
                "current_head_sha": self.current_head_sha,
            },
            "current_session": self.current_session.to_dict(),
            "expected_owner_evidence": self.expected_owner.to_dict(),
            "ownership_status": self.ownership_status,
            "blocking_reasons": list(self.blocking_reasons),
            "next_safe_action": self.next_safe_action,
            "hard_gate_passed": self.hard_gate_passed,
        }


def summarize_merge_owner(
    context: PullRequestContext,
    *,
    current_session_id: str | None,
    current_session_source: str,
    expected_owner_id: str | None,
    expected_owner_source: str,
    expected_owner_head_sha: str | None = None,
    expected_owner_head_sha_source: str | None = None,
) -> MergeOwnerSummary:
    """Determine whether the current session satisfies the merge-owner gate."""

    current_session = IdentityEvidence(
        identity=_normalize_identity(current_session_id),
        source=current_session_source,
    )
    expected_owner = ExpectedOwnerEvidence(
        identity=_normalize_identity(expected_owner_id),
        source=expected_owner_source,
        head_sha=_normalize_identity(expected_owner_head_sha),
        head_sha_source=expected_owner_head_sha_source,
    )

    blocking_reasons = _not_allowed_reasons(context, expected_owner)
    if blocking_reasons:
        status = NOT_ALLOWED
    elif not current_session.available:
        status = UNKNOWN_OWNER
        blocking_reasons = (
            "Current session identity is missing; pass --session-id or set HPW_SESSION_ID.",
        )
    elif not expected_owner.available:
        status = UNKNOWN_OWNER
        blocking_reasons = (
            "Expected merge owner evidence is missing; pass --expected-owner or set HPW_EXPECTED_MERGE_OWNER.",
            "If the original owner is unavailable, record takeover intent and get a human/operator decision before merge.",
        )
    elif current_session.identity == expected_owner.identity:
        status = DIRECT_OWNER
        blocking_reasons = ()
    else:
        status = TAKEOVER_REQUIRED
        blocking_reasons = (
            f"Current session {current_session.identity} does not match expected merge owner {expected_owner.identity}.",
            "Takeover is required before merge; record takeover intent, confirm the original owner is unavailable or has handed off, and rerun pre-merge checks.",
        )

    return MergeOwnerSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        state=context.state,
        current_head_sha=context.head_ref_oid,
        current_session=current_session,
        expected_owner=expected_owner,
        ownership_status=status,
        blocking_reasons=blocking_reasons,
        next_safe_action=_next_safe_action(status),
        hard_gate_passed=status == DIRECT_OWNER,
    )


def _not_allowed_reasons(context: PullRequestContext, expected_owner: ExpectedOwnerEvidence) -> tuple[str, ...]:
    reasons: list[str] = []
    if context.state != "OPEN":
        reasons.append(f"PR is not open (state={context.state or 'unknown'}).")
    if not context.head_ref_oid:
        reasons.append("Current PR head SHA is unknown.")
    if expected_owner.head_sha and context.head_ref_oid and expected_owner.head_sha != context.head_ref_oid:
        reasons.append(
            f"Expected owner evidence applies to head SHA {expected_owner.head_sha}, "
            f"but current head SHA is {context.head_ref_oid}."
        )
    return tuple(reasons)


def _next_safe_action(status: str) -> str:
    if status == DIRECT_OWNER:
        return "Ownership gate passed; run fresh pre-merge checks before attempting any merge."
    if status == TAKEOVER_REQUIRED:
        return "Do not merge yet; follow takeover rules, record the handoff decision, then rerun merge readiness checks."
    if status == UNKNOWN_OWNER:
        return "Do not merge; provide explicit owner evidence or route to takeover/human decision."
    return "Do not merge; resolve the blocking ownership facts and refresh GitHub state."


def _normalize_identity(identity: str | None) -> str | None:
    if identity is None:
        return None
    normalized = identity.strip()
    return normalized or None
