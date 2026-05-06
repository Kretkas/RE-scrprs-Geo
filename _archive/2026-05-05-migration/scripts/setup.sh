#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/bin/python3.13}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python not found or not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt

# Playwright/Patchright browsers are external browser binaries, not pip packages.
.venv/bin/python -m playwright install chromium
.venv/bin/python -m patchright install chromium

PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --dry-run
