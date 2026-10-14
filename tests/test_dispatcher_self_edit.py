"""A session that edits scripts/dispatcher-invoke.sh must not derail the run (issue #345).

bash reads a script in pieces, by byte offset. The fake session below rewrites the
script in place (lines inserted and deleted before the current position) while the
dispatcher waits for it. The run must still finish with the code it started with,
and the ``exec bash "$0"`` tail call must start the rewritten file.
"""

from __future__ import annotations

import shutil

import pytest

from tests import dispatcher_fakes
from tests.dispatcher_fakes import HAVE_TOOLS, REPO_ROOT, Dispatcher

needs_tools = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
NEXT_STORY = 501

# In-place rewrite (same inode, as an editor tool does): 60 lines inserted at the top, a
# version marker that only a fresh `bash` start executes, and the first lines deleted.
REWRITE = r"""
if [[ ! -f "$MOCK_DIR/rewritten" ]]; then
    touch "$MOCK_DIR/rewritten"
    target="$MOCK_DIR/checkout/scripts/dispatcher-invoke.sh"
    {
        sed -n 1p "$target"
        echo 'echo started >> "$MOCK_DIR/versions.log"'
        for n in $(seq 1 60); do echo "# inserted line $n"; done
        sed '1,12d' "$target"
    } > "$MOCK_DIR/rewritten.sh"
    cat "$MOCK_DIR/rewritten.sh" > "$target"
fi
"""


@pytest.fixture
def d(tmp_path, monkeypatch):
    dispatcher = Dispatcher(tmp_path)
    checkout = dispatcher.mock / "checkout"
    shutil.copytree(REPO_ROOT / "scripts", checkout / "scripts")
    shutil.copytree(REPO_ROOT / ".claude" / "commands", checkout / ".claude" / "commands")
    monkeypatch.setattr(dispatcher_fakes, "INVOKE", checkout / "scripts" / "dispatcher-invoke.sh")
    return dispatcher


@needs_tools
def test_post_session_steps_run_when_the_session_rewrites_the_script(d):
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(900, STORY)
    d.on_session("implement", REWRITE)

    proc = d.run_issue(STORY)

    assert (d.mock / "rewritten").exists()  # the session did rewrite the running script
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No such file" not in proc.stderr and "command not found" not in proc.stderr
    assert d.usage_count == 1
    assert f"[HANDOFF] #{STORY}: handed to the Codex review loop" in proc.stdout


@needs_tools
def test_tail_call_starts_the_rewritten_script(d):
    d.add_story(STORY, "Ready for implementation")
    d.add_story(NEXT_STORY, "Ready for implementation")
    d.add_pr(900, STORY)
    d.add_pr(901, NEXT_STORY)
    d.on_session("implement", REWRITE)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}", f"implement {NEXT_STORY}"]
    assert d.usage_count == 2
    # The first process ran the original code; only the process the tail call started ran the marker.
    assert (d.mock / "versions.log").read_text().splitlines() == ["started"]
