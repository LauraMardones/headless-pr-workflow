"""One command reads each GitHub list once (issue #626): the read cache of ``GhGateway``."""

from __future__ import annotations

import json
import sys

import pytest

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.gateway import GatewayError, GhGateway

FAKE_GH = r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
path = next((a for a in args if a.startswith("repos/")), "")
if os.environ.get("FAKE_GH_FAIL") and os.environ["FAKE_GH_FAIL"] in path:
    sys.stderr.write("HTTP 500\n")
    sys.exit(1)
if "graphql" in args:
    print(json.dumps({"errors": [{"message": "no board in this fake"}]}))
elif "state=open" in path and path.endswith("per_page=100") and "/issues?" in path:
    print(json.dumps([{"number": 1, "title": "one", "state": "open", "labels": [], "body": "", "user": {"login": "u"}},
                      {"number": 2, "title": "pr", "pull_request": {}}]))
elif "/pulls?" in path:
    print("[]")
elif "state=closed" in path:
    print("[]")
elif "/comments" in path and args[:2] == ["api", "-X"] or "-X" in args:
    print("{}")
elif "/comments" in path:
    print("[]")
else:
    print("{}")
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "gh.py"
    script.write_text(FAKE_GH, encoding="utf-8")
    log = tmp_path / "log"
    log.write_text("")
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.delenv("FAKE_GH_FAIL", raising=False)

    def make():
        return GhGateway("o/r", gh_command=(sys.executable, str(script)))

    def paths():
        return [next((a for a in json.loads(l) if a.startswith("repos/")), "graphql") for l in log.read_text().splitlines()]

    return make, paths


def _run(make, argv):
    gateway = make()
    return cli.main(argv, gateway_factory=lambda repo: gateway)


@pytest.mark.parametrize("argv", [
    ["status", "read", "--repo", "o/r"],
    ["status", "sync", "--repo", "o/r", "--dry-run"],
    ["facts", "read", "--repo", "o/r"],
    ["scheduler", "next", "--repo", "o/r"],
])
def test_one_command_reads_each_list_once(fake, argv, capsys):
    make, paths = fake
    _run(make, argv)
    seen = paths()
    for key in ("issues?state=open", "pulls?state=open", "issues?state=closed"):
        assert sum(key in p for p in seen) <= 1, seen
    comments = [p for p in seen if "/comments" in p]
    assert len(comments) == len(set(comments)), comments


@pytest.mark.parametrize("argv", [
    ["status", "read", "--repo", "o/r"],
    ["status", "sync", "--repo", "o/r", "--dry-run"],
    ["facts", "read", "--repo", "o/r"],
    ["scheduler", "next", "--repo", "o/r"],
])
def test_cache_changes_no_output_or_exit_code(fake, argv, capsys):
    make, _ = fake
    code_on = _run(make, argv)
    out_on = capsys.readouterr().out
    plain = make()  # never enabled: the uncached behaviour
    code_plain = cli.main(argv, gateway_factory=lambda repo: _Uncached(plain))
    out_plain = capsys.readouterr().out
    assert code_on == code_plain
    assert out_on == out_plain


class _Uncached:
    """Forwards to a gateway without exposing ``enable_read_cache``."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name == "enable_read_cache":
            raise AttributeError(name)
        return getattr(self._inner, name)


def test_cache_is_off_by_default_and_per_instance(fake):
    make, paths = fake
    first, second = make(), make()
    first.enable_read_cache()
    first.list_open_issues()
    first.list_open_issues()
    second.list_open_issues()
    second.list_open_issues()
    assert len([p for p in paths() if "issues?state=open" in p]) == 3  # one cached, two uncached


def test_other_commands_do_not_enable_the_cache(fake):
    make, _ = fake
    gateway = make()
    cli.main(["state", "read", "--repo", "o/r", "--item", "1", "--ref", "x"], gateway_factory=lambda repo: gateway)
    assert gateway._cache is None


def test_a_write_drops_the_cached_reads(fake):
    make, paths = fake
    gateway = make()
    gateway.enable_read_cache()
    gateway.list_open_issues()
    gateway.list_comments(1)
    gateway.list_open_issues()
    gateway.edit_issue_body(1, "new")
    gateway.list_open_issues()
    gateway.list_comments(1)
    seen = paths()
    assert len([p for p in seen if "issues?state=open" in p]) == 2
    assert len([p for p in seen if p.endswith("/issues/1/comments?per_page=100")]) == 2


def test_a_failed_read_is_raised_and_not_stored(fake, monkeypatch):
    make, paths = fake
    gateway = make()
    gateway.enable_read_cache()
    monkeypatch.setenv("FAKE_GH_FAIL", "state=open")
    with pytest.raises(GatewayError, match="HTTP 500") as error:
        gateway.list_open_issues()
    assert error.value.step == "read open issues"
    monkeypatch.delenv("FAKE_GH_FAIL")
    assert [i["number"] for i in gateway.list_open_issues()] == [1]
    assert len([p for p in paths() if "issues?state=open" in p]) == 2


def test_callers_cannot_change_the_cached_list(fake):
    make, _ = fake
    gateway = make()
    gateway.enable_read_cache()
    gateway.list_open_issues().clear()
    assert len(gateway.list_open_issues()) == 1
