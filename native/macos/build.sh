#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
test -x .venv/bin/pyinstaller || { echo "Install PyInstaller in .venv first" >&2; exit 1; }
rm -rf native/macos/build native/macos/dist

# Provenance: default to the real git SHA (short form, "-dirty" appended if
# the working tree has uncommitted changes at build time) rather than an
# opaque "local development build" label -- lets the running app's header
# be trusted to say exactly what code it's running. An explicit
# WATCH_CLANK_PACKAGED_REVISION still overrides this (e.g. CI supplying its
# own build identifier). Written to a bundled resource file, not just an
# env var, because launching via Finder/`open` does not forward the
# shell's exported environment to the new process -- see launcher.py's
# build_revision().
if [ -z "${WATCH_CLANK_PACKAGED_REVISION:-}" ]; then
  if git rev-parse --git-dir >/dev/null 2>&1; then
    sha="$(git rev-parse --short HEAD)"
    if [ -n "$(git status --porcelain=v1)" ]; then
      sha="${sha}-dirty"
    fi
    WATCH_CLANK_PACKAGED_REVISION="$sha"
  else
    WATCH_CLANK_PACKAGED_REVISION="local development build"
  fi
fi
export WATCH_CLANK_PACKAGED_REVISION

mkdir -p native/macos/generated
printf '%s' "$WATCH_CLANK_PACKAGED_REVISION" > native/macos/generated/build_revision.txt
echo "Packaging revision: $WATCH_CLANK_PACKAGED_REVISION"

.venv/bin/pyinstaller --clean --noconfirm --workpath native/macos/build --distpath native/macos/dist "native/macos/Watch Clank.spec"
