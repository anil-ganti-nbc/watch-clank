#!/usr/bin/env bash
# Single finite Casio Japan pipeline run for systemd/cron/manual use.
# Linux/cloud counterpart of scripts/run_scheduled.ps1 — same exit-code
# contract, same log files, same DATABASE_URL-forcing approach, so the
# core pipeline (scripts/run_pipeline.py) requires zero source changes to
# run on either platform.
#
# Exit codes (from Python, preserved by this wrapper):
#   0 = SUCCESS, PARTIAL, ZERO_ITEMS, BLOCKED, SKIPPED_OVERLAP (nonfatal)
#   1 = pipeline failure (FAILED)
#   2 = setup / configuration / fatal exception
#   3 = migration / database failure (reserved)
#
# Usage:
#   ./scripts/run_scheduled.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$REPO_ROOT/data/logs"
WRAPPER_LOG="$LOG_DIR/scheduled-wrapper.log"
PYTHON_LOG="$LOG_DIR/scheduled-python.log"
SQLITE_PATH="$REPO_ROOT/data/watch_clank.db"

mkdir -p "$LOG_DIR"

log() {
    local ts
    ts="$(date -u +"%Y-%m-%dT%H:%M:%S%z")"
    echo "$ts $1" | tee -a "$WRAPPER_LOG"
}

log "START pid=$$ repo=$REPO_ROOT"
log "DB_PATH=$SQLITE_PATH"
log "VENV_PYTHON=$VENV_PYTHON"
log "PWD_BEFORE=$(pwd)"

if [ ! -x "$VENV_PYTHON" ]; then
    log "ERROR: venv python missing at $VENV_PYTHON"
    exit 2
fi

export PYTHONPATH="$REPO_ROOT"
export DATABASE_URL="sqlite:///$SQLITE_PATH"

cd "$REPO_ROOT" || { log "ERROR: cannot cd to $REPO_ROOT"; exit 2; }
log "CWD=$(pwd)"
log "INVOKING: $VENV_PYTHON -m scripts.run_pipeline --scheduled"

"$VENV_PYTHON" -m scripts.run_pipeline --scheduled >>"$PYTHON_LOG" 2>&1
code=$?

log "END exit_code=$code"
exit $code
