import backtrader as bt
from .base import BaseSignalStrategy


class MaRsiStrategy(BaseSignalStrategy):
    """
    MA + RSI 策略（修复版）。

    修复记录：
    1. open_condition: "and"/"or" 参数化，消除条件逻辑矛盾
    2. 仓位检查从 broker.getcash() 改为 broker.getvalue()（含持仓市值）
    3. show_plot 参数化，默认 False，由配置控制
    4. 日志通过 BaseSignalStrategy.log() 恢复，接入 Python logging 模块
    """

    params = dict(
        # --- MA ---
        ma_short=20,
        ma_long=60,

        # --- RSI ---
        rsi_period=14,
        rsi_buy=30,
        rsi_sell=70,

        # --- 开仓条件组合 ---
        # "and": RSI 超卖 AND MACD 金叉同时满足才开仓
        # "or" : RSI 超卖 OR  MACD 金叉任一满足即开仓
        open_condition="or",

        # --- 仓位管理 ---
        risk_ratio=0.50,      # 初始建仓占总资产比例
        add_ratio=0.10,       # 每次加仓占可用现金比例
        max_pos_ratio=0.90,   # 加仓后总仓位不超过总资产的比例（必须 > risk_ratio）
        max_scale_in=2,       # 最大加仓次数
        scale_step=0.03,      # 价格每涨 scale_step 触发一次加仓

        # --- 其他 ---
        show_plot=False,      # 是否在回测结束后弹出 cerebro 内置图表
    )

    def __init__(self):
        # strategy_name 由 BaseSignalStrategy.params 中的 strategy_name 提供，
        # 引擎通过 addstrategy(cls, strategy_name=name, ...) 注入。

        # --- MA ---
        self.ma_short_ind = bt.indicators.SMA(self.data.close, period=self.p.ma_short)
        self.ma_long_ind  = bt.indicators.SMA(self.data.close, period=self.p.ma_long)

        # --- RSI ---
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)

        # --- MACD ---
        self.macd_ind = bt.indicators.MACD(
            self.data.close,
            period_me1=12,
            period_me2=26,
            period_signal=9,
        )
        self.macd_line   = self.macd_ind.macd
        self.signal_line = self.macd_ind.signal

        # 加仓状态
        self.entry_price    = None
        self.scale_in_times = 0

    # ------------------------------------------------------------------ #
    #  主逻辑                                                              #
    # ------------------------------------------------------------------ #
    def next(self):
        price = self.data.close[0]

        # DEBUG：每根 K 线输出指标值
        self.log(
            f"RSI={self.rsi[0]:.2f} | "
            f"MA_S={self.ma_short_ind[0]:.2f} | MA_L={self.ma_long_ind[0]:.2f} | "
            f"MACD={self.macd_line[0]:.4f} | Signal={self.signal_line[0]:.4f}",
            level="DEBUG"
        )

        # ===== 无持仓 → 判断开仓 ===== #
        if not self.position:
            rsi_signal  = self.rsi[0] < self.p.rsi_buy
            macd_signal = self.macd_line[0] > self.signal_line[0]

            if self.p.open_condition == "and":
                should_open = rsi_signal and macd_signal
            else:  # "or"
                should_open = rsi_signal or macd_signal

            if should_open:
                # Bug 修复：使用 getvalue()（总资产 = 现金 + 持仓市值），而非 getcash()
                total_value = self.broker.getvalue()
                size = int((total_value * self.p.risk_ratio) / price)

                self.log(
                    f"OPEN SIGNAL | open_condition={self.p.open_condition} | "
                    f"rsi_signal={rsi_signal} | macd_signal={macd_signal} | "
                    f"total_value={total_value:.2f}",
                    level="DEBUG"
                )

                if size > 0:
                    new_pos_value = size * price
                    if new_pos_value <= total_value * self.p.risk_ratio:
                        self.log(
                            f"BUY INIT | Price={price:.2f} | Size={size} | "
                            f"total_value={total_value:.2f}"
                        )
                        self.buy(size=size)
                        self.entry_price    = price
                        self.scale_in_times = 0
                    else:
                        self.log(
                            f"SKIP BUY | 初始买入后仓位将超过 {self.p.risk_ratio:.0%} | "
                            f"new_pos_value={new_pos_value:.2f} | total_value={total_value:.2f}",
                            level="WARNING"
                        )

        # ===== 有持仓 → 加仓 + 平仓 ===== #
        else:
            # --- 分批加仓 ---
            if self.entry_price is not None:
                target_increase = 1 + self.p.scale_step * (self.scale_in_times + 1)
                if (
                    self.scale_in_times < self.p.max_scale_in
                    and price >= self.entry_price * target_increase
                    and self.ma_short_ind[0] > self.ma_long_ind[0]
                    and self.macd_line[0] > self.signal_line[0]
                ):
                    cash     = self.broker.getcash()
                    add_size = int((cash * self.p.add_ratio) / price)
                    if add_size > 0:
                        total_value      = self.broker.getvalue()
                        current_pos_val  = self.position.size * price
                        new_pos_val      = current_pos_val + add_size * price
                        if new_pos_val <= total_value * self.p.max_pos_ratio:
                            self.log(
                                f"SCALE IN #{self.scale_in_times + 1} | "
                                f"Price={price:.2f} | AddSize={add_size} | "
                                f"total_value={total_value:.2f}"
                            )
                            self.buy(size=add_size)
                            self.scale_in_times += 1
                        else:
                            self.log(
                                f"SKIP SCALE IN | 加仓后仓位将超过 {self.p.max_pos_ratio:.0%} | "
                                f"new_pos_val={new_pos_val:.2f} | total_value={total_value:.2f}",
                                level="WARNING"
                            )

            # --- 平仓：RSI 超买 OR MA 死叉 OR MACD 死叉 ---
            if (
                self.rsi[0] > self.p.rsi_sell
                or self.ma_short_ind[0] < self.ma_long_ind[0]
                or self.macd_line[0] < self.signal_line[0]
            ):
                self.log(
                    f"CLOSE ALL | Price={price:.2f} | PosSize={self.position.size:.0f} | "
                    f"RSI={self.rsi[0]:.2f}"
                )
                self.close()
                self.entry_price    = None
                self.scale_in_times = 0
