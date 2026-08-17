#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
test -x .venv/bin/pyinstaller || { echo "Install PyInstaller in .venv first" >&2; exit 1; }
rm -rf native/macos/build native/macos/dist
WATCH_CLANK_PACKAGED_REVISION="${WATCH_CLANK_PACKAGED_REVISION:-local development build}" \
  .venv/bin/pyinstaller --clean --noconfirm --workpath native/macos/build --distpath native/macos/dist "native/macos/Watch Clank.spec"
