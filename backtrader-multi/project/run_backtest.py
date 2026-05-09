#!/usr/bin/env python3
"""
多策略量化回测系统 — 命令行入口。

用法示例：
    python run_backtest.py --ticker SNDK --n-combos 2 --seed 42
    python run_backtest.py --ticker AAPL --log-level DEBUG --start 2025-01-01 --end 2026-01-28
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import yaml


# ------------------------------------------------------------------ #
#  日志初始化                                                          #
# ------------------------------------------------------------------ #

ANSI_COLORS = {
    "DEBUG":   "\033[94m",   # 蓝
    "INFO":    "\033[92m",   # 绿
    "WARNING": "\033[93m",   # 黄
    "ERROR":   "\033[91m",   # 红
    "RESET":   "\033[0m",
}


class _ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = ANSI_COLORS.get(record.levelname, "")
        reset = ANSI_COLORS["RESET"]
        record.levelname = f"{color}{record.levelname:<7}{reset}"
        return super().format(record)


def setup_logging(
    level_str: str,
    log_path: Path | None,
) -> None:
    level = getattr(logging, level_str.upper(), logging.INFO)

    handlers: list[logging.Handler] = []

    # 控制台（彩色）
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(
        _ColorFormatter(
            fmt="%(levelname)s %(name)s — %(message)s",
        )
    )
    handlers.append(ch)

    # 文件（纯文本）
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handlers.append(fh)

    logging.basicConfig(level=level, handlers=handlers, force=True)

    # 压制 yfinance / urllib3 的冗余输出
    for noisy in ("yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ------------------------------------------------------------------ #
#  CLI 参数解析                                                        #
# ------------------------------------------------------------------ #

def _validate_config(config: dict) -> None:
    """校验 YAML 配置结构，缺失必填字段时抛出友好错误。"""
    required_paths = [
        ("backtest", "start"),
        ("backtest", "end"),
        ("backtest", "cash"),
        ("backtest", "commission"),
        ("random", "seed"),
        ("random", "n_combos"),
    ]
    for keys in required_paths:
        node = config
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                raise ValueError(
                    f"Config missing required field: {'.'.join(str(k) for k in keys)}"
                )
            node = node[k]

    pool = config.get("strategy_pool")
    if not isinstance(pool, list) or not pool:
        raise ValueError("Config 'strategy_pool' must be a non-empty list")
    for entry in pool:
        for f in ("name", "class", "params"):
            if f not in entry:
                raise ValueError(
                    f"strategy_pool entry missing field '{f}': {entry}"
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="多策略量化回测系统",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="YAML 配置文件路径",
    )
    parser.add_argument(
        "--ticker", required=True,
        help="股票代码，如 SNDK / AAPL / 0700.HK",
    )
    parser.add_argument(
        "--n-combos", type=int, default=None,
        help="随机抽取策略数量（覆盖配置文件）",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="随机种子（覆盖配置文件）",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别（覆盖配置文件）",
    )
    parser.add_argument(
        "--start", default=None,
        help="回测开始日期 YYYY-MM-DD（覆盖配置文件）",
    )
    parser.add_argument(
        "--end", default=None,
        help="回测结束日期 YYYY-MM-DD（覆盖配置文件）",
    )
    return parser.parse_args()


# ------------------------------------------------------------------ #
#  主函数                                                              #
# ------------------------------------------------------------------ #

def main() -> int:
    args = parse_args()

    # ---- 加载配置 ----
    config_path = Path(args.config)
    if not config_path.exists():
        # 尝试相对于脚本所在目录
        config_path = Path(__file__).parent / args.config
    if not config_path.exists():
        print(f"[ERROR] Config file not found: {args.config}", file=sys.stderr)
        return 1

    with config_path.open(encoding="utf-8") as f:
        config: dict = yaml.safe_load(f)

    try:
        _validate_config(config)
    except ValueError as exc:
        print(f"[ERROR] Invalid config: {exc}", file=sys.stderr)
        return 1

    # ---- 合并 CLI 覆盖值 ----
    ticker   = args.ticker.upper()
    seed     = args.seed     if args.seed     is not None else config["random"].get("seed", 42)
    n_combos = args.n_combos if args.n_combos is not None else config["random"].get("n_combos", 2)
    log_level = (
        args.log_level
        if args.log_level is not None
        else config.get("logging", {}).get("level", "INFO")
    )
    start = args.start or config["backtest"]["start"]
    end   = args.end   or config["backtest"]["end"]

    # ---- 输出目录 ----
    today      = date.today().strftime("%Y%m%d")
    output_dir = Path(__file__).parent / "output" / ticker
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- 日志 ----
    to_file  = config.get("logging", {}).get("to_file", True)
    log_path = (output_dir / f"{today}_run.log") if to_file else None
    setup_logging(log_level, log_path)

    logger = logging.getLogger("run_backtest")
    logger.info(
        f"Starting | ticker={ticker} | n_combos={n_combos} | seed={seed} | "
        f"start={start} | end={end} | log_level={log_level}"
    )

    # ---- 回测引擎 ----
    # sys.path 确保 strategies/ 包可被 engine.py 内部 import
    sys.path.insert(0, str(Path(__file__).parent))

    from core.engine   import BacktestEngine
    from core.reporter import Reporter

    engine = BacktestEngine(
        config=config,
        ticker=ticker,
        seed=seed,
        n_combos=n_combos,
        start=start,
        end=end,
    )

    try:
        results = engine.run()
    except ValueError as exc:
        logger.error(f"Backtest failed: {exc}")
        return 1

    if not results:
        logger.error("No results returned from engine.")
        return 1

    # ---- 报告 ----
    reporter = Reporter()
    reporter.print_summary(results)
    reporter.save_csv(results, output_dir)
    reporter.save_comparison_chart(results, output_dir)
    reporter.save_html_report(results, output_dir)

    logger.info(f"All outputs saved to: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
