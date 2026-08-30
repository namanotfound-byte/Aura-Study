#!/usr/bin/env bash
# Bootstraps and runs the AuraStudy backend. Safe to run repeatedly.
#
# Requires Python 3.11+ (the pinned dependencies in requirements.txt have no
# CVE-free release for Python 3.9/3.10 -- see requirements.txt's header
# comment). The easiest way to get a modern Python with no admin rights is
# Astral's `uv`:
#
#     curl -LsSf https://astral.sh/uv/install.sh | sh
#     uv python install 3.12
#
# If `uv` is on PATH (or installed at the default ~/.local/bin/uv), this
# script uses it automatically to provision Python 3.12 and to install
# dependencies. Otherwise it falls back to any python3.11+/python3 found on
# PATH, and refuses to build a venv on anything older.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

MIN_VERSION_HUMAN="3.11"

version_ok() {
  # $1 = path to a python interpreter. True if it's >= 3.11.
  "$1" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null
}

no_modern_python_error() {
  cat >&2 <<EOF
ERROR: No Python $MIN_VERSION_HUMAN+ interpreter found, and \`uv\` is not installed.

AuraStudy requires Python $MIN_VERSION_HUMAN or newer -- the pinned
dependencies in requirements.txt have no CVE-free release for Python
3.9/3.10, which is why this project moved off the older system Python.

The easiest fix: install \`uv\` (Astral's Python tool -- installs to your
home directory, no admin rights needed) and let it fetch a modern Python:

    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "\$HOME/.local/bin/env"   # or just restart your shell
    uv python install 3.12

Then re-run this script -- it will detect \`uv\` automatically and build
.venv on Python 3.12.

Alternatively, install any Python $MIN_VERSION_HUMAN+ yourself (pyenv,
python.org, etc.) and make sure it resolves as \`python3\` on your PATH.
EOF
  exit 1
}

stale_venv_error() {
  cat >&2 <<EOF
ERROR: .venv exists but is built on an unsupported Python version (< $MIN_VERSION_HUMAN).

Remove it and re-run this script so it can be rebuilt on a modern Python:

    rm -rf .venv
    ./run.sh

If you don't have Python $MIN_VERSION_HUMAN+ available yet, install \`uv\`
first (no admin rights needed):

    curl -LsSf https://astral.sh/uv/install.sh | sh
    uv python install 3.12
EOF
  exit 1
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
  elif [ -x "$HOME/.local/bin/uv" ]; then
    echo "$HOME/.local/bin/uv"
  fi
}

UV_BIN="$(find_uv || true)"

if [ ! -d ".venv" ]; then
  if [ -n "$UV_BIN" ]; then
    echo "Creating virtualenv at .venv on Python 3.12 (via uv) ..."
    "$UV_BIN" venv --python 3.12 .venv
  else
    PYBIN=""
    for cand in python3.13 python3.12 python3.11 python3; do
      if command -v "$cand" >/dev/null 2>&1 && version_ok "$(command -v "$cand")"; then
        PYBIN="$(command -v "$cand")"
        break
      fi
    done
    if [ -z "$PYBIN" ]; then
      no_modern_python_error
    fi
    echo "Creating virtualenv at .venv on $("$PYBIN" --version) ..."
    "$PYBIN" -m venv .venv
  fi
fi

# Guard against a stale venv left over from an older Python (e.g. a venv
# built before this project required 3.11+).
if ! version_ok ".venv/bin/python"; then
  stale_venv_error
fi

echo "Installing dependencies ..."
if [ -n "$UV_BIN" ]; then
  "$UV_BIN" pip install --python .venv/bin/python --quiet -r requirements.txt
else
  .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || true
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -f ".env" ]; then
  echo "No .env found, copying .env.example -> .env"
  cp .env.example .env
fi

echo "Starting AuraStudy on http://127.0.0.1:5055 ..."
exec .venv/bin/python -m server.app
