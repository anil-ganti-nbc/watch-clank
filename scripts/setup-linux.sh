#!/usr/bin/env bash
# One-time environment setup for Watch Clank on Linux/cloud.
# Mirrors the Windows setup steps in README.md — same venv, same deps, same
# Alembic-is-authoritative policy. Does not install a systemd unit or start
# anything; see systemd/README.md for that (deliberately separate — this
# script only prepares the environment, it does not deploy).
#
# Usage:
#   ./scripts/setup-linux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "=== Watch Clank Linux setup ==="
echo "Repository: $REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON_BIN not found. Install Python 3.12+ first." >&2
    exit 2
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

if [ ! -f ".env" ]; then
    echo "Copying .env.example -> .env (edit secrets/config before running)"
    cp .env.example .env
fi

mkdir -p data/snapshots data/logs

echo "Running Alembic migrations (schema is authoritative from migrations only)..."
python -m alembic upgrade head

chmod +x scripts/run_scheduled.sh

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit .env — set DATABASE_URL if not using the default local SQLite path,"
echo "     and DISCORD_EDITORIAL_WEBHOOK_URL / DISCORD_HEALTH_WEBHOOK_URL if desired."
echo "     Never commit .env."
echo "  2. Manual one-shot run (same path as scheduled): ./scripts/run_scheduled.sh"
echo "  3. For unattended scheduling, see scripts/systemd/README.md"
echo "  4. Dashboard: python -m app.serve --host 127.0.0.1 --port 8765"
