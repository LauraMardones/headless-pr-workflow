"""Tests for hpw next-action command."""

from __future__ import annotations

import json
import subprocess

import pytest

import headless_pr_workflow.next_action as next_action_module
from headless_pr_workflow.next_action import (
    build_workflow_status_command,
    fetch_workflow_status,
    summarize_next_action,
    summarize_next_action_from_subprocess,
)


def workflow_status_payload(
    posture: str,
    *,
    reasons: tuple[str, ...] = ("posture reason",),
    summary: str = "posture summary",
    is_draft: bool = False,
    approval_status: str = "current",
    re_review_needed: bool = False,
    check_buckets: dict[str, list[str]] | None = None,
    unresolved_blocking: int = 0,
    mergeable: str = "MERGEABLE",
    state: str = "OPEN",
    warnings: tuple[str, ...] = (),
) -> dict:
    return {
        "command": "workflow-status",
        "ok": True,
        "repository": "owner/repo",
        "pr": {
            "number": 123,
            "state": state,
            "is_draft": is_draft,
        },
        "workflow_posture": {
            "status": posture,
            "summary": summary,
            "reasons": list(reasons),
            "source_commands": ["approval-check", "ci-summary"],
        },
        "approval": {
            "approval_status": approval_status,
            "blocking_reasons": ["formal approval is stale for the current PR head SHA"]
            if approval_status == "stale"
            else [],
        },
        "re_review": {
            "re_review_needed": re_review_needed,
        },
        "checks": {
            "check_buckets": check_buckets
            or {
                "passing": [],
                "failing": [],
                "pending": [],
                "missing": [],
                "unknown": [],
            }
        },
        "review_threads": {
            "thread_counts": {
                "unresolved_blocking": unresolved_blocking,
            }
        },
        "merge_readiness": {
            "mergeable": mergeable,
            "blocking_reasons": [],
        },
        "warnings": list(warnings),
    }


@pytest.mark.parametrize(
    ("posture", "action"),
    (
        ("implementation_required", "implement"),
        ("review_required", "review"),
        ("merge_validation_required", "merge_validate"),
        ("merged", "post-merge-sync"),
        ("waiting", "wait"),
        ("human_decision_required", "escalate"),
    ),
)
def test_posture_maps_to_single_bounded_action(posture, action):
    result = summarize_next_action(workflow_status_payload(posture))

    assert result.ok is True
    assert result.action == action
    assert result.source_posture == posture
    assert result.source_commands == ("workflow-status", "approval-check", "ci-summary")


def test_clean_implementation_required_recommends_implement():
    result = summarize_next_action(workflow_status_payload("implementation_required"))

    assert result.action == "implement"
    assert "implementation_required" in result.rationale


def test_review_required_no_current_approval_recommends_review():
    result = summarize_next_action(
        workflow_status_payload(
            "review_required",
            reasons=("No current-head approval exists.",),
        )
    )

    assert result.action == "review"
    assert result.blocking_reasons == ("No current-head approval exists.",)


def test_merge_validation_required_recommends_merge_validate():
    result = summarize_next_action(workflow_status_payload("merge_validation_required"))

    assert result.action == "merge_validate"


@pytest.mark.parametrize(
    "bucket",
    ("failing", "pending"),
)
def test_failing_or_pending_ci_recommends_wait(bucket):
    result = summarize_next_action(
        workflow_status_payload(
            "implementation_required",
            check_buckets={
                "passing": [],
                "failing": ["unit"] if bucket == "failing" else [],
                "pending": ["lint"] if bucket == "pending" else [],
                "missing": [],
                "unknown": [],
            },
        )
    )

    assert result.action == "wait"
    assert any("status check" in reason.lower() for reason in result.blocking_reasons)


def test_draft_pr_recommends_wait():
    result = summarize_next_action(
        workflow_status_payload(
            "implementation_required",
            is_draft=True,
        )
    )

    assert result.action == "wait"
    assert any("draft" in reason.lower() for reason in result.blocking_reasons)


