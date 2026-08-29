#!/usr/bin/env bash
# Bootstraps and runs the AuraStudy backend. Safe to run repeatedly.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  echo "Creating virtualenv at .venv ..."
  python3 -m venv .venv
fi

echo "Installing dependencies ..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f ".env" ]; then
  echo "No .env found, copying .env.example -> .env"
  cp .env.example .env
fi

echo "Starting AuraStudy on http://127.0.0.1:5055 ..."
exec .venv/bin/python -m server.app
