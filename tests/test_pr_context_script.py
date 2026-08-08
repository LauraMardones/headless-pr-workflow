from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "pr-context"


def load_script():
    loader = importlib.machinery.SourceFileLoader("pr_context_script", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def script():
    return load_script()


@pytest.fixture
def normal_payload():
    return {
        "number": 7,
        "title": "Useful title\nFAKE FIELD",
        "body": "BODY SECRET\nfixes #81\nCloses #82",
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
        "files": [{"path": "src/main.py"}, {"path": "unsafe\nPATH SECRET"}],
        "comments": [
            {"createdAt": "2025-01-01T00:00:00Z", "body": "COMMENT SECRET"},
            {
                "createdAt": "2025-01-02T00:00:00Z",
                "body": "prefix\n## Session Summary\nCommand: review\nBlockers: none\n\nCOMMENT TAIL SECRET",
            },
        ],
        "statusCheckRollup": [
            {"name": "CHECK SECRET 1", "status": "COMPLETED", "conclusion": "SUCCESS", "detailsUrl": "URL SECRET"},
            {"name": "CHECK SECRET 2", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"name": "CHECK SECRET 3", "status": "IN_PROGRESS", "conclusion": None},
            {"name": "CHECK SECRET 4", "status": "COMPLETED", "conclusion": "MYSTERY"},
        ],
    }


def test_render_normal_payload_is_bounded(script, normal_payload):
    output = script.render(normal_payload, 2)
    assert output == (
        f"PR: #7 Useful title FAKE FIELD\nLinked issue: #81\nHead SHA: {'a' * 40}\nBase SHA: {'b' * 40}\n"
        "Changed files (2):\n- src/main.py\n- unsafe PATH SECRET\nLatest Session Summary:\n"
        "## Session Summary\nCommand: review\nBlockers: none\nUnresolved review threads: 2\n"
        "Checks: pass=1 fail=1 pending=1 other=1\n"
    )
    for secret in ("BODY SECRET", "COMMENT SECRET", "COMMENT TAIL SECRET", "CHECK SECRET", "URL SECRET"):
        assert secret not in output


def test_missing_link_and_summary_and_zero_files(script, normal_payload):
    normal_payload.update(body="no reference", comments=[], files=[], statusCheckRollup=[])
    output = script.render(normal_payload, 0)
    assert "Linked issue: none\n" in output
    assert "Changed files (0):\n- none\n" in output
    assert "Latest Session Summary:\nnone\n" in output
    assert output.endswith("Checks: pass=0 fail=0 pending=0 other=0\n")


def test_latest_summary_uses_creation_time_not_list_order(script, normal_payload):
    normal_payload["comments"] = [
        {"createdAt": "2025-02-02T00:00:00Z", "body": "## Session Summary\nCommand: newest"},
        {"createdAt": "2025-01-01T00:00:00Z", "body": "## Session Summary\nCommand: older"},
    ]
    output = script.render(normal_payload, 0)
    assert "Command: newest" in output
    assert "Command: older" not in output


def test_resolved_and_unresolved_threads_across_pages(script, monkeypatch):
    responses = iter([
        {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"isResolved": False}, {"isResolved": True}],
            "pageInfo": {"hasNextPage": True, "endCursor": "next"},
        }}}}},
        {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"isResolved": False}], "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}}}},
    ])
    commands = []

    def fake_run(arguments):
        commands.append(arguments)
        return next(responses)

    monkeypatch.setattr(script, "run_gh", fake_run)
    assert script.fetch_unresolved_threads(7, "owner/repo") == 2
    assert "cursor=next" in commands[1]


def test_repo_is_passed_to_pr_command_and_graphql_is_scoped(script, monkeypatch, normal_payload):
    commands = []

    def fake_run(arguments):
        commands.append(arguments)
        if arguments[:2] == ["pr", "view"]:
            return normal_payload
        return {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}}}}

    monkeypatch.setattr(script, "run_gh", fake_run)
    script.fetch_pr(7, "owner/repo")
    script.fetch_unresolved_threads(7, "owner/repo")
    assert "--repo" in commands[0] and commands[0][commands[0].index("--repo") + 1] == "owner/repo"
    assert "owner=owner" in commands[1] and "name=repo" in commands[1]


def test_dry_run_is_exact_and_never_calls_subprocess(script, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(script.subprocess, "run", forbidden)
    assert script.main(["--pr", "9", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert captured.out == script.DRY_RUN_OUTPUT
    assert captured.out.endswith("\n")
    assert captured.err == ""


@pytest.mark.parametrize("arguments", [[], ["--pr", "0"], ["--pr", "-1"], ["--pr", "abc"], ["--pr", "1", "--repo", "invalid"]])
def test_invalid_arguments_exit_two(arguments):
    completed = subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert completed.stdout == ""


def test_github_failure_is_concise_and_does_not_leak(script, monkeypatch, capsys):
    completed = subprocess.CompletedProcess([], 1, stdout="RAW RESPONSE SECRET", stderr="GH ERROR SECRET")
    monkeypatch.setattr(script.subprocess, "run", lambda *args, **kwargs: completed)
    assert script.main(["--pr", "7"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: GitHub CLI request failed\n"
    assert "SECRET" not in captured.err


@pytest.mark.parametrize("field", ["number", "title", "body", "headRefOid", "baseRefOid", "files", "comments", "statusCheckRollup"])
def test_incomplete_payload_fails_closed(script, normal_payload, field):
    del normal_payload[field]
    with pytest.raises(script.ContextError, match="incomplete"):
        script.render(normal_payload, 0)


def test_script_is_executable():
    if sys.platform != "win32":
        assert SCRIPT.stat().st_mode & 0o111
