from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from headless_pr_workflow.github import GHCommandError, RequiredStatusChecks
from tests.github_scenarios import scenario_current_approval, scenario_stale_approval


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "merge-gate-summary"


@pytest.fixture
def module():
    loader = SourceFileLoader("merge_gate_summary", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def passing_context():
    return replace(
        scenario_current_approval(head_sha="abcdef1234567890"),
        state="OPEN",
        is_draft=False,
        base_ref_name="main",
        mergeable="MERGEABLE",
    )


def arrange_live(monkeypatch, module, *, context=None, threads=0, policy=("pass(present_non_required)", "present_non_required")):
    context = context or passing_context()
    monkeypatch.setattr(module, "fetch_pr_context", lambda target, repo=None: context)
    monkeypatch.setattr(module, "fetch_repo_default_branch", lambda repo=None: "main")
    monkeypatch.setattr(module, "_policy_for_branch", lambda branch: policy)
    monkeypatch.setattr(
        module,
        "fetch_required_status_check_context",
        lambda repo, branch: RequiredStatusChecks(names=(), status="unavailable"),
    )
    monkeypatch.setattr(module, "fetch_review_threads_for_context", lambda context, repo=None: ())
    monkeypatch.setattr(
        module,
        "summarize_review_threads",
        lambda context, raw: SimpleNamespace(unresolved_blocking_threads=tuple(range(threads))),
    )
    return context


def test_dry_run_exact_output_and_no_gh_call(tmp_path):
    fake_gh = tmp_path / ("gh.bat" if os.name == "nt" else "gh")
    fake_gh.write_text("exit 99\n", encoding="utf-8")
    if os.name != "nt":
        fake_gh.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--pr", "1", "--dry-run"],
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == (
        "Merge gate: policy=pass(present_non_required), checks=absent-ok, "
        "approval=solo-maintainer, threads=none, mergeable=yes, head=b607aa9\n"
    )
    assert result.stderr == ""


@pytest.mark.parametrize("value", ["0", "-1", "1.0", "abc", "01"])
def test_pr_must_be_positive_integer(value):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--pr", value, "--dry-run"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "positive integer" in result.stderr


def test_repo_must_use_owner_repo_format():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--pr", "1", "--repo", "invalid", "--dry-run"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "owner/repo" in result.stderr


def test_all_pass_has_fixed_field_order(monkeypatch, module, capsys):
    arrange_live(monkeypatch, module)
    assert module.main(["--pr", "12", "--repo", "owner/repo"]) == 0
    assert capsys.readouterr().out == (
        "Merge gate: policy=pass(present_non_required), checks=absent-ok, "
        "approval=formal, threads=none, mergeable=yes, head=abcdef1\n"
    )


def test_unresolved_threads_block(monkeypatch, module, capsys):
    arrange_live(monkeypatch, module, threads=2)
    assert module.main(["--pr", "12"]) == 1
    assert "threads=fail(2)" in capsys.readouterr().out


def test_stale_approval_blocks(monkeypatch, module, capsys):
    context = replace(
        scenario_stale_approval(head_sha="abcdef1234", approval_sha="1234567dead"),
        state="OPEN",
        is_draft=False,
        base_ref_name="main",
        mergeable="MERGEABLE",
    )
    arrange_live(monkeypatch, module, context=context)
    assert module.main(["--pr", "12"]) == 1
    assert "approval=fail(stale)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"state": "CLOSED"}, "fail(not-open)"),
        ({"is_draft": True}, "fail(draft)"),
        ({"base_ref_name": "develop"}, "fail(wrong-base)"),
        ({"mergeable": "CONFLICTING"}, "fail(conflicting)"),
        ({"mergeable": "UNKNOWN"}, "warn(unknown)"),
    ],
)
def test_mergeability_failures(monkeypatch, module, capsys, change, expected):
    arrange_live(monkeypatch, module, context=replace(passing_context(), **change))
    assert module.main(["--pr", "12"]) == 1
    assert f"mergeable={expected}" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("buckets", "expected"),
    [
        ({"failure"}, "fail(failing)"),
        ({"pending"}, "fail(pending)"),
        ({"unknown"}, "warn(unknown)"),
    ],
)
def test_check_failures(module, buckets, expected):
    context = SimpleNamespace(status_checks=tuple(SimpleNamespace(bucket=b, name=b) for b in buckets))
    required = RequiredStatusChecks(names=(), status="available")
    assert module._checks_value(context, required, "present_non_required") == expected


def test_missing_required_check_blocks(module):
    context = SimpleNamespace(status_checks=())
    required = RequiredStatusChecks(names=("unit",), status="available")
    assert module._checks_value(context, required, "present_non_required") == "fail(missing-required)"


def test_malformed_policy_blocks(module, tmp_path):
    policy = tmp_path / "docs" / "required-check-policy.json"
    policy.parent.mkdir()
    policy.write_text("not json", encoding="utf-8")
    assert module._policy_for_branch("main", repo_root=tmp_path) == ("fail(malformed-policy)", None)


def test_policy_rejects_workflow_mismatch(module, tmp_path):
    policy = tmp_path / "docs" / "required-check-policy.json"
    policy.parent.mkdir()
    policy.write_text(
        '{"schema":"headless-pr-workflow.required-check-policy.v1",'
        '"branches":{"main":{"required_status_checks":"absent","ci_workflows":"absent"}}}',
        encoding="utf-8",
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: CI", encoding="utf-8")
    assert module._policy_for_branch("main", repo_root=tmp_path) == ("fail(workflows-present)", None)


def test_thread_fetch_failure_is_non_passing(monkeypatch, module, capsys):
    arrange_live(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "fetch_review_threads_for_context",
        lambda context, repo=None: (_ for _ in ()).throw(GHCommandError(["gh"], 1, "denied")),
    )
    assert module.main(["--pr", "12"]) == 1
    assert "threads=warn(unavailable)" in capsys.readouterr().out


def test_pr_fetch_failure_is_usage_error(monkeypatch, module, capsys):
    monkeypatch.setattr(
        module,
        "fetch_pr_context",
        lambda target, repo=None: (_ for _ in ()).throw(GHCommandError(["gh"], 1, "not found")),
    )
    assert module.main(["--pr", "404"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "GitHub CLI failed" in captured.err


@pytest.mark.skipif(os.name == "nt", reason="Windows does not use POSIX executable mode")
def test_script_is_executable():
    assert SCRIPT.stat().st_mode & 0o111 == 0o111
