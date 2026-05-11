#!/usr/bin/env bash
# backtrader-multi skill runner.
# Usage: bash run.sh <TICKER> <START_YYYY-MM-DD> <END_YYYY-MM-DD>
# Bootstraps a venv on first call, runs the bundled backtest, and relocates
# all artefacts into the caller's CWD under output/<TICKER>/.

set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: bash run.sh <TICKER> <START> <END> [--config <path>]" >&2
  exit 64
fi

TICKER_RAW="$1"
START="$2"
END="$3"
TICKER="$(echo "$TICKER_RAW" | tr '[:lower:]' '[:upper:]')"
shift 3

CONFIG_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      if [[ $# -lt 2 ]]; then
        echo "[backtrader-multi] --config requires a path argument" >&2
        exit 64
      fi
      CONFIG_FILE="$2"
      shift 2
      ;;
    *)
      echo "[backtrader-multi] unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$SKILL_DIR/project"
VENV_DIR="$SKILL_DIR/.venv"
REQS="$SKILL_DIR/requirements.txt"
DEFAULT_CONFIG="$PROJECT_DIR/config.yaml"
CONFIG_FILE="${CONFIG_FILE:-$DEFAULT_CONFIG}"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[backtrader-multi] config file not found: $CONFIG_FILE" >&2
  exit 66
fi

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
  --config "$CONFIG_FILE" \
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

# ------------------------------------------------------------------
# Override-config disposition
#   Success path: archive the staged config alongside the run's
#     artefacts as <RUN_DATE>_config.yaml, then remove the /tmp file.
#   Failure / no-output path: leave the staged file in place and emit
#     STAGED_CONFIG=<path> on stderr so the caller can inspect what
#     was actually attempted.
# ------------------------------------------------------------------
if [[ "$CONFIG_FILE" != "$DEFAULT_CONFIG" ]]; then
  if [[ $RC -eq 0 && $MOVED -gt 0 ]]; then
    cp "$CONFIG_FILE" "$TARGET_DIR/${TODAY}_config.yaml"
    rm -f "$CONFIG_FILE"
    echo "[backtrader-multi] Archived effective config to $TARGET_DIR/${TODAY}_config.yaml" >&2
  else
    echo "STAGED_CONFIG=$CONFIG_FILE" >&2
    echo "[backtrader-multi] Override config preserved at $CONFIG_FILE (run did not complete cleanly)" >&2
  fi
fi

if [[ $RC -ne 0 ]]; then
  echo "[backtrader-multi] Backtest exited with code $RC." >&2
  exit "$RC"
fi

# Emit a machine-readable line for the calling agent to pick up.
echo "OUTPUT_DIR=$TARGET_DIR"
echo "RUN_DATE=$TODAY"
