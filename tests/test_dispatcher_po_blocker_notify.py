"""The po_blocker actionable Slack event (issue #359).

Registration is driven through the real ``scripts/dispatcher-invoke.sh`` with the
fakes in ``tests/dispatcher_fakes.py``; retry and resolution through
``scripts/dispatcher-notify-retry.sh`` with a fake ``gh`` and a fake
``slack-notify.sh``; the payload through ``scripts/slack-notify.sh`` with a fake
``curl``. Everything runs in the required ``pytest`` check.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from tests.dispatcher_fakes import BASH, HAVE_TOOLS, REPO_ROOT, Dispatcher

needs_tools = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
PR = 900
HEAD = "a" * 40
SCRIPTS = REPO_ROOT / "scripts"
PO_LOGIN = "LauraMardones"


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def po_events(d: Dispatcher) -> list[dict]:
    return [e for e in d.queued_events if e["event"] == "po_blocker"]


def declaration(d: Dispatcher, target: int = STORY) -> dict:
    return [c for c in d._read(f"comments_{target}.json", []) if "## Blocked Declaration" in c["body"]][-1]


# ── registration (AC 1, 2, 3, 7, 8) ──────────────────────────────────────────

@needs_tools
def test_ac1_fix_round_maximum_registers_one_po_blocker(d):
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    for head in ("1" * 40, "2" * 40, HEAD):
        d.add_findings(PR, head)

    first = d.resume(env={"CODEX_FIX_ROUND_MAX": "2"})
    second = d.resume(env={"CODEX_FIX_ROUND_MAX": "2"})

    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    events = po_events(d)
    assert len(events) == 1 and len(d.posted(STORY)) == 1  # the marker holds: nothing new on the second run
    event = events[0]
    assert event["issue"] == STORY and event["key"] == f"po_blocker:{STORY}:{event['since']}"
    assert event["since"] == declaration(d)["created_at"]
    context = json.loads(event["context"])
    assert context["blocker_type"] == "resource"
    assert context["story_title"] == f"Story {STORY}" and context["issue_url"] == f"https://github.com/o/r/issues/{STORY}"
    assert "fixed by hand" in context["unblocked_when"] and "CODEX_FIX_ROUND_MAX" in context["unblocked_when"]
    assert "2 automatic fix rounds" in context["state"] and context["cause"] == ""
    assert len(d.slack_calls) == 1  # queued, then delivered once by the drain after the resume item (#349)


@needs_tools
@pytest.mark.parametrize("pr", ["none", "closed-unmerged", "draft"])
def test_ac2_in_merge_story_without_a_pr_registers_one_po_blocker(d, pr):
    d.add_story(STORY, "In merge")
    if pr == "closed-unmerged":
        d.add_pr(PR, STORY, state="closed")
    elif pr == "draft":
        d.add_pr(PR, STORY, draft=True)

    assert d.resume().returncode == 0
    assert d.resume().returncode == 0

    events = po_events(d)
    assert len(events) == 1
    context = json.loads(events[0]["context"])
    assert context["blocker_type"] == "external" and "In merge" in context["state"]


@needs_tools
def test_ac2_failed_mid_cycle_command_registers_a_po_blocker(d):
    d.add_story(STORY, "Ready for implementation")
    d.fail("implement")

    d.run_issue(STORY)

    events = po_events(d)
    assert len(events) == 1 and events[0]["since"] == declaration(d)["created_at"]
    context = json.loads(events[0]["context"])
    assert context["blocker_type"] == "external"
    assert "/implement completes successfully" in context["unblocked_when"]
    assert "execution loop halted after /implement failed" in context["state"]


@needs_tools
def test_ac2_conflict_cause_travels_in_the_context(d):
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY, draft=True, mergeable=False, mergeable_state="dirty")

    d.run_issue(STORY)

    events = po_events(d)
    assert len(events) == 1
    context = json.loads(events[0]["context"])
    assert "merge conflict with main" in context["cause"]
    assert "Cause:" in declaration(d)["body"]


@needs_tools
def test_ac2_a_failed_blocker_write_registers_nothing(d):
    d.add_story(STORY, "In merge")
    d.fail_comment_writes(STORY, 5)

    d.resume()

    assert po_events(d) == []


@needs_tools
def test_ac3_conflict_blockers_register_nothing(d):
    d.add_story(501, "In implementation")
    board = d._read("board.json")  # a recently updated story is active, not stale
    for item in board["items"]:
        item["updatedAt"] = "2999-01-01T00:00:00Z"
    d._write("board.json", board)
    d.add_story(STORY, "Ready for implementation")

    proc = d.run_issue(STORY, env={"WIP_LIMIT_IMPLEMENTATION": "1"})

    assert proc.returncode != 0
    assert "Type: conflict" in declaration(d)["body"]
    assert d.queued_events == [] and d.slack_calls == []


@needs_tools
def test_ac7_evidence_give_up_registers_one_po_blocker_and_no_dispatcher_error(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    d.fail("cleanup")

    for _ in range(4):
        assert d.resume().returncode == 0

    assert len([e for e in po_events(d)]) == 1 and len(d.queued_events) == 1
    assert len(d.slack_calls) == 1  # one event, delivered once by the first drain, never again (#349)


@needs_tools
def test_ac8_dry_run_logs_the_would_register_line_and_writes_nothing(d):
    d.add_story(STORY, "In merge")

    proc = d.resume("--dry-run")

    assert proc.returncode == 0, proc.stderr
    assert f"[DRY RUN] Would register actionable event: po_blocker:{STORY}:" in proc.stderr + proc.stdout
    assert d.queued_events == [] and d.slack_calls == [] and d.posted(STORY) == []


# ── retry and resolution (AC 4, 5) ───────────────────────────────────────────

CONTEXT = json.dumps({"story_title": "Story 7", "issue_url": "https://github.com/o/r/issues/7",
                      "blocker_type": "resource", "unblocked_when": "x", "state": "y", "cause": ""})
SINCE = "2026-10-16T10:00:00Z"


class Retry:
    """dispatcher-notify-retry.sh next to a fake slack-notify.sh and a fake gh."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        (tmp_path / "scripts").mkdir()
        (tmp_path / "bin").mkdir()
        (tmp_path / "gh").mkdir()
        shutil.copy(SCRIPTS / "dispatcher-notify-retry.sh", tmp_path / "scripts")
        slack = tmp_path / "scripts" / "slack-notify.sh"
        slack.write_text(f'#!/usr/bin/env bash\necho "$1" >> "{(tmp_path / "slack.log").as_posix()}"\n'
                         f'[[ -e "{(tmp_path / "slack-fail").as_posix()}" ]] && exit 1\nexit 0\n', newline="\n")
        gh = tmp_path / "bin" / "gh"
        gh.write_text(f'''#!/usr/bin/env bash
shift; path=""; post=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --paginate) shift ;;
        -X) post=true; shift 2 ;;
        -f) echo "$path :: $2" >> "{(tmp_path / "posts.log").as_posix()}"; shift 2 ;;
        --jq) shift 2 ;;
        *) path="$1"; shift ;;
    esac
done
n=$(echo "$path" | sed -E 's#^repos/[^/]+/[^/]+/issues/([0-9]+).*#\\1#')
if [[ "$path" == */comments* ]]; then
    $post && exit 0
    cat "{tmp_path.as_posix()}/gh/comments_$n.json" 2>/dev/null || echo '[]'
else
    cat "{tmp_path.as_posix()}/gh/state_$n" 2>/dev/null || echo open
fi
''', newline="\n")
        for path in (slack, gh):
            path.chmod(0o755)

    def env(self):
        return {**os.environ, "PATH": f"{(self.root / 'bin').as_posix()}{os.pathsep}{os.environ['PATH']}",
                "DISPATCHER_STATE_DIR": (self.root / "state").as_posix()}

    def script(self, *args):
        return subprocess.run([BASH, (self.root / "scripts" / "dispatcher-notify-retry.sh").as_posix(), *args, "--repo", "o/r"],
                              capture_output=True, text=True, env=self.env(), timeout=60)

    def register(self, issue=7, since=SINCE, event="po_blocker"):
        proc = self.script("register", "--event", event, "--issue", str(issue), "--since", since, "--context", CONTEXT)
        assert proc.returncode == 0, proc.stderr
        return proc

    def run(self, *args):
        return self.script("run", *args)

    def fail_slack(self, failing=True):
        path = self.root / "slack-fail"
        path.write_text("") if failing else path.unlink(missing_ok=True)

    def comments(self, issue, comments):
        (self.root / "gh" / f"comments_{issue}.json").write_text(json.dumps(comments))

    def close(self, issue):
        (self.root / "gh" / f"state_{issue}").write_text("closed")

    @property
    def sent(self):
        path = self.root / "slack.log"
        return path.read_text().split() if path.exists() else []

    @property
    def posts(self):
        path = self.root / "posts.log"
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def retry(tmp_path):
    return Retry(tmp_path)


