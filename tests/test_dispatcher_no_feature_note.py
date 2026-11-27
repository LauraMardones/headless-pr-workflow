"""The no-feature note (issue #616, Feature #460 F2.11): ``hpw dispatcher no-feature-note``.

Runs against the in-memory fake gateway of ``test_dispatcher_write_block``: no network, no ``gh``.
"""

from __future__ import annotations

import io
import json

import pytest

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.gateway import GatewayError
from headless_pr_workflow.dispatcher.no_feature_note import MARKER, note_text
from tests.test_dispatcher_write_block import BOT, FakeGateway, gw, settings  # noqa: F401 - fixtures

REPO = "acme/example"


def decision(*entries: tuple[int, str]) -> str:
    return json.dumps({"result": "idle", "action": None,
                       "skipped": [{"issue": n, "reason": r} for n, r in entries]})


def run_note(gw, settings, capsys, monkeypatch, output: str, *extra: str):
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(output.encode())))
    code = cli.main(["no-feature-note", "--repo", REPO, *extra], gateway_factory=lambda repo: gw, settings=settings)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_marker_has_the_documented_format():
    assert MARKER == "<!-- hpw:dispatcher action=no-feature-note -->"
    assert note_text().startswith("## Not scheduled: no Feature group")


def test_a_no_feature_story_gets_one_note_with_reason_fix_and_marker(gw, settings, capsys, monkeypatch):
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json")
    assert code == 0
    assert json.loads(out) == {"issue": 7, "result": "written"}
    assert gw.posts() == ["post 7"]
    body = gw.comments[7][0]["body"]
    assert "`no-feature`" in body and "no `Feature group:` line" in body
    assert "Feature group: #<n>" in body and "Epic" in body
    assert body.rstrip().endswith(MARKER)
    assert gw.comments[7][0]["user"]["login"] == BOT


def test_human_line(gw, settings, capsys, monkeypatch):
    _, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")))
    assert out.strip() == "[NO-FEATURE NOTE] #7: written"


def test_with_the_marker_nothing_is_posted_also_on_a_repeat(gw, settings, capsys, monkeypatch):
    for _ in range(3):
        code, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json")
        assert code == 0
    assert gw.posts() == ["post 7"]
    assert json.loads(out) == {"issue": 7, "result": "already"}


def test_a_marker_after_a_po_unblock_still_counts(gw, settings, capsys, monkeypatch):
    gw.add(7, "note\n\n" + MARKER)
    gw.add(7, "/unblock", login="LauraMardones")
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json")
    assert code == 0 and json.loads(out)["result"] == "already" and gw.posts() == []


def test_a_marker_from_another_account_does_not_count(gw, settings, capsys, monkeypatch):
    gw.add(7, MARKER, login="someone-else")
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json")
    assert code == 0 and json.loads(out)["result"] == "written"


def test_other_skip_reasons_write_nothing(gw, settings, capsys, monkeypatch):
    skipped = decision((1, "epic-not-refined"), (2, "feature-not-refined #5"), (3, "no-merged-spec: x"), (4, "wip-limit"))
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, skipped, "--json")
    assert code == 0 and out == "" and gw.calls == []


def test_only_the_no_feature_stories_are_noted_once_each(gw, settings, capsys, monkeypatch):
    skipped = decision((3, "no-feature"), (4, "buffer-full"), (3, "no-feature"), (9, "no-feature"))
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, skipped, "--json")
    assert code == 0
    assert [json.loads(line)["issue"] for line in out.splitlines()] == [3, 9]
    assert gw.posts() == ["post 3", "post 9"]


def test_unreadable_comments_write_nothing_and_exit_3(gw, settings, capsys, monkeypatch):
    gw.list_failures = 1
    code, out, err = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json")
    assert code == 3
    assert json.loads(out) == {"issue": 7, "result": "unreadable"}
    assert gw.posts() == [] and "#7" in err
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json")
    assert code == 0 and gw.posts() == ["post 7"]  # a later run tries again


def test_one_unreadable_story_does_not_stop_the_next(gw, settings, capsys, monkeypatch):
    gw.list_failures = 1
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature"), (8, "no-feature")), "--json")
    assert code == 3
    assert [json.loads(line)["result"] for line in out.splitlines()] == ["unreadable", "written"]


def test_a_failed_write_exits_1_and_a_later_run_tries_again(gw, settings, capsys, monkeypatch):
    gw.post_modes = ["fail"] * 10
    code, out, err = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json")
    assert code == 1 and json.loads(out) == {"issue": 7, "result": "failed"} and "failed" in err
    assert not gw.comments.get(7)
    gw.post_modes = []
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json")
    assert code == 0 and json.loads(out)["result"] == "written"


def test_dry_run_writes_nothing(gw, settings, capsys, monkeypatch):
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, decision((7, "no-feature")), "--json", "--dry-run")
    assert code == 0 and json.loads(out)["result"] == "dry-run" and gw.posts() == []


@pytest.mark.parametrize("output", ["", "not json", "[]", '{"result": "idle"}', '{"skipped": {"issue": 1}}',
                                    '{"skipped": [{"issue": "x", "reason": "no-feature"}]}', '{"skipped": [1]}'])
def test_unusable_scheduler_output_writes_nothing_and_exits_3(gw, settings, capsys, monkeypatch, output):
    code, out, _ = run_note(gw, settings, capsys, monkeypatch, output, "--json")
    assert code == 3 and out == "" and gw.calls == []


def test_a_missing_repo_is_invalid(gw, settings, capsys):
    code = cli.main(["no-feature-note"], gateway_factory=lambda repo: gw, settings=settings)
    assert code == 2
