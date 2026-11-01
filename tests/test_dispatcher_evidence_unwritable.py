"""The PO is alerted once when required evidence is missing and the story cannot be written (issue #372).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``: comment writes on the story fail, reads work.
The delivery and resolution of the queued event are tested in
tests/test_dispatcher_notify_retry.sh.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tests.dispatcher_fakes import BASH, HAVE_TOOLS, REPO_ROOT, Dispatcher

pytestmark = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
PR = 900
HEAD = "a" * 40
CONFIRMATION = "Merge gate: policy=pass, head=aaaaaaa\n\n## Session Summary\nCommand: merge"
MANY = 99


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def pending(item: str, payload: str, head: str = "-") -> str:
    return subprocess.run(
        [BASH, "-c", f'source "{REPO_ROOT}/scripts/lib/evidence-pending.sh"; evidence_pending_comment "$@"',
         "_", item, head, payload], capture_output=True, text=True, check=True).stdout


def ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def events(d: Dispatcher) -> list[dict]:
    return [e for e in d.queued_events if e["event"] == "evidence_unwritable"]


def pending_confirmation_setup(d: Dispatcher, age_minutes: int, login: str | None = None) -> None:
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)
    kwargs = {"login": login} if login else {}
    d.add_comment(PR, pending("merge-confirmation", CONFIRMATION, HEAD), at=ago(age_minutes), **kwargs)
    d.fail_comment_writes(PR, MANY)
    d.fail_comment_writes(STORY, MANY)


def test_ac1_pending_item_older_than_15_minutes_and_unwritable_story_queues_one_event(d):
    pending_confirmation_setup(d, 20)
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    [event] = events(d)
    assert event["issue"] == STORY
    assert event["key"].startswith(f"evidence_unwritable:{STORY}:") and "merge-confirmation" in event["key"]
    assert f"[RESUME SKIP] #{STORY}: the merge-confirmation evidence on #{PR} has been missing for 20 min" in proc.stdout
    assert "PO alert queued" in proc.stdout
    assert event["delivered"] is True and len(d.slack_calls) == 1  # the drain between resume items (#349) sends it once


def test_ac2_pending_item_younger_than_15_minutes_queues_no_event(d):
    pending_confirmation_setup(d, 5)
    assert d.resume().returncode == 0
    assert events(d) == []


def test_ac3_a_later_run_with_the_same_item_queues_no_second_event(d):
    pending_confirmation_setup(d, 20)
    d.resume()
    first = events(d)
    d.resume()
    d.resume()
    assert events(d) == first and len(first) == 1


def test_ac2_writable_story_posts_the_348_marker_and_queues_no_event(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)
    d.add_comment(PR, pending("merge-confirmation", CONFIRMATION, HEAD), at=ago(60))
    d.fail_comment_writes(PR, MANY)
    assert d.resume().returncode == 0
    assert any("action=evidence-retry-failed item=merge-confirmation" in b for b in d.posted(STORY))
    assert events(d) == []


def test_ac1_without_a_pending_comment_the_time_runs_from_the_prs_merge(d):
    """merge-confirmation-missing: no confirmation and no pending text exist."""
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True, merged_at=ago(30))
    d._write(f"comments_{PR}.json", [])
    d.fail_comment_writes(STORY, MANY)
    assert d.resume().returncode == 0
    [event] = events(d)
    assert "merge-confirmation-missing" in event["key"] and ago(30)[:13] in event["key"]


def test_ac2_without_a_pending_comment_a_recent_merge_queues_no_event(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True, merged_at=ago(3))
    d._write(f"comments_{PR}.json", [])
    d.fail_comment_writes(STORY, MANY)
    assert d.resume().returncode == 0
    assert events(d) == []


def test_ac1_cleanup_summary_unrecorded_is_timed_from_the_merge(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True, merged_at=ago(40))
    d.fail("cleanup")
    d.fail_comment_writes(STORY, MANY)
    assert d.resume().returncode == 0
    [event] = events(d)
    assert "cleanup-summary-unrecorded" in event["key"]


def test_ac3_a_different_missing_item_on_the_same_story_is_a_separate_event(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True, merged_at=ago(40))
    d._write(f"comments_{PR}.json", [])
    d.fail_comment_writes(STORY, MANY)
    d.resume()
    d.add_comment(PR, CONFIRMATION)  # the confirmation is now there: cleanup follows and fails
    d.fail("cleanup")
    d.resume()
    items = {e["key"] for e in events(d)}
    assert len(items) == 2 and any("merge-confirmation-missing" in k for k in items) \
        and any("cleanup-summary-unrecorded" in k for k in items)


def test_ac7_a_pending_comment_of_another_account_starts_no_timer(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    d.add_comment(PR, pending("merge-owner", "<!-- hpw:dispatcher action=merge-owner -->", HEAD),
                  login="someone-else", at=ago(120))
    d.fail_comment_writes(PR, MANY)
    d.fail_comment_writes(STORY, MANY)
    d.resume()
    assert events(d) == []


def test_ac8_dry_run_registers_nothing(d):
    pending_confirmation_setup(d, 20)
    proc = d.resume("--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert events(d) == []
