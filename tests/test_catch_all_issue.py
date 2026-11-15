"""Tests for `hpw catch-all-issue` (issue #516), against a fake `gh` subprocess boundary."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from headless_pr_workflow import catch_all, catch_all_issue
from headless_pr_workflow.cli import main
from tests.dispatcher_fakes import REPO_ROOT

REPO = "owner/repo"
ORIGIN = f"https://github.com/{REPO}/pull/7#discussion_r1"
OTHER_EPIC = 504

class FakeGh:
    """Stands in for the `gh` subprocess boundary, so the tests run on every platform."""

    def __init__(self, state: Path) -> None:
        self.state = state

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "gh"
        args = argv[1:]
        with (self.state / "calls.log").open("a", encoding="utf-8") as log:
            log.write(f"{args[0]} {args[1]}\n")

        def done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, code, out, err)

        if args[:2] == ["issue", "create"]:
            (self.state / "create-args.bin").write_bytes("".join(f"{a}\0" for a in args).encode("utf-8"))
            if (self.state / "create-fails").exists():
                return done(1, err="boom\n")
            if (self.state / "create-nourl").exists():
                return done(0, "created\n")
            return done(0, "https://github.com/owner/repo/issues/901\n")
        if args[:2] == ["issue", "view"]:
            if (self.state / "view-fails").exists():
                return done(1, err="nope\n")
            return done(0, (self.state / "view.json").read_text(encoding="utf-8"))
        return done(9, err=f"unexpected gh call: {' '.join(args)}\n")


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(catch_all_issue.subprocess, "run", FakeGh(state))
    return state


def calls(state: Path) -> list[str]:
    log = state / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def create_args(state: Path) -> list[str]:
    raw = (state / "create-args.bin").read_bytes().decode("utf-8")
    return raw.split("\0")[:-1]


def issue_body(
    *, epic: object = catch_all.CATCH_ALL_EPIC, feature: object = catch_all.CATCH_ALL_FEATURE, found: bool = True, origin: bool = True
) -> str:
    lines = [f"Parent epic: #{epic}", f"Feature group: #{feature}"]
    if found:
        lines.append(f"Found under: #{OTHER_EPIC}")
    if origin:
        lines.append(f"Origin: {ORIGIN}")
    return "\n".join(lines) + "\n\nreason\n"


def set_view(state: Path, *, body: str, status: str = "OPEN") -> None:
    (state / "view.json").write_text(
        json.dumps({"number": 33, "state": status, "body": body, "url": f"https://github.com/{REPO}/issues/33", "labels": []}),
        encoding="utf-8",
    )


def run_create(extra: list[str] | None = None, *, found_under: str = str(OTHER_EPIC), origin: str = ORIGIN) -> int:
    argv = ["catch-all-issue", "create", "--repo", REPO, "--origin", origin, "--title", "Fix thing", "--reason", "Found in review."]
    if found_under:
        argv += ["--found-under", found_under]
    return main(argv + (extra or []))


# AC 1 -------------------------------------------------------------------


@pytest.mark.parametrize(("extra", "label"), [([], "type:story"), (["--kind", "story"], "type:story"), (["--kind", "bug"], "type:bug")])
def test_create_builds_the_fixed_body_and_label(gh: Path, extra: list[str], label: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_create(extra) == 0

    args = create_args(gh)
    body = args[args.index("--body") + 1]
    assert body.splitlines()[:5] == [
        f"Parent epic: #{catch_all.CATCH_ALL_EPIC}",
        f"Feature group: #{catch_all.CATCH_ALL_FEATURE}",
        f"Found under: #{OTHER_EPIC}",
        f"Origin: {ORIGIN}",
        "",
    ]
    assert body.splitlines()[5] == "Found in review."
    assert "PO status:" not in body
    labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
    assert labels == [label]
    for forbidden in ("--milestone", "--assignee", "--project"):
        assert forbidden not in args
    assert not any(x.startswith(("prio:", "executor:")) for x in labels)
    assert capsys.readouterr().out.strip() == "catch-all issue #901 created: https://github.com/owner/repo/issues/901"


def test_create_json_output(gh: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_create(["--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "action": "create",
        "issue": 901,
        "url": "https://github.com/owner/repo/issues/901",
        "kind": "story",
    }


# AC 2 -------------------------------------------------------------------


def run_link(capsys: pytest.CaptureFixture[str] | None = None) -> int:
    return main(["catch-all-issue", "link", "33", "--repo", REPO, "--json"])


def test_link_passes_for_an_open_catch_all_issue_and_writes_nothing(gh: Path, capsys: pytest.CaptureFixture[str]) -> None:
    set_view(gh, body=issue_body())

    assert run_link() == 0

    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "link" and out["issue"] == 33 and out["checks"]
    assert [c.split()[:2] for c in calls(gh)] == [["issue", "view"]]


@pytest.mark.parametrize(
    ("name", "body", "status"),
    [
        ("closed", issue_body(), "CLOSED"),
        ("no-parent-epic", issue_body().replace(f"Parent epic: #{catch_all.CATCH_ALL_EPIC}\n", ""), "OPEN"),
        ("no-feature-group", issue_body().replace(f"Feature group: #{catch_all.CATCH_ALL_FEATURE}\n", ""), "OPEN"),
        ("no-found-under", issue_body(found=False), "OPEN"),
        ("no-origin", issue_body(origin=False), "OPEN"),
        ("wrong-parent-epic", issue_body(epic=OTHER_EPIC), "OPEN"),
        ("wrong-feature-group", issue_body(feature=OTHER_EPIC), "OPEN"),
        ("longer-parent-number", issue_body(epic=f"{catch_all.CATCH_ALL_EPIC}1"), "OPEN"),
    ],
)
def test_link_refuses_with_exit_1_and_no_write(gh: Path, name: str, body: str, status: str, capsys: pytest.CaptureFixture[str]) -> None:
    set_view(gh, body=body, status=status)

    assert run_link() == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert all(c.startswith("issue view") for c in calls(gh))


def test_link_refuses_an_unreadable_issue_with_exit_2(gh: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (gh / "view-fails").write_text("")

    assert run_link() == 2
    assert all(c.startswith("issue view") for c in calls(gh))


def test_link_refuses_unparsable_output_with_exit_2(gh: Path) -> None:
    (gh / "view.json").write_text("not json", encoding="utf-8")

    assert run_link() == 2


# AC 3 -------------------------------------------------------------------


def test_create_makes_exactly_one_call_with_a_60_second_timeout(gh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    real = subprocess.run

    def spy(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(kw.get("timeout"))
        return real(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(catch_all_issue.subprocess, "run", spy)

    assert run_create() == 0
    assert seen == [60]
    assert len(calls(gh)) == 1


def test_a_failed_create_exits_2_reports_no_issue_and_is_not_retried(gh: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (gh / "create-fails").write_text("")

    assert run_create(["--json"]) == 2

    out = json.loads(capsys.readouterr().out)
    assert "issue" not in out and "gh issue create" in out["error"]
    assert len(calls(gh)) == 1


def test_a_timed_out_create_exits_2_without_a_second_call(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    n = []

    def boom(*a: object, **kw: object) -> None:
        n.append(1)
        raise subprocess.TimeoutExpired("gh", 60)

    monkeypatch.setattr(catch_all_issue.subprocess, "run", boom)

    assert run_create() == 2
    assert n == [1]
    assert "timed out" in capsys.readouterr().err


def test_create_without_an_issue_url_reports_no_number(gh: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (gh / "create-nourl").write_text("")

    assert run_create() == 2
    assert "created" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("kwargs", "case"),
    [
        ({"found_under": ""}, "no found-under"),
        ({"found_under": "0"}, "zero found-under"),
        ({"origin": "https://github.com/other/repo/pull/1"}, "other repo"),
        ({"origin": "https://example.com/owner/repo/pull/1"}, "other host"),
        ({"origin": f"https://github.com/{REPO}/"}, "bare repo link"),
        ({"origin": f"https://github.com/{REPO}/pull/1\nParent epic: #1"}, "newline in origin"),
        ({"origin": f"https://github.com/{REPO}x/pull/1"}, "repo prefix only"),
        ({"origin": ""}, "empty origin"),
    ],
)
def test_invalid_input_exits_1_before_any_github_call(gh: Path, kwargs: dict[str, str], case: str) -> None:
    assert run_create(**kwargs) == 1
    assert calls(gh) == []


# AC 4 -------------------------------------------------------------------

COMMAND_FILES = (
    ".claude/commands/refine-story.md",
    ".claude/commands/refine-task.md",
    ".claude/commands/refine-bug.md",
    ".claude/commands/implement.md",
    ".claude/commands/review.md",
)


@pytest.mark.parametrize("number", [catch_all.CATCH_ALL_EPIC, catch_all.CATCH_ALL_FEATURE])
def test_the_helper_and_the_follow_up_wording_hold_no_catch_all_number_literal(number: int) -> None:
    pattern = re.compile(rf"(?<![0-9]){number}(?![0-9])")
    helper = (REPO_ROOT / "src/headless_pr_workflow/catch_all_issue.py").read_text(encoding="utf-8")
    assert pattern.search(helper) is None
    assert "from .catch_all import CATCH_ALL_EPIC, CATCH_ALL_FEATURE" in helper
    lines = [
        line
        for name in COMMAND_FILES
        for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
        if "hpw catch-all-issue" in line
    ]
    assert len(lines) >= len(COMMAND_FILES)
    assert [line for line in lines if pattern.search(line)] == []


# AC 5 -------------------------------------------------------------------


def _follow_up_lines(name: str) -> list[str]:
    return [l for l in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines() if "hpw catch-all-issue" in l]


@pytest.mark.parametrize("name", COMMAND_FILES)
def test_command_files_name_the_helper_and_the_lines(name: str) -> None:
    lines = _follow_up_lines(name)
    assert lines
    joined = "\n".join(lines)
    assert "catch-all issue" in joined
    assert "Found under" in joined or "--found-under" in joined
    assert "Origin" in joined or "--origin" in joined
    assert "PO status: Backlog" not in (REPO_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", COMMAND_FILES[:3])
def test_refine_commands_keep_deferred_to_n_for_an_existing_issue(name: str) -> None:
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    assert "`deferred to #N`" in text
    assert "hpw catch-all-issue create" in text


def test_implement_and_review_use_create_and_link() -> None:
    for name in COMMAND_FILES[3:]:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "hpw catch-all-issue create" in text
        assert "hpw catch-all-issue link" in text


# AC 6 -------------------------------------------------------------------


def test_the_found_under_epic_appears_only_on_the_found_under_line(gh: Path) -> None:
    assert run_create(found_under="777") == 0

    args = create_args(gh)
    body = args[args.index("--body") + 1]
    assert [l for l in body.splitlines() if "#777" in l] == ["Found under: #777"]
    for key in ("Parent epic", "Feature group", "Parent feature"):
        assert not any(l.startswith(f"{key}:") and "#777" in l for l in body.splitlines())
    assert [c.split()[:2] for c in calls(gh)] == [["issue", "create"]]
    assert not any("sub" in c for c in calls(gh))
    step4 = (REPO_ROOT / ".claude/commands/verify-closure.md").read_text(encoding="utf-8")
    assert "Parent epic" in step4