def test_stale_approval_recommends_review():
    result = summarize_next_action(
        workflow_status_payload(
            "review_required",
            approval_status="stale",
            re_review_needed=True,
        )
    )

    assert result.action == "review"
    assert any("stale" in reason.lower() for reason in result.blocking_reasons)


def test_unresolved_review_thread_blockers_recommend_wait():
    result = summarize_next_action(
        workflow_status_payload(
            "implementation_required",
            unresolved_blocking=2,
        )
    )

    assert result.action == "wait"
    assert any("review-thread" in reason.lower() for reason in result.blocking_reasons)


def test_unknown_mergeability_recommends_escalate():
    result = summarize_next_action(
        workflow_status_payload(
            "human_decision_required",
            mergeable="UNKNOWN",
        )
    )

    assert result.action == "escalate"
    assert any("UNKNOWN" in reason for reason in result.blocking_reasons)


def test_merged_pr_recommends_post_merge_sync_before_unknown_mergeability_guard():
    result = summarize_next_action(
        workflow_status_payload(
            "human_decision_required",
            reasons=("PR mergeable state is UNKNOWN.",),
            mergeable="UNKNOWN",
            state="MERGED",
        )
    )
    output = result.to_dict()

    assert result.ok is True
    assert result.action == "post-merge-sync"
    assert result.rationale == "PR is merged. Run post-merge-sync to update local state."
    assert result.source_posture == "human_decision_required"
    assert result.blocking_reasons == ()
    assert output["ok"] is True
    assert output["action"] == "post-merge-sync"
    assert "merged" in output["rationale"]
    assert output["blocking_reasons"] == []
    assert output["errors"] is None


def test_unknown_posture_recommends_escalate():
    result = summarize_next_action(workflow_status_payload("new_future_posture"))

    assert result.ok is True
    assert result.action == "escalate"
    assert result.source_posture == "new_future_posture"


def test_missing_workflow_posture_is_structured_error():
    result = summarize_next_action({"repository": "owner/repo", "pr": {"number": 123}})

    assert result.ok is False
    assert result.errors["type"] == "workflow-status-malformed"


def test_subprocess_failure_returns_nonzero_structured_result(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")

    monkeypatch.setattr(next_action_module.subprocess, "run", fake_run)

    result = summarize_next_action_from_subprocess("123", repo="owner/repo", path="/repo")

    assert result.ok is False
    assert result.errors["type"] == "workflow-status-failed"
    assert result.errors["details"]["returncode"] == 1


def test_subprocess_parse_failure_returns_structured_result(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

    monkeypatch.setattr(next_action_module.subprocess, "run", fake_run)

    result = summarize_next_action_from_subprocess("123", repo="owner/repo", path="/repo")

    assert result.ok is False
    assert result.errors["type"] == "workflow-status-parse-failed"


def test_fetch_workflow_status_consumes_json_stdout(monkeypatch):
    payload = workflow_status_payload("waiting")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(next_action_module.subprocess, "run", fake_run)

    assert fetch_workflow_status("123", repo="owner/repo", path="/repo") == payload


def test_workflow_status_command_passes_repo_json_and_path():
    command = build_workflow_status_command("123", repo="owner/repo", path="/repo")

    assert command[:4] == (
        next_action_module.sys.executable,
        "-m",
        "headless_pr_workflow.cli",
        "workflow-status",
    )
    assert "--repo" in command
    assert "owner/repo" in command
    assert "--json" in command
    assert command[-2:] == ("--path", "/repo")


def test_json_contract_contains_required_fields():
    result = summarize_next_action(workflow_status_payload("waiting", warnings=("ci pending",)))
    output = result.to_dict()

    for key in (
        "command",
        "ok",
        "repository",
        "pr",
        "action",
        "rationale",
        "source_posture",
        "blocking_reasons",
        "source_commands",
        "warnings",
        "errors",
    ):
        assert key in output
    assert output["command"] == "next-action"
    assert output["warnings"] == ["ci pending"]
