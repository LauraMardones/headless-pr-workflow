#!/usr/bin/env bash
# scripts/codex-worker.sh
#
# Entry point of the scheduled task in the signed-in Codex app on the PO's
# computer (issue #528, ADR-012 → Codex Plus amendment). It runs the local
# Codex worker, `hpw dispatcher codex-worker`, and passes its JSON line and
# exit code through unchanged. Setup, markers and recovery:
# docs/DISPATCHER-CONFIG.md → "Local Codex worker".
#
# Usage:
#   scripts/codex-worker.sh claim    --repo <owner/repo> [--owner <id>] [--run <id>] [--dry-run]
#   scripts/codex-worker.sh progress --repo <owner/repo> --issue <n> --attempt <k> --run <id> \
#                                    --branch codex/issue-<n>-<slug> --head <sha> [--pr <n>]
#   scripts/codex-worker.sh finish   --repo <owner/repo> --issue <n> --attempt <k> --run <id> \
#                                    (--result done --head <sha> | --result wait --reason <word> [--until <iso>])
#   scripts/codex-worker.sh status   --repo <owner/repo>
#
# Exit codes: 0 claimed, continued or written (the session may go on);
# 1 nothing to start, waiting, not the owner or not written; 2 invalid
# arguments; 3 GitHub could not be read (nothing was started).
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HPW_BIN="${HPW_BIN:-$SCRIPT_DIR/hpw}"

case "${1:-}" in
    claim|progress|finish|status) ;;
    *)
        echo "usage: scripts/codex-worker.sh claim|progress|finish|status --repo <owner/repo> [options]" >&2
        exit 2
        ;;
esac

exec "$HPW_BIN" dispatcher codex-worker "$@"
