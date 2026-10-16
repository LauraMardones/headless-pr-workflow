"""Fakes for tests of scripts/dispatcher-merge.sh and scripts/dispatcher-cleanup.sh (issue #264).

A fake ``gh`` serves JSON files from a mock directory (the file name is the
endpoint without its query, ``/`` as ``_``) and applies ``--jq`` with the real
``jq``; POSTs are logged. A fake ``hpw`` and a fake merge gate print canned
output and log their arguments. ``session-summary.sh`` is the real one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.script_tools import BASH, missing_tools, requires, script_path, windows_env

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = ("bash", "jq", "git", "python3")
HAVE_TOOLS = not missing_tools(TOOLS)
needs_tools = requires(*TOOLS)

FAKE_GH = r"""#!/usr/bin/env bash
echo "gh $*" >> "$MOCK_DIR/gh.log"
if [[ "${1:-}" != "api" ]]; then  # e.g. the real hpw's `gh pr view`; gh_fail_stderr is its stderr
    [[ -f "$MOCK_DIR/gh_fail_stderr" ]] && cat "$MOCK_DIR/gh_fail_stderr" >&2
    exit 1
fi
shift
endpoint="" jq_expr="" method=GET body=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --jq) jq_expr="$2"; shift 2 ;;
        -X) method="$2"; shift 2 ;;
        -f) case "$2" in body=*) body="${2#body=}" ;; esac; shift 2 ;;
        --paginate) shift ;;
        *) [[ -z "$endpoint" ]] && endpoint="$1"; shift ;;
    esac
done
if [[ "$endpoint" == "graphql" ]]; then
    [[ -f "$MOCK_DIR/graphql_fail" ]] && { echo "HTTP 403: GraphQL is not available" >&2; exit 1; }
    echo '{"data":{"viewer":{"login":"bot"}}}'; exit 0
fi
if [[ "$method" == "POST" ]]; then
    [[ -f "$MOCK_DIR/post_fail" ]] && { echo "HTTP 500" >&2; exit 1; }
    if [[ -f "$MOCK_DIR/post_fail_times" ]]; then  # fail the first N posts, then work
        n=$(cat "$MOCK_DIR/post_fail_times")
        if (( n > 0 )); then echo $((n - 1)) > "$MOCK_DIR/post_fail_times"; echo "HTTP 500" >&2; exit 1; fi
    fi
    echo "POST $endpoint" >> "$MOCK_DIR/posts.log"
    printf '%s' "$body" > "$MOCK_DIR/post_$(echo "${endpoint%%\?*}" | tr / _).body"
    printf '%s\n---\n' "$body" >> "$MOCK_DIR/post_bodies.log"
    exit 0
fi
file="$MOCK_DIR/$(echo "${endpoint%%\?*}" | tr / _).json"
[[ -f "$file" ]] || { echo "fake gh: no fixture for $endpoint" >&2; exit 1; }
if [[ -n "$jq_expr" ]]; then jq -r "$jq_expr" "$file"; else cat "$file"; fi
"""

# Prints $MOCK_DIR/hpw_<subcommand>.json, writes hpw_<subcommand>.stderr to stderr
# and exits with hpw_<subcommand>.rc (default 0).
FAKE_HPW = r"""#!/usr/bin/env bash
echo "hpw $*" >> "$MOCK_DIR/hpw.log"
sub="$1"
[[ -f "$MOCK_DIR/hpw_$sub.json" ]] && cat "$MOCK_DIR/hpw_$sub.json"
[[ -f "$MOCK_DIR/hpw_$sub.stderr" ]] && cat "$MOCK_DIR/hpw_$sub.stderr" >&2
exit "$(cat "$MOCK_DIR/hpw_$sub.rc" 2>/dev/null || echo 0)"
"""

# Stands in for scripts/merge-gate-summary: prints gate_line, exits with gate_rc.
FAKE_GATE = r"""#!/usr/bin/env python3
import os, sys
mock = os.environ["MOCK_DIR"]
open(mock + "/gate.log", "a").write("gate " + " ".join(sys.argv[1:]) + "\n")
sys.stdout.write(open(mock + "/gate_line").read())
rc = mock + "/gate_rc"
sys.exit(int(open(rc).read()) if os.path.exists(rc) else 0)
"""


class PhaseHarness:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.mock = tmp / "mock"
        self.mock.mkdir()
        for name, text in (("gh", FAKE_GH), ("hpw", FAKE_HPW), ("gate", FAKE_GATE)):
            path = self.bin / name
            path.write_text(text, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        for name in ("gh.log", "hpw.log", "gate.log", "posts.log"):
            (self.mock / name).write_text("")
        self.cwd = tmp / "repo"
        self.cwd.mkdir()

    def fixture(self, endpoint: str, value) -> None:
        name = endpoint.replace("/", "_") + ".json"
        (self.mock / name).write_text(json.dumps(value), encoding="utf-8", newline="\n")

    def set(self, name: str, text: str) -> None:
        (self.mock / name).write_text(text, encoding="utf-8", newline="\n")

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        return subprocess.run(["git", *args], cwd=self.cwd, env=env, capture_output=True, text=True,
                              check=True).stdout

    def run(self, script: str, *args: str, env_overrides: dict[str, str] | None = None):
        env = {
            "HOME": self.tmp.as_posix(),
            "PATH": script_path(self.bin.as_posix()),
            "MOCK_DIR": self.mock.as_posix(),
            "COMMENT_RETRY_DELAY": "0",
            "HPW_BIN": (self.bin / "hpw").as_posix(),
            "MERGE_GATE_SCRIPT": (self.bin / "gate").as_posix(),
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        env.update(windows_env())
        env.update(env_overrides or {})
        return subprocess.run([BASH, (REPO_ROOT / "scripts" / script).as_posix(), *args], cwd=self.cwd, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=120)

    def log(self, name: str) -> list[str]:
        return (self.mock / name).read_text(encoding="utf-8").splitlines()

    @property
    def posts(self) -> list[str]:
        return self.log("posts.log")

    def body(self, endpoint: str) -> str:
        return (self.mock / f"post_{endpoint.replace('/', '_')}.body").read_text(encoding="utf-8")
