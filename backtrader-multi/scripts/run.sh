#!/usr/bin/env bash
# backtrader-multi skill runner.
# Usage: bash run.sh <TICKER> <START_YYYY-MM-DD> <END_YYYY-MM-DD>
# Bootstraps a venv on first call, runs the bundled backtest, and relocates
# all artefacts into the caller's CWD under output/<TICKER>/.

set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: bash run.sh <TICKER> <START> <END>" >&2
  exit 64
fi

TICKER_RAW="$1"
START="$2"
END="$3"
TICKER="$(echo "$TICKER_RAW" | tr '[:lower:]' '[:upper:]')"

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$SKILL_DIR/project"
VENV_DIR="$SKILL_DIR/.venv"
REQS="$SKILL_DIR/requirements.txt"

CALLER_CWD="$(pwd)"
TARGET_DIR="$CALLER_CWD/output/$TICKER"

# ------------------------------------------------------------------
# venv bootstrap (idempotent)
# ------------------------------------------------------------------
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[backtrader-multi] First run: creating venv at $VENV_DIR" >&2
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[backtrader-multi] python3 not found on PATH. Install Python 3.9+ and retry." >&2
    exit 1
  fi
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip
  echo "[backtrader-multi] Installing dependencies (this may take 1-2 minutes)..." >&2
  "$VENV_DIR/bin/pip" install --quiet -r "$REQS"
  echo "[backtrader-multi] Dependencies installed." >&2
fi

PYBIN="$VENV_DIR/bin/python"

# ------------------------------------------------------------------
# Run backtest
#   run_backtest.py writes outputs to <project>/output/<TICKER>/<YYYYMMDD>_*
#   so we capture <YYYYMMDD> here, run, then relocate everything to the
#   caller's CWD and clean up the bundled scratch directories.
# ------------------------------------------------------------------
TODAY="$(date +%Y%m%d)"
BUNDLED_OUTPUT="$PROJECT_DIR/output/$TICKER"

set +e
"$PYBIN" "$PROJECT_DIR/run_backtest.py" \
  --config "$PROJECT_DIR/config.yaml" \
  --ticker "$TICKER" \
  --start "$START" \
  --end "$END"
RC=$?
set -e

# ------------------------------------------------------------------
# Relocate today's artefacts into the caller's CWD
# ------------------------------------------------------------------
MOVED=0
if [[ -d "$BUNDLED_OUTPUT" ]]; then
  shopt -s nullglob
  for f in "$BUNDLED_OUTPUT/${TODAY}"_*; do
    if [[ $MOVED -eq 0 ]]; then
      mkdir -p "$TARGET_DIR"
    fi
    mv -f "$f" "$TARGET_DIR/"
    MOVED=$((MOVED + 1))
  done
  shopt -u nullglob

  # Best-effort cleanup of the bundled scratch dirs. rmdir refuses to remove
  # non-empty directories, so this only succeeds when no other artefacts (or
  # other tickers) remain.
  rmdir "$BUNDLED_OUTPUT" 2>/dev/null || true
  rmdir "$PROJECT_DIR/output" 2>/dev/null || true
fi

if [[ $MOVED -gt 0 ]]; then
  echo "[backtrader-multi] Relocated $MOVED artefact(s) to $TARGET_DIR" >&2
fi

if [[ $RC -ne 0 ]]; then
  echo "[backtrader-multi] Backtest exited with code $RC." >&2
  exit "$RC"
fi

# Emit a machine-readable line for the calling agent to pick up.
echo "OUTPUT_DIR=$TARGET_DIR"
echo "RUN_DATE=$TODAY"
