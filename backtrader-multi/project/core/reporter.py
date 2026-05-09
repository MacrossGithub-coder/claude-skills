"""
结果汇总、对比图、quantstats HTML 报告、CSV、控制台打印。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 非交互后端，避免 GUI 弹窗
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import quantstats as qs

if TYPE_CHECKING:
    from .engine import RunResult

logger = logging.getLogger(__name__)


class Reporter:
    """把 list[RunResult] 转换为各种输出物。"""

    # ------------------------------------------------------------------ #
    #  CSV 交易明细                                                        #
    # ------------------------------------------------------------------ #

    def save_csv(self, results: list[RunResult], output_dir: Path) -> None:
        """将所有策略的交易明细合并保存为单个 CSV。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        path  = output_dir / f"{today}_trades.csv"

        frames = [r.trades_df for r in results if not r.trades_df.empty]
        if frames:
            combined = pd.concat(frames, ignore_index=True)
            combined.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info(f"Trades CSV saved: {path}")
        else:
            logger.warning("No trades data to save.")

    # ------------------------------------------------------------------ #
    #  多策略资金曲线对比图                                                #
    # ------------------------------------------------------------------ #

    def save_comparison_chart(
        self,
        results: list[RunResult],
        output_dir: Path,
    ) -> None:
        """将所有策略的资金曲线绘制在同一张图上并保存。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        path  = output_dir / f"{today}_comparison_chart.png"

        # 高对比度颜色序列：色相间隔大，深浅交替
        HIGH_CONTRAST_COLORS = [
            "#E63946",  # 红
            "#2196F3",  # 蓝
            "#FF9800",  # 橙
            "#4CAF50",  # 绿
            "#9C27B0",  # 紫
            "#00BCD4",  # 青
            "#F06292",  # 粉红
            "#8D6E63",  # 棕
        ]
        fig, ax = plt.subplots(figsize=(14, 7))

        # 左轴：各策略资金曲线
        for i, r in enumerate(results):
            if r.equity_curve.empty:
                logger.warning(f"[{r.name}] equity_curve is empty, skipping chart line.")
                continue
            color = HIGH_CONTRAST_COLORS[i % len(HIGH_CONTRAST_COLORS)]
            ax.plot(
                r.equity_curve.index,
                r.equity_curve.values,
                label=r.name,
                color=color,
                linewidth=2.0,
            )

        ax.set_title("Multi-Strategy Equity Curve Comparison", fontsize=14)
        ax.set_xlabel("Date")
        ax.set_ylabel("Portfolio Value")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax.grid(alpha=0.3)

        # 右轴：股票价格走势（取第一个有价格数据的策略）
        price_series = next(
            (r.price_series for r in results if not r.price_series.empty), None
        )
        if price_series is not None:
            ax2 = ax.twinx()
            ax2.plot(
                price_series.index,
                price_series.values,
                color="#AAAAAA",
                linewidth=1.0,
                linestyle="--",
                alpha=0.6,
                label="Stock Price",
            )
            ax2.set_ylabel("Stock Price", color="#888888")
            ax2.tick_params(axis="y", labelcolor="#888888")
            ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.1f}"))
            # 合并两轴图例
            lines1, labels1 = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
        else:
            ax.legend(loc="upper left")

        fig.tight_layout()
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"Comparison chart saved: {path}")

    # ------------------------------------------------------------------ #
    #  quantstats HTML 报告                                               #
    # ------------------------------------------------------------------ #

    def save_html_report(
        self,
        results: list[RunResult],
        output_dir: Path,
    ) -> None:
        """为每个策略生成独立的 quantstats HTML 报告。"""
        # try:
        #     import quantstats as qs
        # except ImportError:
        #     logger.warning(
        #         "quantstats not installed. Skipping HTML report generation. "
        #         "Install with: pip install quantstats"
        #     )
        #     return

        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")

        for r in results:
            if r.equity_curve.empty:
                logger.warning(f"[{r.name}] equity_curve empty, skipping HTML report.")
                continue

            path = output_dir / f"{today}_{r.name}_report.html"

            # quantstats 需要连续日历日的每日收益率序列
            # resample("D").last().ffill() 填充非交易日，避免间距不均导致指标偏差
            returns = (
                r.equity_curve
                .resample("D").last()
                .ffill()
                .pct_change()
                .dropna()
            )
            if returns.empty:
                logger.warning(f"[{r.name}] returns series empty after pct_change, skipping.")
                continue

            try:
                qs.reports.html(
                    returns,
                    output=str(path),
                    title=f"{r.name} — Backtest Report",
                    download_filename=path.name,
                )
                logger.info(f"[{r.name}] HTML report saved: {path}")
            except Exception as exc:
                logger.error(f"[{r.name}] Failed to generate HTML report: {exc}")

    # ------------------------------------------------------------------ #
    #  控制台绩效汇总                                                      #
    # ------------------------------------------------------------------ #

    def print_summary(self, results: list[RunResult]) -> None:
        """控制台打印各策略绩效表格 + 加权汇总指标。"""
        print()
        print("=" * 70)
        print("  BACKTEST SUMMARY")
        print("=" * 70)

        header = (
            f"{'Strategy':<30} {'Return':>8} {'Annual':>8} "
            f"{'MaxDD':>8} {'Sharpe':>8} {'Weight':>7}"
        )
        print(header)
        print("-" * 70)

        weighted_return = 0.0
        weighted_annual = 0.0
        weighted_dd     = 0.0
        total_weight    = sum(r.weight for r in results)

        for r in results:
            sharpe_str = f"{r.sharpe:.3f}" if r.sharpe is not None else "N/A"
            w = r.weight / total_weight if total_weight > 0 else 0

            print(
                f"{r.name:<30} "
                f"{r.total_return:>7.2%} "
                f"{r.annual_return:>7.2%} "
                f"{r.max_drawdown:>7.2f}% "
                f"{sharpe_str:>8} "
                f"{r.weight:>6.2f}"
            )

            weighted_return += r.total_return * w
            weighted_annual += r.annual_return * w
            weighted_dd     += r.max_drawdown  * w

        print("-" * 70)
        print(
            f"{'WEIGHTED AVERAGE':<30} "
            f"{weighted_return:>7.2%} "
            f"{weighted_annual:>7.2%} "
            f"{weighted_dd:>7.2f}%"
        )
        print("=" * 70)

        # 逐策略交易统计
        for r in results:
            total_t = _get_trade_stat(r.trades_df, r.name, "total_trades")
            won_t   = _get_trade_stat(r.trades_df, r.name, "won_trades")
            wr_val  = _get_trade_stat(r.trades_df, r.name, "win_rate")

            wr_str = f"{wr_val:.1%}" if wr_val is not None else "N/A"
            print(
                f"  [{r.name}] trades={int(total_t or 0)} | "
                f"won={int(won_t or 0)} | win_rate={wr_str}"
            )
        print()


# ------------------------------------------------------------------ #
#  辅助函数                                                            #
# ------------------------------------------------------------------ #

def _get_trade_stat(df: pd.DataFrame, strategy: str, metric: str):
    if df.empty:
        return None
    row = df[(df["strategy"] == strategy) & (df["metric"] == metric)]
    if row.empty:
        return None
    return row.iloc[0]["value"]
