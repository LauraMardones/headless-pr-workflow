"""The standing catch-all Epic and Feature numbers (issue #550)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from headless_pr_workflow import catch_all
from headless_pr_workflow.dispatcher import cli
from tests.dispatcher_fakes import REPO_ROOT


def test_numbers_prints_the_constants_as_one_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["catch-all", "numbers"]) == 0

    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == {"epic": catch_all.CATCH_ALL_EPIC, "feature": catch_all.CATCH_ALL_FEATURE}


@pytest.mark.parametrize("broken", ["missing", "not-a-number"])
def test_numbers_exits_2_when_a_constant_cannot_be_read(
    broken: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if broken == "missing":
        monkeypatch.delattr(catch_all, "CATCH_ALL_FEATURE")
    else:
        monkeypatch.setattr(catch_all, "CATCH_ALL_EPIC", "530")

    assert cli.main(["catch-all", "numbers"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Error: cannot read the catch-all numbers")


def test_numbers_exits_2_when_the_module_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "headless_pr_workflow.catch_all", None)

    assert cli.main(["catch-all", "numbers"]) == 2
    assert "Error: cannot read the catch-all numbers" in capsys.readouterr().err


CONSUMERS = (
    "scripts/dispatcher-invoke.sh",
    "src/headless_pr_workflow/closure_verification.py",
    ".claude/commands/verify-closure.md",
)


def _consumer_files() -> list[Path]:
    files = [REPO_ROOT / name for name in CONSUMERS]
    files += sorted(p for p in (REPO_ROOT / "src/headless_pr_workflow/dispatcher").rglob("*") if p.suffix in {".py", ".sh"})
    return files


@pytest.mark.parametrize("number", [catch_all.CATCH_ALL_EPIC, catch_all.CATCH_ALL_FEATURE])
def test_the_numbers_are_literals_only_in_catch_all_py(number: int) -> None:
    import re

    pattern = re.compile(rf"(?<![0-9]){number}(?![0-9])")
    offenders = [str(p.relative_to(REPO_ROOT)) for p in _consumer_files() if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
