"""Block 3: saved state (ADR-014 → Decision → 3 → Block 3, issue #434).

``read`` checks a registered state file against its current format and never
changes it. ``write`` checks the data, writes a temporary file, renames it into
place and reads it back; an existing path that is not a regular file, or a
regular file that cannot be read, is moved aside to ``<file>.unreadable`` and
never overwritten. ``read-pending`` decodes the payload of the dispatcher's own
evidence-pending comment, found through Block 2.

Result words and exit codes:

- ``read`` → ``ok``, ``missing`` (the name's empty value) (0); ``malformed``,
  ``stale`` (another day) (1); ``unreadable`` (3)
- ``write`` → ``written``, ``dry-run`` (0); ``failed`` (1)
- ``read-pending`` → ``none``, ``ok`` (0); ``malformed``, ``stale-head`` (1);
  ``unreadable`` (3)
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .gateway import Gateway
from .marker import list_markers
from .write import _PENDING_FIELDS, EXIT_INVALID, EXIT_NO, EXIT_OK, EXIT_UNREADABLE, Result, log

BLOCK = "state"
FILE_NAMES = ("actionable-events", "usage-sessions", "usage-notified", "allowance-hold")

_EXIT = {
    "ok": EXIT_OK,
    "missing": EXIT_OK,
    "none": EXIT_OK,
    "written": EXIT_OK,
    "dry-run": EXIT_OK,
    "malformed": EXIT_NO,
    "stale": EXIT_NO,
    "stale-head": EXIT_NO,
    "failed": EXIT_NO,
    "unreadable": EXIT_UNREADABLE,
}

_DAY = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_SESSIONS = re.compile(rf"^({_DAY}):([0-9]+)$")
_NOTIFIED = re.compile(rf"^({_DAY})$")


class StateResult(Result):
    """A ``Result`` whose words mean what the Block 3 table says (``missing`` is exit 0 here)."""

    @property
    def exit_code(self) -> int:  # type: ignore[override]
        return _EXIT.get(self.result, EXIT_NO)


def _result(operation: str, result: str, name: str | None = None, error: str | None = None, **fields: Any) -> StateResult:
    if name is not None:
        fields = {"name": name, **fields}
    return StateResult(operation, result, error=error, fields=fields, block=BLOCK)


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def today_utc(env: Mapping[str, str]) -> str:
    return (env.get("USAGE_LIMIT_TODAY") or "").strip() or datetime.now(timezone.utc).strftime("%Y-%m-%d")


def state_path(name: str, repo: str, env: Mapping[str, str] | None = None) -> Path:
    """The file of a registered name; the directories keep the overrides the scripts always had."""

    env = os.environ if env is None else env
    if name == "actionable-events":
        base = Path(env.get("DISPATCHER_STATE_DIR") or _root() / ".dispatcher-state")
        owner, _, repo_name = repo.partition("/")
        return base / owner / repo_name / name
    if name in ("usage-sessions", "usage-notified"):
        base = Path(env.get("USAGE_LIMIT_STATE_DIR") or _root() / ".dispatcher-usage")
        return base / ("sessions" if name == "usage-sessions" else "notified")
    base = Path(env.get("ALLOWANCE_STATE_DIR") or _root() / ".dispatcher-allowance")
    return base / "hold.json"


def empty_value(name: str) -> Any:
    return {
        "actionable-events": [],
        "usage-sessions": {"day": None, "count": 0},
        "usage-notified": {"day": None},
        "allowance-hold": None,
    }[name]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _event_record(value: Any) -> bool:
    """The fields a delivery cycle relies on (``load_state`` of dispatcher-notify-retry.sh)."""

    return (
        isinstance(value, dict)
        and isinstance(value.get("key"), str)
        and isinstance(value.get("event"), str)
        and _number(value.get("issue"))
        and isinstance(value.get("context"), str)
        and isinstance(value.get("delivered"), bool)
        and _number(value.get("failed_cycles"))
    )


def parse(name: str, text: str, today: str) -> tuple[str, Any, dict[str, Any]]:
    """``(result, data, extra fields)`` of file content; ``result`` is ``ok``, ``malformed`` or ``stale``."""

    if name == "actionable-events":
        kept: list[Any] = []
        total = 0
        # Split at "\n" only: splitlines() also breaks at U+2028/U+0085, legal inside a JSON string.
        for line in text.split("\n"):
            if not line.strip():
                continue
            total += 1
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if _event_record(value):
                kept.append(value)
        # A malformed line never discards a valid pending record next to it (#338).
        return ("ok" if len(kept) == total else "malformed"), kept, {"dropped": total - len(kept)}
    if name == "usage-sessions":
        match = _SESSIONS.match(text.rstrip("\n"))
        if match is None:
            return "malformed", empty_value(name), {}
        data = {"day": match.group(1), "count": int(match.group(2))}
        return ("ok" if data["day"] == today else "stale"), data, {}
    if name == "usage-notified":
        match = _NOTIFIED.match(text.rstrip("\n"))
        if match is None:
            return "malformed", empty_value(name), {}
        data = {"day": match.group(1)}
        return ("ok" if data["day"] == today else "stale"), data, {}
    try:
        value = json.loads(text)
    except ValueError:
        return "malformed", None, {}
    if isinstance(value, dict) and _number(value.get("until")):
        return "ok", value, {}
    return "malformed", None, {}


def _decode_line(line: bytes) -> str:
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError:
        return "\ufffd"  # not JSON, so the line counts as a dropped record


def read_state(name: str, repo: str, env: Mapping[str, str] | None = None) -> StateResult:
    env = os.environ if env is None else env
    path = state_path(name, repo, env)
    where = {"path": str(path)}
    try:
        info = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return _result("read", "missing", name, data=empty_value(name), **where)
    except OSError as exc:
        return _result("read", "unreadable", name, error=f"read {path}: {exc.strerror or exc}", reason="read-failed",
                       data=empty_value(name), **where)  # fmt: skip
    if not stat.S_ISREG(info.st_mode):
        return _result("read", "unreadable", name, error=f"read {path}: not a regular file", reason="not-a-file",
                       data=empty_value(name), **where)  # fmt: skip
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _result("read", "unreadable", name, error=f"read {path}: {exc.strerror or exc}", reason="read-failed",
                       data=empty_value(name), **where)  # fmt: skip
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        if name == "actionable-events":
            # Decode record by record: an undecodable line is dropped, the valid records next to it are kept (#338).
            text = "\n".join(_decode_line(line) for line in raw.split(b"\n"))
            status, data, extra = parse(name, text, today_utc(env))
            return _result("read", status, name, error=f"read {path}: malformed content", data=data, **extra, **where)
        return _result("read", "malformed", name, error=f"read {path}: not valid UTF-8", data=empty_value(name), **where)
    status, data, extra = parse(name, text, today_utc(env))
    error = None if status == "ok" else f"read {path}: {'malformed content' if status == 'malformed' else 'dated another day'}"
    return _result("read", status, name, error=error, data=data, **extra, **where)


def _move_aside(path: Path) -> None:
    aside = Path(f"{path}.unreadable")
    if aside.is_dir() and not aside.is_symlink():
        shutil.rmtree(aside, ignore_errors=True)
    else:
        aside.unlink(missing_ok=True)
    os.replace(path, aside)


def write_state(name: str, repo: str, text: str, *, dry_run: bool = False, env: Mapping[str, str] | None = None) -> StateResult:
    env = os.environ if env is None else env
    path = state_path(name, repo, env)
    where = {"path": str(path)}
    # The data is checked against the name's format first; an event file takes only whole records.
    status, _, _ = parse(name, text, today_utc(env))
    if status == "malformed":
        return _fail(name, path, "the data does not match the format of the name")
    if dry_run:
        return _result("write", "dry-run", name, **where)
    try:
        existing = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        existing = None
    except OSError as exc:
        return _fail(name, path, exc.strerror or str(exc))
    temp = Path(f"{path}.tmp.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if existing is not None:
            readable = stat.S_ISREG(existing.st_mode)
            if readable:
                try:
                    with open(path, "rb"):
                        pass
                except OSError:
                    readable = False
            if not readable:
                _move_aside(path)
                log(f"[STATE] Warning: {path} is not a readable regular file; moved aside to {path}.unreadable")
        with open(temp, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        if path.read_bytes() != text.encode("utf-8"):
            return _fail(name, path, "the file read back differs from the data written")
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return _fail(name, path, exc.strerror or str(exc))
    return _result("write", "written", name, **where)


def _fail(name: str, path: Path, reason: str) -> StateResult:
    error = f"write {path}: {reason}"
    log(f"[STATE] Error: cannot write {name} state {error}")
    return _result("write", "failed", name, error=error, path=str(path))


# ── comment state ─────────────────────────────────────────────────────────────


def decode_pending(comment_body: str, expected_head: str | None) -> tuple[str, dict[str, Any], str | None]:
    """``(result, fields, reason)`` for the body of an evidence-pending comment."""

    match = _PENDING_FIELDS.search(comment_body)
    if match is None:
        return "malformed", {}, "malformed"
    head, encoded = match.group(1), match.group(2)
    fields: dict[str, Any] = {"head": head}
    if expected_head and head.lower() != expected_head.lower():
        return "stale-head", fields, "other-head"
    try:
        payload = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        payload = ""
    if not payload:
        return "malformed", fields, "undecodable"
    return "ok", {**fields, "payload": payload}, None


def read_pending(
    gateway: Gateway, number: int, item: str, *, expected_head: str | None = None,
    own: list[dict[str, Any]] | None = None,
) -> StateResult:
    """The payload of the newest evidence-pending comment of ``item`` on ``number``.

    The comment is found through Block 2, so only the dispatcher's own account counts.
    ``own`` is the list of ``{"created_at", "body"}`` rows a caller has already read from Block 2.
    """

    base: dict[str, Any] = {"number": number, "item": item}
    if own is None:
        listed = list_markers(gateway, number, marker=f"action=evidence-pending item={item} ")
        if listed.result != "read":
            return _result("read-pending", "unreadable", error=listed.error, payload=None, **base)
        own = listed.fields["comments"]
    if not own:
        return _result("read-pending", "none", payload=None, **base)
    newest = own[-1]
    base["pending_at"] = str(newest.get("created_at") or "")
    result, fields, reason = decode_pending(str(newest.get("body") or ""), expected_head)
    fields = {**base, **fields}
    fields.setdefault("payload", None)
    if reason:
        fields["reason"] = reason
    error = None if result == "ok" else f"read pending {item} on #{number}: {reason}"
    return _result("read-pending", result, error=error, **fields)
