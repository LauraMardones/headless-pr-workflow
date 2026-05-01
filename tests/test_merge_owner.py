import json

from headless_pr_workflow import cli

from tests.github_scenarios import build_pr_context


def test_merge_owner_direct_owner_passes_with_explicit_evidence(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head123"))

    exit_code = cli.main(
        [
            "merge-owner",
            "123",
            "--repo",
            "owner/repo",
            "--session-id",
            "session-a",
            "--expected-owner",
            "session-a",
            "--expected-owner-sha",
            "head123",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "ownership status: direct owner" in output
    assert "hard gate passed: true" in output
    assert "run fresh pre-merge checks" in output


def test_merge_owner_different_owner_requires_takeover(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head123"))

    exit_code = cli.main(
        [
            "merge-owner",
            "123",
            "--repo",
            "owner/repo",
            "--session-id",
            "session-b",
            "--expected-owner",
            "session-a",
            "--json",
        ]
    )

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ownership_status"] == "takeover_required"
    assert output["hard_gate_passed"] is False
    assert "Takeover is required before merge" in output["blocking_reasons"][1]


def test_merge_owner_missing_current_session_is_unknown(monkeypatch, capsys):
    monkeypatch.delenv("HPW_SESSION_ID", raising=False)
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head123"))

    exit_code = cli.main(["merge-owner", "123", "--repo", "owner/repo", "--expected-owner", "session-a", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ownership_status"] == "unknown_owner"
    assert output["current_session"]["available"] is False
    assert "Current session identity is missing" in output["blocking_reasons"][0]


def test_merge_owner_missing_expected_owner_evidence_is_unknown(monkeypatch, capsys):
    monkeypatch.delenv("HPW_EXPECTED_MERGE_OWNER", raising=False)
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head123"))

    exit_code = cli.main(["merge-owner", "123", "--repo", "owner/repo", "--session-id", "session-a", "--json"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ownership_status"] == "unknown_owner"
    assert output["expected_owner_evidence"]["available"] is False
    assert "Expected merge owner evidence is missing" in output["blocking_reasons"][0]
    assert "human/operator decision" in output["blocking_reasons"][1]


def test_merge_owner_human_output_explains_takeover_path(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head123"))

    exit_code = cli.main(
        [
            "merge-owner",
            "123",
            "--repo",
            "owner/repo",
            "--session-id",
            "takeover-session",
            "--expected-owner",
            "original-session",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "ownership status: takeover required" in output
    assert "record takeover intent" in output
    assert "Do not merge yet" in output


def test_merge_owner_json_output_shape(monkeypatch, capsys):
    monkeypatch.setenv("HPW_SESSION_ID", "session-a")
    monkeypatch.setenv("HPW_EXPECTED_MERGE_OWNER", "session-a")
    monkeypatch.setenv("HPW_EXPECTED_MERGE_OWNER_SHA", "head123")
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="head123"))

    exit_code = cli.main(["merge-owner", "123", "--repo", "owner/repo", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "merge-owner"
    assert output["pr"]["number"] == 123
    assert output["current_head_sha"] == "head123"
    assert output["current_session"] == {
        "identity": "session-a",
        "source": "HPW_SESSION_ID",
        "available": True,
    }
    assert output["expected_owner_evidence"]["identity"] == "session-a"
    assert output["expected_owner_evidence"]["head_sha"] == "head123"
    assert output["ownership_status"] == "direct_owner"
    assert output["blocking_reasons"] == []
    assert output["hard_gate_passed"] is True


def test_merge_owner_not_allowed_when_owner_evidence_is_for_stale_head(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_pr_context", lambda target, repo=None: build_pr_context(head_ref_oid="new-head"))

    exit_code = cli.main(
        [
            "merge-owner",
            "123",
            "--repo",
            "owner/repo",
            "--session-id",
            "session-a",
            "--expected-owner",
            "session-a",
            "--expected-owner-sha",
            "old-head",
            "--json",
        ]
    )

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ownership_status"] == "not_allowed"
    assert "Expected owner evidence applies to head SHA old-head, but current head SHA is new-head." in output["blocking_reasons"]
