#!/usr/bin/env python3
"""Parse backtrader_multi run artefacts into structured JSON.

Reads the latest run.log + trades.csv under <output-root>/<TICKER>/ and emits
a JSON object on stdout containing per-strategy metrics, ranking, risk flags,
and pointers to the generated files. Designed to be consumed by an LLM agent
that will turn it into a shareable markdown report.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd


_DONE_RE = re.compile(
    r"\[(?P<name>[^\]]+?)\] Done \| "
    r"Initial=(?P<initial>[\d.]+) \| "
    r"Final=(?P<final>[\d.]+) \| "
    r"Return=(?P<ret>-?[\d.]+)% \| "
    r"MaxDD=(?P<dd>-?[\d.]+)% \| "
    r"Sharpe=(?P<sharpe>None|-?nan|-?inf|-?[\d.eE+]+)"
)
# Sharpe values that mean "undefined" — treated as None in the payload.
_SHARPE_NULL_TOKENS = {"None", "nan", "-nan", "inf", "-inf"}
_SELECTED_RE = re.compile(
    r"Selected \d+ strategies \(seed=(?P<seed>\d+)\):\s*(?P<names>[^\n]+)"
)
_DATA_RE = re.compile(
    r"Data ready: \S+ \| "
    r"(?P<ds>\d{4}-\d{2}-\d{2}) → (?P<de>\d{4}-\d{2}-\d{2}) \| "
    r"(?P<bars>\d+) bars"
)
_STARTING_RE = re.compile(
    r"Starting \| ticker=(?P<ticker>[A-Za-z0-9.\-_]+).*?"
    r"start=(?P<start>\d{4}-\d{2}-\d{2}).*?"
    r"end=(?P<end>\d{4}-\d{2}-\d{2})"
)


def find_latest_run(output_root: Path, ticker: str) -> tuple[Path, str]:
    ticker_dir = output_root / ticker.upper()
    if not ticker_dir.exists():
        raise FileNotFoundError(f"No output directory for {ticker}: {ticker_dir}")
    log_files = sorted(ticker_dir.glob("*_run.log"))
    if not log_files:
        # fall back to any timestamped artefact
        candidates = sorted(ticker_dir.glob("*_trades.csv"))
        if not candidates:
            raise FileNotFoundError(f"No run artefacts found in {ticker_dir}")
        date_prefix = candidates[-1].name.split("_", 1)[0]
        return ticker_dir, date_prefix
    latest = log_files[-1]
    date_prefix = latest.name.replace("_run.log", "")
    return ticker_dir, date_prefix


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def parse_run_log(log_path: Path) -> dict:
    raw = log_path.read_text(encoding="utf-8", errors="replace")
    text = _strip_ansi(raw)

    # Locate the LAST `Starting | ticker=...` block. run_backtest.py opens the
    # log in append mode (run_backtest.py:62) so the same {YYYYMMDD}_run.log
    # file may contain entries from earlier runs of the same calendar day.
    # Everything after the last Starting marker is the current run; anything
    # before it is a prior run whose strategies must NOT leak into the report.
    last_starting_end = 0
    config_period = {"start": None, "end": None}
    ticker_from_log: str | None = None
    for sm in _STARTING_RE.finditer(text):
        last_starting_end = sm.end()
        ticker_from_log = sm.group("ticker")
        config_period = {"start": sm.group("start"), "end": sm.group("end")}

    # Slice: only parse the current-run section. If no Starting marker exists
    # (malformed log), fall back to the whole text rather than emit nothing.
    current_run_text = text[last_starting_end:] if last_starting_end else text

    strategies: list[dict] = []
    for m in _DONE_RE.finditer(current_run_text):
        sharpe_raw = m.group("sharpe")
        sharpe = None if sharpe_raw in _SHARPE_NULL_TOKENS else float(sharpe_raw)
        strategies.append({
            "name": m.group("name"),
            "initial": float(m.group("initial")),
            "final": float(m.group("final")),
            "total_return_pct": float(m.group("ret")),
            "max_drawdown_pct": float(m.group("dd")),
            "sharpe": sharpe,
        })

    seed: int | None = None
    selected_names: list[str] = []
    # Same scoping rule for the strategy-selection line — only the current run.
    sel = _SELECTED_RE.search(current_run_text) or _SELECTED_RE.search(text)
    if sel:
        seed = int(sel.group("seed"))
        selected_names = [n.strip() for n in sel.group("names").split(",")]

    data_window = {"start": None, "end": None, "bars": None}
    dm = _DATA_RE.search(current_run_text) or _DATA_RE.search(text)
    if dm:
        data_window = {
            "start": dm.group("ds"),
            "end": dm.group("de"),
            "bars": int(dm.group("bars")),
        }

    return {
        "strategies": strategies,
        "seed": seed,
        "selected_names": selected_names,
        "data_window": data_window,
        "config_period": config_period,
        "ticker_from_log": ticker_from_log,
    }


def parse_trades_csv(csv_path: Path) -> dict[str, dict]:
    if not csv_path.exists():
        return {}
    df = pd.read_csv(csv_path)
    out: dict[str, dict] = {}
    for strategy in df["strategy"].unique():
        sub = df[df["strategy"] == strategy]
        metrics = {row["metric"]: row["value"] for _, row in sub.iterrows()}
        # Coerce known numeric fields
        record = {
            "total": int(metrics.get("total_trades", 0) or 0),
            "won": int(metrics.get("won_trades", 0) or 0),
            "lost": int(metrics.get("lost_trades", 0) or 0),
            "win_rate": _maybe_float(metrics.get("win_rate")),
            "won_pnl_average": _maybe_float(metrics.get("won_pnl_average")),
            "won_pnl_max": _maybe_float(metrics.get("won_pnl_max")),
            "won_pnl_min": _maybe_float(metrics.get("won_pnl_min")),
            "lost_pnl_average": _maybe_float(metrics.get("lost_pnl_average")),
            "lost_pnl_max": _maybe_float(metrics.get("lost_pnl_max")),
            "lost_pnl_min": _maybe_float(metrics.get("lost_pnl_min")),
        }
        out[strategy] = record
    return out


def _maybe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def compute_flags(strategy: dict, trades: dict) -> list[str]:
    flags: list[str] = []
    sharpe = strategy.get("sharpe")
    if sharpe is None:
        flags.append("sharpe_unavailable")
    elif sharpe < 0:
        flags.append("negative_sharpe")
    if strategy.get("max_drawdown_pct", 0.0) > 20.0:
        flags.append("high_drawdown")
    total = trades.get("total", 0) if trades else 0
    if total == 0:
        flags.append("no_trades")
    elif total < 5:
        flags.append("too_few_trades")
    return flags


def rank_strategies(strategies: list[dict]) -> list[dict]:
    def key(s: dict):
        sharpe = s.get("sharpe")
        # None goes to the bottom of the ranking
        return -(sharpe if sharpe is not None else float("-inf"))
    return sorted(strategies, key=key)


def collect_artifacts(ticker_dir: Path, date_prefix: str) -> dict:
    return {
        "run_log": _path_or_none(ticker_dir / f"{date_prefix}_run.log"),
        "trades_csv": _path_or_none(ticker_dir / f"{date_prefix}_trades.csv"),
        "comparison_chart": _path_or_none(ticker_dir / f"{date_prefix}_comparison_chart.png"),
        "html_reports": [
            str(p) for p in sorted(ticker_dir.glob(f"{date_prefix}_*_report.html"))
        ],
    }


def _path_or_none(p: Path) -> str | None:
    return str(p) if p.exists() else None


def build_payload(output_root: Path, ticker: str) -> dict:
    ticker_dir, date_prefix = find_latest_run(output_root, ticker)
    log_path = ticker_dir / f"{date_prefix}_run.log"
    csv_path = ticker_dir / f"{date_prefix}_trades.csv"

    log_data = parse_run_log(log_path) if log_path.exists() else {
        "strategies": [], "seed": None, "selected_names": [],
        "data_window": {"start": None, "end": None, "bars": None},
        "config_period": {"start": None, "end": None},
        "ticker_from_log": None,
    }
    trades_by_strategy = parse_trades_csv(csv_path)

    enriched: list[dict] = []
    for s in log_data["strategies"]:
        tm = trades_by_strategy.get(s["name"], {})
        s_full = {**s, "trades": tm, "flags": compute_flags(s, tm)}
        enriched.append(s_full)

    ranked = rank_strategies(enriched)

    selected_names = log_data["selected_names"]
    completed_names = {s["name"] for s in ranked}
    missing_strategies = [n for n in selected_names if n not in completed_names]

    return {
        "ticker": ticker.upper(),
        "run_date": date_prefix,
        "config_period": log_data["config_period"],
        "data_window": log_data["data_window"],
        "seed": log_data["seed"],
        "n_strategies": len(ranked),
        "selected_names": selected_names,
        "missing_strategies": missing_strategies,
        "strategies_ranked": ranked,
        "best_strategy": ranked[0]["name"] if ranked else None,
        "artifacts": collect_artifacts(ticker_dir, date_prefix),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True, help="Stock ticker symbol")
    parser.add_argument(
        "--output-root", required=True,
        help="Directory containing the per-ticker subdirectories (e.g. ./output)",
    )
    args = parser.parse_args()

    root = Path(args.output_root).resolve()
    if not root.exists():
        print(f"Output root does not exist: {root}", file=sys.stderr)
        return 1

    payload = build_payload(root, args.ticker)
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
