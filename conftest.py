from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config: object) -> None:
    """Redirect pytest tmp_path base to a dir outside the repo.

    Avoids two problems in sandboxed environments (e.g. Codex):
    1. The system temp directory may not be writable.
    2. A temp dir inside the repo causes git-detection to succeed for tests
       that create subdirectories expecting a non-git environment.

    Placing the base one level above the repo root sidesteps both issues.
    PYTEST_DEBUG_TEMPROOT takes precedence when set explicitly.
    """
    if os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return
    opts = getattr(config, "option", None)
    if opts is not None and not getattr(opts, "basetemp", None):
        # One level above the repo root — outside git working tree.
        basetemp = Path(__file__).parent.parent / ".pytest_tmp"
        basetemp.mkdir(parents=True, exist_ok=True)
        opts.basetemp = basetemp
