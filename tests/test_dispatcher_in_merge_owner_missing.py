"""A story "Mergeable" whose head has no dispatcher merge owner (issue #406).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``. The merge script is a stub that logs to
``phases.log``; the ownership gate itself is tested with that script.
"""

from __future__ import annotations

import base64

import pytest

from tests.dispatcher_fakes import PO, Dispatcher, needs_tools

pytestmark = needs_tools

STORY = 500
PR = 900
HEAD = "a" * 40
NEW_HEAD = "b" * 40
BLOCKED = "## Blocked Declaration"
CONFLICTING = {"mergeable": False, "mergeable_state": "dirty"}


def owner_marker(head: str) -> str:
    return f"<!-- hpw:dispatcher action=merge-owner pr={PR} head={head} -->"


def missing_marker(head: str) -> str:
    return f"<!-- hpw:dispatcher action=merge-owner-missing pr={PR} head={head} -->"


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def blockers(d):
    return [b for b in d.posted(STORY) if b.startswith(BLOCKED)]


def po_events(d):
    return [e for e in d.queued_events if e["event"] == "po_blocker"]


@pytest.mark.parametrize("executor", ["claude-code-sonnet", "codex"])
def test_ac1_ac2_a_head_without_a_merge_owner_gets_one_blocker_and_no_merge(d, executor):
    d.add_story(STORY, "Mergeable", executor=executor)
    d.add_pr(PR, STORY, head=NEW_HEAD, owned=False)
    d.add_comment(PR, owner_marker(HEAD))  # evidence for an older head does not count

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == [] and d.sessions == [] and d.status(STORY) == "Mergeable"
    assert f"[RESUME SKIP] #{STORY}: PR #{PR} head {NEW_HEAD[:7]} has no merge-owner evidence; blocker posted" in proc.stdout
    [blocker] = blockers(d)
    assert "Type: resource" in blocker
    assert f"the merge owner is missing for head {NEW_HEAD[:7]} on PR #{PR}" in blocker
    assert "not pushed by a dispatcher session" in blocker
    assert f"Unblocked when: the PO merges PR #{PR} by hand with /merge {PR} and then runs /cleanup {PR}" in blocker
    assert "docs/TAKEOVER-RULES.md → Merge Takeover" in blocker
    assert missing_marker(NEW_HEAD) in blocker
    assert len(po_events(d)) == 1
    assert not any("action=merge-attempt" in b for b in d.posted(STORY))
    assert not [e for e in d.queued_events if e["event"] == "dispatcher_error"]

    proc = d.resume()  # a later run on the same head posts nothing more
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == [] and len(blockers(d)) == 1 and len(po_events(d)) == 1
    assert f"PR #{PR} head {NEW_HEAD[:7]} has no merge-owner evidence; the blocker is already posted" in proc.stdout

    d.add_comment(STORY, "/unblock", login=PO)  # still the same head
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == [] and len(blockers(d)) == 1


def test_ac3_a_head_with_a_merge_owner_marker_goes_to_the_merge_script(d):
    d.add_story(STORY, "Mergeable", executor="claude-code-sonnet")
    d.add_pr(PR, STORY, head=NEW_HEAD, owned=False)
    d.add_comment(PR, owner_marker(NEW_HEAD))

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases[0] == f"merge {PR}"
    assert blockers(d) == []


def test_ac3_a_pending_copy_for_the_head_is_posted_and_the_merge_script_runs(d):
    d.add_story(STORY, "Mergeable", executor="claude-code-sonnet")
    d.add_pr(PR, STORY, head=NEW_HEAD, owned=False)
    payload = base64.b64encode(owner_marker(NEW_HEAD).encode()).decode()
    d.add_comment(PR, f"Dispatcher: pending.\n\n<!-- hpw:dispatcher action=evidence-pending item=merge-owner head={NEW_HEAD} payload={payload} -->")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases[0] == f"merge {PR}"
    assert blockers(d) == []
    assert any(owner_marker(NEW_HEAD) in b for b in d.posted(PR))


def test_ac3_a_conflicting_pr_gets_no_merge_owner_blocker(d):
    d.add_story(STORY, "Mergeable", executor="codex")
    d.add_pr(PR, STORY, head=NEW_HEAD, owned=False, **CONFLICTING)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == []
    assert not any(missing_marker(NEW_HEAD) in b for b in d.posted(STORY))