def comment(body, login=PO_LOGIN, at="2026-10-16T11:00:00Z"):
    return {"user": {"login": login}, "created_at": at, "body": body}


@needs_tools
def test_ac4_failed_delivery_is_retried_then_sent_once(retry):
    retry.register()
    retry.fail_slack()
    assert retry.run().returncode == 0 and retry.sent == ["po_blocker"]
    retry.fail_slack(False)
    assert retry.run().returncode == 0 and retry.sent == ["po_blocker", "po_blocker"]
    assert retry.run().returncode == 0 and len(retry.sent) == 2  # delivered: not sent again while unresolved


@needs_tools
def test_ac4_three_failed_cycles_turn_the_run_red_with_one_comment(retry):
    retry.register()
    retry.fail_slack()
    codes = [retry.run().returncode for _ in range(4)]
    assert codes == [0, 0, 1, 1]
    assert len([p for p in retry.posts if p.startswith("repos/o/r/issues/7/comments ::")]) == 1
    assert "`po_blocker` for #7" in "\n".join(retry.posts)


@needs_tools
@pytest.mark.parametrize("name,comments,dropped", [
    ("po unblock line", [comment("done\n/unblock\n")], True),
    ("padded unblock line", [comment("  /unblock  ")], True),
    ("unblock inside prose", [comment("please /unblock this later")], False),
    ("other account", [comment("/unblock", login="someone-else")], False),
    ("before since", [comment("/unblock", at="2026-10-16T09:00:00Z")], False),
])
def test_ac5_resolution_by_unblock_line(retry, name, comments, dropped):
    retry.register()
    retry.comments(7, comments)
    proc = retry.run()
    assert proc.returncode == 0, proc.stderr
    assert (retry.sent == []) is dropped, name
    assert ("Resolved, dropping" in proc.stderr) is dropped


