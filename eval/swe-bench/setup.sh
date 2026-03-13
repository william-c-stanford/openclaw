#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EVAL_DIR="$ROOT_DIR/eval/swe-bench"
VENV_DIR="$EVAL_DIR/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[error] python3 is required but was not found in PATH." >&2
  exit 1
fi

if ! command -v pip3 >/dev/null 2>&1; then
  echo "[error] pip3 is required but was not found in PATH." >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[info] Creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  echo "[info] Reusing existing virtual environment at $VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

echo "[info] Installing Python dependencies"
python -m pip install --upgrade pip
python -m pip install -r "$EVAL_DIR/requirements.txt"

if ! command -v docker >/dev/null 2>&1; then
  echo "[error] Docker is required but was not found in PATH." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "[error] Docker is installed but not running (or not accessible to current user)." >&2
  exit 1
fi

if ! command -v openclaw >/dev/null 2>&1; then
  echo "[error] openclaw CLI is required but was not found in PATH." >&2
  exit 1
fi

echo

echo "✅ Setup complete."
echo "Activate the environment and run:"
echo "  source eval/swe-bench/.venv/bin/activate"
echo "  python eval/swe-bench/run.py"
