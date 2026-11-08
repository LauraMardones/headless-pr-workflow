"""The scan of the Codex review bookkeeping job's blockers (issue #393).

``scripts/dispatcher-notify-retry.sh scan-job-blockers`` is driven with a fake
``gh`` and a fake ``slack-notify.sh``; the real script registers and delivers.
The fixture comments are the output of the job's own ``blocker_body()``, so a
change to the job's declaration format fails these tests (F270.3).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from headless_pr_workflow import codex_review_bookkeeping as job
from tests.dispatcher_fakes import BASH, HAVE_TOOLS, REPO_ROOT
from tests.hpw_dispatcher_route import fake_state_route

needs_tools = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

SCRIPTS = REPO_ROOT / "scripts"
PO = "LauraMardones"
HEAD = "b" * 40
STORY = 7
T0 = "2026-11-08T10:00:00Z"
T1 = "2026-11-08T11:00:00Z"
T2 = "2026-11-08T12:00:00Z"


def story_board(number: int = STORY) -> job.StoryBoard:
    return job.StoryBoard(number=number, title=f"Story {number}", status=job.STATUS_IN_REVIEW)


def decision(action: str, **extra) -> job.Decision:
    return job.Decision(
        pr=900, head_sha=HEAD, story=STORY, story_status=job.STATUS_IN_REVIEW, codex_result="unknown",
        codex_url=None, action=action, reason="reason", requests_posted=3, max_requests=3,
        marker=job.action_marker(action, HEAD, source="x"), **extra,
    )


def unknown_format(number: int = STORY) -> str:
    return job.blocker_body(
        decision(job.ACTION_BLOCKER_EXTERNAL, blocker_unblocked_when="a new Codex review result",
                 blocker_detail="The latest Codex reply is in an unknown format."),
        story_board(number))


def max_requests(number: int = STORY) -> str:
    return job.blocker_body(
        decision(job.ACTION_BLOCKER_RESOURCE, blocker_unblocked_when="Codex posts a review result for head " + HEAD),
        story_board(number))


def unresolved_threads() -> str:
    return job.blocker_body(
        decision(job.ACTION_BLOCKER_EXTERNAL, blocker_unblocked_when="the listed threads are resolved",
                 blocker_detail="2 unresolved review thread(s)."), story_board())


def c(body, login=PO, at=T0, **extra):
    return {"user": {"login": login, "type": "User"}, "created_at": at, "body": body, **extra}


class Scan:
    def __init__(self, tmp_path):
        self.root = tmp_path
        for sub in ("scripts", "bin", "gh"):
            (tmp_path / sub).mkdir()
        shutil.copy(SCRIPTS / "dispatcher-notify-retry.sh", tmp_path / "scripts")
        slack = tmp_path / "scripts" / "slack-notify.sh"
        slack.write_text(f'#!/usr/bin/env bash\necho "$1 $2" >> "{(tmp_path / "slack.log").as_posix()}"\n'
                         f'[[ -e "{(tmp_path / "slack-fail").as_posix()}" ]] && exit 1\nexit 0\n', newline="\n")
        hpw = tmp_path / "scripts" / "hpw"
        hpw.write_text("#!/usr/bin/env bash\n" + fake_state_route() + "exit 2\n", newline="\n")
        gh = tmp_path / "bin" / "gh"
        data = tmp_path.as_posix()
        gh.write_text(f'''#!/usr/bin/env bash
echo "gh $*" >> "{data}/gh.log"
if [[ "$1 $2" == "pr list" ]]; then
    [[ -e "{data}/gh/pr_list_fail" ]] && exit 1
    cat "{data}/gh/prs.json"; exit 0
fi
path=""
shift
while [[ $# -gt 0 ]]; do
    case "$1" in --paginate) shift ;; --jq) shift 2 ;; *) path="$1"; shift ;; esac
done
n=$(echo "$path" | sed -E 's#^repos/[^/]+/[^/]+/issues/([0-9]+).*#\\1#')
if [[ "$path" == */comments* ]]; then
    [[ -e "{data}/gh/comments_fail_$n" ]] && exit 1
    cat "{data}/gh/comments_$n.json" 2>/dev/null || echo '[]'
else
    [[ -e "{data}/gh/issue_fail_$n" ]] && exit 1
    cat "{data}/gh/issue_$n.json" 2>/dev/null || echo '{{"state":"open","title":"Story '$n'"}}'
fi
''', newline="\n")
        for path in (slack, gh, hpw):
            path.chmod(0o755)
        self.set_prs([STORY])

    def set_prs(self, stories, draft=False):
        prs = [{"number": 900 + s, "isDraft": draft, "closingIssuesReferences": [{"number": s}]} for s in stories]
        (self.root / "gh" / "prs.json").write_text(json.dumps(prs))

    def comments(self, story, comments):
        (self.root / "gh" / f"comments_{story}.json").write_text(json.dumps(comments))

    def flag(self, name):
        (self.root / "gh" / name).write_text("")

    def unflag(self, name):
        (self.root / "gh" / name).unlink(missing_ok=True)

    def close(self, story):
        (self.root / "gh" / f"issue_{story}.json").write_text(json.dumps({"state": "closed", "title": "x"}))

    def env(self):
        return {**os.environ, "PATH": f"{(self.root / 'bin').as_posix()}{os.pathsep}{os.environ['PATH']}",
                "DISPATCHER_STATE_DIR": (self.root / "state").as_posix(),
                "HPW_SRC": (REPO_ROOT / "src").as_posix(), "HPW_PYTHON": shutil.which("python3") or shutil.which("python") or "python"}

    def script(self, *args):
        return subprocess.run([BASH, (self.root / "scripts" / "dispatcher-notify-retry.sh").as_posix(), *args, "--repo", "o/r"],
                              capture_output=True, text=True, env=self.env(), timeout=120)

    def scan(self, *args):
        return self.script("scan-job-blockers", *args)

    def run(self, *args):
        return self.script("run", *args)

    @property
    def state(self):
        path = self.root / "state" / "o" / "r" / "actionable-events"
        return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []

    @property
    def sent(self):
        path = self.root / "slack.log"
        return path.read_text().splitlines() if path.exists() else []

    def fail_slack(self, failing=True):
        path = self.root / "slack-fail"
        path.write_text("") if failing else path.unlink(missing_ok=True)


@pytest.fixture
def s(tmp_path):
    return Scan(tmp_path)


def only_event(s):
    assert len(s.state) == 1, s.state
    return s.state[0]


# ── AC 1, 2: the job's blockers register one po_blocker ─────────────────────

@needs_tools
@pytest.mark.parametrize("body,blocker_type,when", [
    (unknown_format(), "external", "a new Codex review result"),
    (max_requests(), "resource", "Codex posts a review result for head " + HEAD),
    (unresolved_threads(), "external", "the listed threads are resolved"),
])
def test_ac1_ac2_job_blocker_registers_one_po_blocker(s, body, blocker_type, when):
    s.comments(STORY, [c(body, at=T1)])
    proc = s.scan()
    assert proc.returncode == 0 and proc.stdout == "", proc.stderr
    event = only_event(s)
    assert event["event"] == "po_blocker" and event["issue"] == STORY and event["since"] == T1
    assert event["key"] == f"po_blocker:{STORY}:{T1}"
    context = json.loads(event["context"])
    assert context["blocker_type"] == blocker_type and context["unblocked_when"] == when
    assert context["state"].startswith(f"PR #900 at head {HEAD}") and context["cause"] == ""
    assert context["story_title"] == f"Story {STORY}" and context["issue_url"] == f"https://github.com/o/r/issues/{STORY}"
    assert f"Registered actionable event: po_blocker:{STORY}:{T1}" in proc.stderr


# ── AC 3: delivered in the same cycle, retried, bounded ─────────────────────

@needs_tools
def test_ac3_scan_registered_event_is_delivered_retried_and_bounded(s):
    s.comments(STORY, [c(unknown_format(), at=T1)])
    s.fail_slack()
    assert s.scan().returncode == 0
    codes = [s.run().returncode for _ in range(3)]
    assert codes == [0, 0, 1] and len(s.sent) == 3
    assert s.state[0]["delivered"] is False and s.state[0]["comment_posted"] is False or s.state[0]["failed_cycles"] == 3
    s.fail_slack(False)
    assert s.run().returncode == 0 and len(s.sent) == 4 and s.state[0]["delivered"] is True
    assert s.sent[-1].startswith("po_blocker ")


# ── AC 4: once per declaration ───────────────────────────────────────────────

@needs_tools
def test_ac4_scan_twice_and_after_delivery_sends_one_message(s):
    s.comments(STORY, [c(unknown_format(), at=T1)])
    assert s.scan().returncode == 0 and s.scan().returncode == 0
    assert len(s.state) == 1
    assert s.run().returncode == 0 and len(s.sent) == 1
    assert s.scan().returncode == 0 and s.run().returncode == 0
    assert len(s.state) == 1 and len(s.sent) == 1


@needs_tools
def test_ac4_a_later_job_blocker_is_a_new_event_and_only_the_latest_counts(s):
    s.comments(STORY, [c(unknown_format(), at=T0), c(max_requests(), at=T2)])
    s.scan()
    assert only_event(s)["since"] == T2  # the earlier declaration does not count
    s.comments(STORY, [c(unknown_format(), at=T0), c(max_requests(), at=T2), c(unknown_format(), at=T2.replace("12", "13"))])
    s.scan()
    assert [e["since"] for e in s.state] == [T2, "2026-11-08T13:00:00Z"]


# ── AC 5: resolved ───────────────────────────────────────────────────────────

@needs_tools
@pytest.mark.parametrize("name,extra,registers", [
    ("po-unblock-line", [c("looks fine\n  /unblock  \n", at=T2)], False),
    ("unblock-in-prose", [c("please /unblock this", at=T2)], True),
    ("unblock-from-another-account", [c("/unblock", login="someone", at=T2)], True),
    ("unblock-before-the-declaration", [c("/unblock", at=T0)], True),
])
def test_ac5_only_a_standalone_po_unblock_after_the_declaration_resolves(s, name, extra, registers):
    s.comments(STORY, [c(unknown_format(), at=T1), *extra])
    assert s.scan().returncode == 0
    assert (len(s.state) == 1) is registers, name


@needs_tools
def test_ac5_closed_story_registers_nothing(s):
    s.comments(STORY, [c(unknown_format(), at=T1)])
    s.close(STORY)
    assert s.scan().returncode == 0 and s.state == []


@needs_tools
def test_ac5_a_later_declaration_after_unblock_is_a_new_event(s):
    s.comments(STORY, [c(unknown_format(), at=T0), c("/unblock", at=T1), c(max_requests(), at=T2)])
    s.scan()
    assert only_event(s)["since"] == T2


@needs_tools
@pytest.mark.parametrize("name,pr_state", [("draft pr", "draft"), ("no pr", "none")])
def test_ac5_stories_of_draft_or_missing_prs_register_nothing(s, name, pr_state):
    s.comments(STORY, [c(unknown_format(), at=T1)])
    s.set_prs([STORY], draft=True) if pr_state == "draft" else s.set_prs([])
    assert s.scan().returncode == 0 and s.state == [], name


# ── AC 6: only the job's blockers ────────────────────────────────────────────

def dispatcher_blocker(kind="external"):
    return (f"## Blocked Declaration\nType: {kind}\nDeclared by: dispatcher (GitHub Actions)\nBlocks: #7 — x\n"
            "Unblocked when: y\nOwner: PO (@LauraMardones)\nState of in-progress work: z\n\n"
            "<!-- hpw:dispatcher action=blocker -->")


@needs_tools
@pytest.mark.parametrize("name,body", [
    ("dispatcher blocker", dispatcher_blocker()),
    ("job decision blocker", unknown_format().replace("Type: external", "Type: decision")
        .replace(job.ACTION_BLOCKER_EXTERNAL, "blocker-decision")),
    ("job conflict blocker", unknown_format().replace("Type: external", "Type: conflict")
        .replace(job.ACTION_BLOCKER_EXTERNAL, "blocker-conflict")),
    ("quote of a job blocker", "\n".join("> " + line for line in unknown_format().splitlines())),
    ("job text without its marker", unknown_format().replace(job.MARKER_PREFIX, "<!-- other")),
    ("marker only", f"{job.MARKER_PREFIX} action={job.ACTION_BLOCKER_EXTERNAL} head={HEAD} -->"),
])
def test_ac6_only_the_jobs_blockers_register(s, name, body):
    s.comments(STORY, [c(body, at=T1)])
    assert s.scan().returncode == 0 and s.state == [], name


@needs_tools
def test_ac6_a_forged_declaration_from_an_untrusted_account_registers_nothing(s):
    s.comments(STORY, [c(unknown_format(), login="stranger", author_association="NONE", at=T1)])
    assert s.scan().returncode == 0 and s.state == []


# ── AC 7: a failed read is contained ─────────────────────────────────────────

@needs_tools
def test_ac7_failed_comment_read_skips_that_story_and_a_later_scan_registers_it(s):
    s.set_prs([7, 8])
    s.comments(7, [c(unknown_format(7), at=T1)])
    s.comments(8, [c(max_requests(8), at=T1)])
    s.flag("comments_fail_7")
    proc = s.scan()
    assert proc.returncode == 0 and "cannot read the comments of #7" in proc.stderr
    assert [e["issue"] for e in s.state] == [8]
    s.unflag("comments_fail_7")
    assert s.scan().returncode == 0
    assert sorted(e["issue"] for e in s.state) == [7, 8]


@needs_tools
@pytest.mark.parametrize("flag", ["pr_list_fail", "issue_fail_7"])
def test_ac7_failed_pr_or_story_read_registers_nothing_and_exits_zero(s, flag):
    s.comments(STORY, [c(unknown_format(), at=T1)])
    s.flag(flag)
    proc = s.scan()
    assert proc.returncode == 0 and s.state == [] and "[NOTIFY] Warning:" in proc.stderr
    s.unflag(flag)
    assert s.scan().returncode == 0 and len(s.state) == 1


@needs_tools
def test_ac7_unreadable_job_format_is_a_warning_not_a_failure(s):
    s.comments(STORY, [c(unknown_format(), at=T1)])
    proc = subprocess.run(
        [BASH, (s.root / "scripts" / "dispatcher-notify-retry.sh").as_posix(), "scan-job-blockers", "--repo", "o/r"],
        capture_output=True, text=True, env={**s.env(), "HPW_PYTHON": "false"}, timeout=120)
    assert proc.returncode == 0 and "declaration format" in proc.stderr and s.state == []


# ── AC 8: dry run ────────────────────────────────────────────────────────────

@needs_tools
def test_ac8_dry_run_logs_writes_nothing_and_sends_nothing(s):
    s.comments(STORY, [c(unknown_format(), at=T1)])
    proc = s.scan("--dry-run")
    assert proc.returncode == 0
    assert f"[DRY RUN] Would register actionable event: po_blocker:{STORY}:{T1}" in proc.stderr
    assert s.state == [] and not (s.root / "state").exists() and s.sent == []