@needs_tools
def test_ac5_closing_the_issue_resolves_it_and_a_later_blocker_is_a_new_event(retry):
    retry.register()
    retry.close(7)
    assert retry.run().returncode == 0 and retry.sent == []
    retry.register(issue=8)
    assert retry.run().returncode == 0 and retry.sent == ["po_blocker"]
    retry.comments(8, [comment("/unblock", at="2026-10-16T10:30:00Z")])
    retry.register(issue=8, since="2026-10-16T12:00:00Z")  # a new blocker after the resolved one
    proc = retry.run()
    assert retry.sent == ["po_blocker"] * 2 or "Resolved, dropping: po_blocker:8:2026-10-16T10:00:00Z" in proc.stderr
    assert retry.sent.count("po_blocker") == 2


# ── Slack payload (AC 6) ─────────────────────────────────────────────────────

def payload(tmp_path, context: dict) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "payload.json"
    curl = bin_dir / "curl"
    curl.write_text(f'#!/usr/bin/env bash\nwhile [[ $# -gt 0 ]]; do [[ "$1" == --data ]] && printf %s "$2" > "{out.as_posix()}"; shift; done\n',
                    newline="\n")
    curl.chmod(0o755)
    proc = subprocess.run([BASH, (SCRIPTS / "slack-notify.sh").as_posix(), "po_blocker", json.dumps(context)],
                          capture_output=True, text=True, timeout=60,
                          env={**os.environ, "SLACK_WEBHOOK_URL": "https://hooks.slack.test/x",
                               "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}"})
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text())


def texts(body: dict) -> list[str]:
    return [b["text"]["text"] for b in body["blocks"]]


RESUME_LINE = "To resume: do what *Unblocked when* says, then reply on the issue with a line that is exactly `/unblock`."


@needs_tools
def test_ac6_payload_has_every_field_link_and_resume_line(tmp_path):
    body = texts(payload(tmp_path, {"story_title": "My story", "issue_url": "https://github.com/o/r/issues/7",
                                    "blocker_type": "resource", "unblocked_when": "fix it", "state": "branch x",
                                    "cause": "merge conflict with main"}))
    joined = "\n".join(body)
    for needle in ("waiting for the PO", "*Story:* My story", "*Blocker type:* resource", "*Unblocked when:* fix it",
                   "*State of in-progress work:* branch x", "*Cause:* merge conflict with main",
                   "<https://github.com/o/r/issues/7|View issue>"):
        assert needle in joined, needle
    assert body[-1] == RESUME_LINE


@needs_tools
def test_ac6_missing_fields_show_unknown_and_no_cause_section(tmp_path):
    body = texts(payload(tmp_path, {"issue_url": "https://github.com/o/r/issues/7"}))
    joined = "\n".join(body)
    assert "*Story:* (unknown)" in joined and "*Unblocked when:* (unknown)" in joined
    assert "*State of in-progress work:* (unknown)" in joined and "Cause" not in joined


@needs_tools
def test_ac6_texts_stay_below_the_block_kit_limit_and_the_link_is_intact(tmp_path):
    long = "x" * 5000
    url = "https://github.com/o/r/issues/7"
    body = texts(payload(tmp_path, {"story_title": long, "issue_url": url, "blocker_type": long,
                                    "unblocked_when": long, "state": long, "cause": long}))
    assert all(len(t) <= 3000 for t in body)
    assert f"<{url}|View issue>" in "\n".join(body) and body[-1] == RESUME_LINE
