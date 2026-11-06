"""Run the real ``hpw dispatcher`` block against a bash ``gh`` fake (issue #432).

Shell harnesses put a fake ``hpw`` on ``HPW_BIN`` that hands ``hpw dispatcher ...``
to this script (``fake_hpw_route``). It runs the real block code with ``gh``
started as ``bash <fake gh>``: on Windows Python cannot start a bash script
directly (AGENTS.md → Running Tests), so the real ``scripts/hpw`` would not reach
the fake there. Only the transport differs from production.

Environment: ``HPW_FAKE_BASH`` (the bash to run the fake with) and
``HPW_FAKE_GH`` (the fake ``gh`` script).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def fake_hpw_route(fake_gh: Path, bash: str) -> str:
    """Bash lines for a fake ``hpw``: ``hpw dispatcher ...`` runs the real block on ``fake_gh``."""

    python = Path(sys.executable).as_posix()
    route = Path(__file__).resolve().as_posix()
    return (
        'if [[ "${1:-}" == dispatcher ]]; then\n'
        f'    HPW_FAKE_BASH="{Path(bash).as_posix()}" HPW_FAKE_GH="{fake_gh.as_posix()}" exec "{python}" "{route}" "$@"\n'
        "fi\n"
    )


def fake_state_route() -> str:
    """Bash lines for a fake ``hpw``: ``hpw dispatcher state ...`` runs the real Block 3 (issue #434).

    Block 3 reads and writes local files only, so it needs no ``gh`` fake.
    """

    python = Path(sys.executable).as_posix()
    return (
        'if [[ "${1:-} ${2:-}" == "dispatcher state" ]]; then\n'
        f'    shift; PYTHONPATH="{(REPO_ROOT / "src").as_posix()}" exec "{python}" -m headless_pr_workflow.dispatcher "$@"\n'
        "fi\n"
    )


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from headless_pr_workflow.dispatcher.cli import main as dispatcher_main
    from headless_pr_workflow.dispatcher.gateway import GhGateway

    command = (os.environ["HPW_FAKE_BASH"], os.environ["HPW_FAKE_GH"])
    # Git Bash expands the arguments a native process gives it as file patterns,
    # and an argument such as `labels[]=x` crashes it; real gh does no such thing.
    os.environ["MSYS"] = " ".join(filter(None, [os.environ.get("MSYS", ""), "noglob"]))
    if argv[:1] == ["dispatcher"]:
        argv = argv[1:]
    return dispatcher_main(argv, gateway_factory=lambda repo: GhGateway(repo, gh_command=command))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
