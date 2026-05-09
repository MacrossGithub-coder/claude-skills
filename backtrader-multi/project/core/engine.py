"""
多策略回测引擎。

职责：
  1. 用固定随机种子从 strategy_pool 中随机抽取 n_combos 个变体
  2. 每个变体在独立 bt.Cerebro 实例中运行，互不干扰
  3. 收集每个变体的结果（equity_curve, analyzers, trades）
  4. 返回 list[RunResult]
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import backtrader as bt
import pandas as pd

from .data_loader import download_df

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  结果数据类                                                          #
# ------------------------------------------------------------------ #

@dataclass
class RunResult:
    name:         str
    weight:       float
    params:       dict
    initial_value: float
    final_value:  float
    equity_curve: pd.Series          # index=date, value=portfolio_value
    sharpe:       float | None
    max_drawdown: float              # 百分比，如 15.3 表示 15.3%
    total_return: float              # 小数，如 0.25 表示 25%
    annual_return: float             # 小数
    trades_df:    pd.DataFrame       # 每笔交易明细
    actual_start: date
    actual_end:   date
    price_series: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))  # 收盘价
    analyzers_raw: dict = field(default_factory=dict)


# ------------------------------------------------------------------ #
#  资金曲线采集器（Observer）                                          #
# ------------------------------------------------------------------ #

class _EquityObserver(bt.Observer):
    """每个 bar 记录一次组合市值，供回测结束后提取曲线。"""
    lines = ("portfolio_value",)
    plotinfo = dict(plot=False)

    def next(self):
        self.lines.portfolio_value[0] = self._owner.broker.getvalue()


# ------------------------------------------------------------------ #
#  引擎                                                               #
# ------------------------------------------------------------------ #

class BacktestEngine:
    def __init__(
        self,
        config:   dict,
        ticker:   str,
        seed:     int,
        n_combos: int,
        start:    str | None = None,
        end:      str | None = None,
    ) -> None:
        self.config   = config
        self.ticker   = ticker.upper()
        self.seed     = seed
        self.n_combos = n_combos
        self.start    = start  or config["backtest"]["start"]
        self.end      = end    or config["backtest"]["end"]

    # ---------------------------------------------------------------- #

    def _select_strategies(self) -> list[dict]:
        """用固定种子从 strategy_pool 随机抽取 n_combos 个变体。"""
        pool = self.config["strategy_pool"]
        rng  = random.Random(self.seed)

        if self.n_combos >= len(pool):
            selected = pool[:]
        else:
            selected = rng.sample(pool, self.n_combos)

        logger.info(
            f"Selected {len(selected)} strategies (seed={self.seed}): "
            + ", ".join(s["name"] for s in selected)
        )
        return selected

    # ---------------------------------------------------------------- #

    def _resolve_strategy_class(self, class_name: str):
        """根据类名字符串返回策略类。"""
        from strategies import MaRsiStrategy  # lazy import 避免循环
        registry = {
            "MaRsiStrategy": MaRsiStrategy,
        }
        cls = registry.get(class_name)
        if cls is None:
            raise ValueError(
                f"Unknown strategy class '{class_name}'. "
                f"Available: {list(registry.keys())}"
            )
        return cls

    # ---------------------------------------------------------------- #

    def _run_single(
        self,
        strategy_cfg: dict,
        feed:         bt.feeds.PandasData,
        actual_start: date,
        actual_end:   date,
    ) -> RunResult:
        """为单个策略创建独立 Cerebro 并运行。"""
        bt_cfg  = self.config["backtest"]
        name    = strategy_cfg["name"]
        weight  = strategy_cfg.get("weight", 1.0)
        params  = dict(strategy_cfg.get("params", {}))
        cls     = self._resolve_strategy_class(strategy_cfg["class"])

        logger.info(f"[{name}] Starting backtest ...")

        cerebro = bt.Cerebro()
        cerebro.broker.setcash(bt_cfg["cash"])
        cerebro.broker.setcommission(commission=bt_cfg["commission"])
        cerebro.adddata(feed)

        # 注入 strategy_name 参数（BaseSignalStrategy.params 定义）
        params["strategy_name"] = name
        cerebro.addstrategy(cls, **params)

        # Analyzers
        cerebro.addanalyzer(
            bt.analyzers.SharpeRatio_A, _name="sharpe",
            timeframe=bt.TimeFrame.Days, compression=1,
        )
        cerebro.addanalyzer(bt.analyzers.DrawDown,  _name="drawdown")
        cerebro.addanalyzer(bt.analyzers.Returns,   _name="returns")
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
        cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="time_return",
                            timeframe=bt.TimeFrame.Days)

        # 资金曲线 Observer
        cerebro.addobserver(_EquityObserver)

        initial_value = cerebro.broker.getvalue()
        results = cerebro.run()
        strat   = results[0]

        final_value = cerebro.broker.getvalue()

        # ---- 提取分析器结果 ----
        sharpe_ana  = strat.analyzers.sharpe.get_analysis()
        dd_ana      = strat.analyzers.drawdown.get_analysis()
        ret_ana     = strat.analyzers.returns.get_analysis()
        trades_ana  = strat.analyzers.trades.get_analysis()
        tr_ana      = strat.analyzers.time_return.get_analysis()

        sharpe_val = sharpe_ana.get("sharperatio")

        max_dd_pct = 0.0
        if dd_ana.get("max") and dd_ana["max"].get("drawdown") is not None:
            max_dd_pct = float(dd_ana["max"]["drawdown"])

        total_ret = (final_value - initial_value) / initial_value if initial_value > 0 else 0.0
        days      = (actual_end - actual_start).days
        years     = days / 365.25 if days > 0 else 1.0
        annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 and total_ret > -1 else 0.0

        # ---- 资金曲线 ----
        equity_curve = self._extract_equity_curve(strat, tr_ana, initial_value)

        # ---- 交易明细 ----
        trades_df = self._extract_trades(trades_ana, name)

        # ---- 收盘价序列 ----
        price_series = self._extract_price_series(strat)

        # ---- show_plot ----
        if params.get("show_plot", False):
            cerebro.plot(iplot=False, show=True)

        logger.info(
            f"[{name}] Done | "
            f"Initial={initial_value:.2f} | Final={final_value:.2f} | "
            f"Return={total_ret:.2%} | MaxDD={max_dd_pct:.2f}% | "
            f"Sharpe={sharpe_val}"
        )

        return RunResult(
            name=name,
            weight=weight,
            params=params,
            initial_value=initial_value,
            final_value=final_value,
            equity_curve=equity_curve,
            sharpe=sharpe_val,
            max_drawdown=max_dd_pct,
            total_return=total_ret,
            annual_return=annual_ret,
            trades_df=trades_df,
            actual_start=actual_start,
            actual_end=actual_end,
            price_series=price_series,
            analyzers_raw={
                "sharpe":  sharpe_ana,
                "drawdown": dd_ana,
                "returns":  ret_ana,
                "trades":   trades_ana,
            },
        )

    # ---------------------------------------------------------------- #

    @staticmethod
    def _extract_price_series(strat) -> pd.Series:
        """从策略数据 feed 中提取收盘价序列。"""
        try:
            data = strat.datas[0]
            closes = list(data.close.array)
            dates  = [bt.num2date(dt) for dt in data.datetime.array]
            series = pd.Series(closes, index=pd.DatetimeIndex(dates), name="close")
            return series.dropna()
        except Exception:
            return pd.Series(dtype=float, name="close")

    @staticmethod
    def _extract_equity_curve(
        strat,
        time_return_ana: dict,
        initial_value: float,
    ) -> pd.Series:
        """
        从 TimeReturn analyzer 构建累计资金曲线。
        如果分析器数据为空，回退到 Observer 数据。
        """
        if time_return_ana:
            dates  = list(time_return_ana.keys())
            values = []
            cum = 1.0
            for d in dates:
                cum *= (1 + time_return_ana[d])
                values.append(initial_value * cum)
            return pd.Series(values, index=pd.DatetimeIndex(dates), name="equity")

        # 回退：从 Observer 提取（按类型查找，避免索引越界或取到错误对象）
        obs_data = next(
            (o for o in strat.observers if isinstance(o, _EquityObserver)), None
        )
        if obs_data is None:
            return pd.Series(dtype=float, name="equity")
        try:
            arr = obs_data.lines.portfolio_value.array
            return pd.Series(arr, name="equity")
        except Exception:
            return pd.Series(dtype=float, name="equity")

    # ---------------------------------------------------------------- #

    @staticmethod
    def _extract_trades(trades_ana: dict, strategy_name: str) -> pd.DataFrame:
        """将 TradeAnalyzer 数据整理为 DataFrame。"""
        rows = []
        total = trades_ana.get("total", {}).get("total", 0)
        won   = trades_ana.get("won",   {}).get("total", 0)
        lost  = trades_ana.get("lost",  {}).get("total", 0)

        rows.append({
            "strategy": strategy_name,
            "metric":   "total_trades",
            "value":    total,
        })
        rows.append({
            "strategy": strategy_name,
            "metric":   "won_trades",
            "value":    won,
        })
        rows.append({
            "strategy": strategy_name,
            "metric":   "lost_trades",
            "value":    lost,
        })
        if total > 0:
            rows.append({
                "strategy": strategy_name,
                "metric":   "win_rate",
                "value":    won / total,
            })

        for side in ("won", "lost"):
            pnl = trades_ana.get(side, {}).get("pnl", {})
            for stat in ("average", "max", "min"):
                if pnl.get(stat) is not None:
                    rows.append({
                        "strategy": strategy_name,
                        "metric":   f"{side}_pnl_{stat}",
                        "value":    pnl[stat],
                    })

        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- #

    def run(self) -> list[RunResult]:
        """主入口：抽取策略 → 加载数据 → 逐策略回测 → 返回结果列表。"""
        selected = self._select_strategies()

        # 只下载一次原始 DataFrame
        df, actual_start, actual_end = download_df(
            ticker=self.ticker,
            start=self.start,
            end=self.end,
        )

        results: list[RunResult] = []
        for strategy_cfg in selected:
            # 每个策略从同一份 DataFrame copy() 构建独立 feed，避免重复下载
            feed_i = bt.feeds.PandasData(dataname=df.copy())
            result = self._run_single(strategy_cfg, feed_i, actual_start, actual_end)
            results.append(result)

        return results
