"""Block 3, saved state: ``hpw dispatcher state`` (issue #434, ADR-014).

File state runs against ``tmp_path`` directories; comment state against the Python fake
gateway of the Block 1 tests: no network, no real ``gh``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import cli, state
from headless_pr_workflow.dispatcher.write import Settings, evidence_pending_comment

from tests.test_dispatcher_write_block import PO, FakeGateway

TODAY = "2026-11-06"
EVENT = json.dumps({"key": "decision_blocker:7:s", "event": "decision_blocker", "issue": 7, "since": "s", "context": "{}",
                    "delivered": False, "failed_cycles": 0, "first_failed_at": "", "comment_posted": False})
HOLD = json.dumps({"until": 1790000000, "reserve_five_hour_percent": 10, "reserve_weekly_percent": 10})


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPATCHER_STATE_DIR", str(tmp_path / "dispatcher-state"))
    monkeypatch.setenv("USAGE_LIMIT_STATE_DIR", str(tmp_path / "usage"))
    monkeypatch.setenv("ALLOWANCE_STATE_DIR", str(tmp_path / "allowance"))
    monkeypatch.setenv("USAGE_LIMIT_TODAY", TODAY)


# name -> (file, a good content, the empty value)
FILES = {
    "actionable-events": ("dispatcher-state/o/r/actionable-events", EVENT + "\n", []),
    "notified-refinement": ("dispatcher-state/o/r/notified-refinement", "12\n40\n", []),
    "usage-sessions": ("usage/sessions", f"{TODAY}:3\n", {"day": None, "count": 0}),
    "usage-notified": ("usage/notified", f"{TODAY}\n", {"day": None}),
    "allowance-hold": ("allowance/hold.json", HOLD + "\n", None),
}
MALFORMED = {
    "actionable-events": '{"key":"k"}\n',
    "notified-refinement": "12\nabc\n",
    "usage-sessions": "garbage\n",
    "usage-notified": "tomorrow\n",
    "allowance-hold": '{"until": "soon"}',
}
STALE = {"usage-sessions": "2026-11-05:9\n", "usage-notified": "2026-11-05\n"}


def run(capsys, *argv, gateway=None, settings=None):
    gw = gateway or FakeGateway()
    rc = cli.main(list(argv), gateway_factory=lambda repo: gw, settings=settings or Settings(retry_delay=0, po_login=PO))
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    return rc, json.loads(lines[0]), err


def read(capsys, name):
    return run(capsys, "state", "read", "--name", name, "--repo", "o/r")


def write(capsys, tmp_path, name, content, *extra):
    data = tmp_path / "data"
    data.write_text(content, newline="\n")
    return run(capsys, "state", "write", "--name", name, "--data-file", str(data), "--repo", "o/r", *extra)


def path_of(tmp_path, name):
    return tmp_path / FILES[name][0]


def put(tmp_path, name, content):
    path = path_of(tmp_path, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, newline="\n")
    return path


# ── AC 1: the contract ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", FILES)
def test_read_prints_one_json_line_with_the_contract_fields(capsys, tmp_path, name):
    put(tmp_path, name, FILES[name][1])
    rc, out, _ = read(capsys, name)
    assert rc == 0 and out["ok"] is True and out["block"] == "state" and out["operation"] == "read"
    assert out["result"] == "ok" and out["name"] == name and out["error"] is None and "data" in out


@pytest.mark.parametrize("argv", [
    ["state"],
    ["state", "read"],
    ["state", "read", "--name", "nonsense", "--repo", "o/r"],
    ["state", "read", "--name", "actionable-events"],
    ["state", "read", "--name", "actionable-events", "--repo", "not-a-repo"],
    ["state", "write", "--name", "usage-sessions"],
    ["state", "write", "--name", "usage-sessions", "--data-file", "x", "--repo", "o/r", "--bogus"],
    ["state", "read-pending", "--number", "5"],
    ["state", "read-pending", "--number", "0", "--item", "merge-confirmation", "--repo", "o/r"],
    ["state", "read-pending", "--number", "5", "--item", "bad item", "--repo", "o/r"],
    ["state", "read-pending", "--number", "5", "--item", "x", "--expected-head", "zz", "--repo", "o/r"],
])
def test_unknown_name_or_invalid_arguments_exit_two(capsys, argv):
    rc = cli.main(argv, gateway_factory=lambda repo: FakeGateway())
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["ok"] is False and out["result"] == "invalid" and out["block"] == "state"


def test_the_day_files_need_no_repository(capsys):
    rc, out, _ = run(capsys, "state", "read", "--name", "usage-sessions")
    assert rc == 0 and out["result"] == "missing"


def test_dry_run_writes_nothing_and_returns_dry_run(capsys, tmp_path):
    rc, out, _ = write(capsys, tmp_path, "usage-sessions", f"{TODAY}:1\n", "--dry-run")
    assert rc == 0 and out["ok"] is True and out["result"] == "dry-run"
    assert not path_of(tmp_path, "usage-sessions").exists()


def test_dry_run_still_refuses_invalid_data(capsys, tmp_path):
    rc, out, _ = write(capsys, tmp_path, "usage-sessions", "garbage", "--dry-run")
    assert rc == 1 and out["result"] == "failed"


# ── AC 2: invalid state is rejected ──────────────────────────────────────────


@pytest.mark.parametrize("name", FILES)
def test_a_missing_file_is_missing_with_the_empty_value(capsys, name):
    rc, out, _ = read(capsys, name)
    assert rc == 0 and out["result"] == "missing" and out["data"] == FILES[name][2] and out["ok"] is True


@pytest.mark.parametrize("name", FILES)
def test_a_malformed_file_is_malformed_and_left_alone(capsys, tmp_path, name):
    path = put(tmp_path, name, MALFORMED[name])
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    rc, out, _ = read(capsys, name)
    assert rc == 1 and out["ok"] is False and out["result"] == "malformed" and out["error"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_an_event_file_keeps_its_valid_records_next_to_a_malformed_line(capsys, tmp_path):
    put(tmp_path, "actionable-events", EVENT + "\n" + '{"key":"trunc\n')
    rc, out, _ = read(capsys, "actionable-events")
    assert rc == 1 and out["result"] == "malformed" and out["dropped"] == 1
    assert [e["issue"] for e in out["data"]] == [7]


def test_an_event_file_keeps_its_valid_records_next_to_an_undecodable_line(capsys, tmp_path):
    path = path_of(tmp_path, "actionable-events")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(EVENT.encode() + b"\n\xff\n")
    rc, out, _ = read(capsys, "actionable-events")
    assert rc == 1 and out["result"] == "malformed" and out["dropped"] == 1
    assert [e["issue"] for e in out["data"]] == [7]
    assert path.read_bytes() == EVENT.encode() + b"\n\xff\n"


@pytest.mark.parametrize("separator", ["\u2028", "\u0085", "\u2029", "\x0b", "\x0c", "\x1c"])
def test_an_event_record_with_a_unicode_line_separator_in_a_string_stays_whole(capsys, tmp_path, separator):
    record = json.loads(EVENT)
    record["context"] = f"title{separator}rest"
    put(tmp_path, "actionable-events", json.dumps(record, ensure_ascii=False) + "\n")
    rc, out, _ = read(capsys, "actionable-events")
    assert rc == 0 and out["result"] == "ok" and out["dropped"] == 0
    assert [e["context"] for e in out["data"]] == [record["context"]]


@pytest.mark.parametrize("name", STALE)
def test_a_file_dated_another_day_is_stale(capsys, tmp_path, name):
    path = put(tmp_path, name, STALE[name])
    rc, out, _ = read(capsys, name)
    assert rc == 1 and out["result"] == "stale" and out["data"]["day"] == "2026-11-05"
    assert path.read_text() == STALE[name]


@pytest.mark.parametrize("name", FILES)
def test_a_path_that_is_not_a_regular_file_is_unreadable(capsys, tmp_path, name):
    path = path_of(tmp_path, name)
    path.mkdir(parents=True)
    rc, out, _ = read(capsys, name)
    assert rc == 3 and out["result"] == "unreadable" and out["reason"] == "not-a-file" and out["ok"] is False
    assert path.is_dir()


@pytest.mark.parametrize("name", FILES)
def test_a_file_that_cannot_be_read_is_unreadable(capsys, tmp_path, name, monkeypatch):
    path = put(tmp_path, name, FILES[name][1])

    def refuse(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    rc, out, _ = read(capsys, name)
    assert rc == 3 and out["result"] == "unreadable" and out["reason"] == "read-failed" and str(path) in out["error"]
    monkeypatch.undo()
    assert path.read_text() == FILES[name][1]


def test_a_parent_that_is_a_file_reads_as_missing(capsys, tmp_path):
    (tmp_path / "usage").write_text("x")
    rc, out, _ = read(capsys, "usage-sessions")
    assert rc == 0 and out["result"] == "missing"


# ── AC 3: writes are safe ────────────────────────────────────────────────────


@pytest.mark.parametrize("name", FILES)
def test_write_stores_the_data_and_reads_it_back(capsys, tmp_path, name):
    rc, out, _ = write(capsys, tmp_path, name, FILES[name][1])
    assert rc == 0 and out["result"] == "written" and out["ok"] is True and out["name"] == name
    assert path_of(tmp_path, name).read_text() == FILES[name][1]
    assert read(capsys, name)[1]["result"] == "ok"
    assert not list(path_of(tmp_path, name).parent.glob("*.tmp.*"))


@pytest.mark.parametrize("name", FILES)
def test_invalid_data_is_refused_and_the_existing_file_is_unchanged(capsys, tmp_path, name):
    path = put(tmp_path, name, FILES[name][1])
    rc, out, err = write(capsys, tmp_path, name, MALFORMED[name])
    assert rc == 1 and out["result"] == "failed" and out["ok"] is False
    assert path.read_text() == FILES[name][1]
    assert err.count("\n") == 1 and str(path) in err and "write" in err


def test_a_failed_rename_leaves_the_old_file_whole_and_names_the_file_and_step(capsys, tmp_path, monkeypatch):
    path = put(tmp_path, "usage-sessions", f"{TODAY}:1\n")

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", refuse)
    rc, out, err = write(capsys, tmp_path, "usage-sessions", f"{TODAY}:2\n")
    monkeypatch.undo()
    assert rc == 1 and out["result"] == "failed" and str(path) in out["error"] and out["error"].startswith("write ")
    assert err.count("\n") == 1 and str(path) in err and "usage-sessions" in err
    assert path.read_text() == f"{TODAY}:1\n"
    assert not list(path.parent.glob("*.tmp.*"))


def test_a_failed_read_back_is_failed(capsys, tmp_path, monkeypatch):
    path = path_of(tmp_path, "usage-sessions")
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"something else" if self == path else real(self))
    rc, out, err = write(capsys, tmp_path, "usage-sessions", f"{TODAY}:2\n")
    assert rc == 1 and out["result"] == "failed" and "read back" in out["error"]
    assert str(path) in err


def test_a_write_to_an_unwritable_directory_is_failed(capsys, tmp_path, monkeypatch):
    (tmp_path / "usage").write_text("a file where the directory should be")
    rc, out, err = write(capsys, tmp_path, "usage-sessions", f"{TODAY}:2\n")
    assert rc == 1 and out["result"] == "failed" and "usage" in err


def test_a_path_that_is_not_a_regular_file_is_moved_aside_never_overwritten(capsys, tmp_path):
    path = path_of(tmp_path, "actionable-events")
    (path / "inner").mkdir(parents=True)
    (path / "inner" / "keep").write_text("kept")
    rc, out, err = write(capsys, tmp_path, "actionable-events", EVENT + "\n")
    assert rc == 0 and out["result"] == "written" and path.read_text() == EVENT + "\n"
    assert (Path(f"{path}.unreadable") / "inner" / "keep").read_text() == "kept"
    assert "moved aside" in err


def test_a_file_that_cannot_be_read_is_moved_aside_never_overwritten(capsys, tmp_path, monkeypatch):
    path = put(tmp_path, "allowance-hold", HOLD)
    real_open = open

    def refuse(file, mode="r", *a, **k):
        if Path(file) == path and "b" in mode and "w" not in mode:
            raise PermissionError(13, "Permission denied")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr("builtins.open", refuse)
    rc, out, _ = write(capsys, tmp_path, "allowance-hold", HOLD + "\n")
    monkeypatch.undo()
    assert rc == 0 and out["result"] == "written"
    assert Path(f"{path}.unreadable").read_text() == HOLD
    assert path.read_text() == HOLD + "\n"


def test_an_earlier_moved_aside_copy_is_replaced_by_the_newer_one(capsys, tmp_path):
    path = path_of(tmp_path, "usage-notified")
    path.mkdir(parents=True)
    Path(f"{path}.unreadable").write_text("older")
    rc, _, _ = write(capsys, tmp_path, "usage-notified", f"{TODAY}\n")
    assert rc == 0 and path.read_text() == f"{TODAY}\n" and Path(f"{path}.unreadable").is_dir()


def test_a_malformed_but_readable_file_is_replaced(capsys, tmp_path):
    path = put(tmp_path, "notified-refinement", "oops\n")
    rc, out, _ = write(capsys, tmp_path, "notified-refinement", "5\n")
    assert rc == 0 and out["result"] == "written" and path.read_text() == "5\n"
    assert not Path(f"{path}.unreadable").exists()


def test_an_empty_event_file_is_valid_state(capsys, tmp_path):
    rc, out, _ = write(capsys, tmp_path, "notified-refinement", "")
    assert rc == 0 and path_of(tmp_path, "notified-refinement").read_text() == ""
    assert read(capsys, "notified-refinement")[1]["data"] == []


def test_a_data_file_that_cannot_be_read_is_failed(capsys, tmp_path):
    rc, out, _ = run(capsys, "state", "write", "--name", "usage-sessions", "--data-file", str(tmp_path / "nope"))
    assert rc == 1 and out["result"] == "failed" and "data file" in out["error"]


def test_state_path_defaults_to_the_repository_directories(monkeypatch):
    for name in ("DISPATCHER_STATE_DIR", "USAGE_LIMIT_STATE_DIR", "ALLOWANCE_STATE_DIR"):
        monkeypatch.delenv(name)
    root = Path(state.__file__).resolve().parents[3]
    assert state.state_path("actionable-events", "o/r") == root / ".dispatcher-state/o/r/actionable-events"
    assert state.state_path("usage-sessions", "o/r") == root / ".dispatcher-usage/sessions"
    assert state.state_path("allowance-hold", "o/r") == root / ".dispatcher-allowance/hold.json"


# ── AC 5: comment state ──────────────────────────────────────────────────────

HEAD = "a1b2c3d4e5f6"
PAYLOAD = "Merge gate: confirmed\nsecond line"


def pending(gw, number=5, item="merge-confirmation", head=HEAD, payload=PAYLOAD, login=None):
    body = evidence_pending_comment(item, head, payload)
    gw.add(number, body, **({"login": login} if login else {}))


def read_pending(capsys, gw, *extra, item="merge-confirmation"):
    return run(capsys, "state", "read-pending", "--number", "5", "--item", item, "--repo", "o/r", *extra, gateway=gw)


def test_read_pending_is_none_without_a_comment(capsys):
    rc, out, _ = read_pending(capsys, FakeGateway())
    assert rc == 0 and out["ok"] is True and out["result"] == "none" and out["payload"] is None
    assert out["block"] == "state" and out["operation"] == "read-pending" and out["error"] is None


def test_read_pending_decodes_the_payload(capsys):
    gw = FakeGateway()
    pending(gw)
    rc, out, _ = read_pending(capsys, gw, "--expected-head", HEAD.upper())
    assert rc == 0 and out["result"] == "ok" and out["payload"] == PAYLOAD and out["head"] == HEAD


def test_read_pending_takes_the_newest_comment_of_the_item(capsys):
    gw = FakeGateway()
    pending(gw, payload="old")
    pending(gw, item="cleanup-summary", payload="other item")
    pending(gw, payload="new")
    rc, out, _ = read_pending(capsys, gw)
    assert out["payload"] == "new"


def test_read_pending_rejects_a_payload_for_another_head(capsys):
    gw = FakeGateway()
    pending(gw)
    rc, out, _ = read_pending(capsys, gw, "--expected-head", "ffffff0")
    assert rc == 1 and out["ok"] is False and out["result"] == "stale-head" and out["payload"] is None
    assert out["reason"] == "other-head" and out["head"] == HEAD


def test_read_pending_ignores_a_comment_by_another_account(capsys):
    gw = FakeGateway()
    pending(gw, login="someone-else")
    rc, out, _ = read_pending(capsys, gw)
    assert rc == 0 and out["result"] == "none"


@pytest.mark.parametrize("body,reason", [
    ("<!-- hpw:dispatcher action=evidence-pending item=merge-confirmation head=abc1234 -->", "malformed"),
    ("<!-- hpw:dispatcher action=evidence-pending item=merge-confirmation head=abc1234 payload=!!!notbase64 -->", "malformed"),
    ("<!-- hpw:dispatcher action=evidence-pending item=merge-confirmation head=abc1234 payload= -->", "undecodable"),
])
def test_read_pending_with_a_payload_that_cannot_be_decoded_is_malformed(capsys, body, reason):
    gw = FakeGateway()
    gw.add(5, body)
    rc, out, _ = read_pending(capsys, gw)
    assert rc == 1 and out["result"] == "malformed" and out["payload"] is None and out["reason"] in (reason, "malformed", "undecodable")


def test_read_pending_with_unreadable_comments_is_unreadable(capsys):
    gw = FakeGateway()
    gw.list_failures = 1
    rc, out, _ = read_pending(capsys, gw)
    assert rc == 3 and out["ok"] is False and out["result"] == "unreadable" and out["error"]


def test_retry_pending_reads_its_payload_through_read_pending(monkeypatch):
    from headless_pr_workflow.dispatcher import write as write_module

    calls = []
    real = state.read_pending

    def spy(*args, **kwargs):
        calls.append(kwargs.get("expected_head"))
        return real(*args, **kwargs)

    monkeypatch.setattr(state, "read_pending", spy)
    gw = FakeGateway()
    pending(gw)
    result = write_module.retry_pending(gw, 5, "merge-confirmation", "pr5", 7, expected_head=HEAD, dry_run=True,
                                        settings=Settings(retry_delay=0, po_login=PO))
    assert calls == [HEAD] and result.result == "dry-run" and result.fields["payload"] == PAYLOAD
