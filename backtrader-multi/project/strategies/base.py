import logging
import backtrader as bt


class BaseSignalStrategy(bt.Strategy):
    """
    所有策略的抽象基类。
    统一 log() 接口、notify_order / notify_trade 实现，
    内部通过 Python 标准 logging 模块输出日志。
    """

    params = dict(
        strategy_name="",   # 由引擎在 addstrategy 时注入；空字符串表示使用类名
    )

    @property
    def strategy_name(self) -> str:
        return self.p.strategy_name or self.__class__.__name__

    # ------------------------------------------------------------------ #
    #  日志                                                                #
    # ------------------------------------------------------------------ #
    def _get_logger(self) -> logging.Logger:
        return logging.getLogger(self.strategy_name)

    # 包含这些关键词的日志视为交易事件，文件中加分隔线标记
    _TRADE_KEYWORDS = (
        "BUY INIT", "SCALE IN", "CLOSE ALL",
        "ORDER BUY EXECUTED", "ORDER SELL EXECUTED", "TRADE CLOSED",
    )

    def log(self, msg: str, level: str = "INFO") -> None:
        """
        统一日志入口。
        level: "DEBUG" / "INFO" / "WARNING" / "ERROR"
        交易事件（BUY/SELL/CLOSE）在日志文件中自动加分隔线标记。
        """
        logger = self._get_logger()
        try:
            bar_dt = self.datas[0].datetime.datetime(0)
            dt_str = bar_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            dt_str = "N/A"

        is_trade = any(kw in msg for kw in self._TRADE_KEYWORDS)
        sep = "-" * 72

        if is_trade:
            full_msg = f"\n{sep}\n[{dt_str}] [{self.strategy_name}] [{level:<5}] {msg}\n{sep}"
        else:
            full_msg = f"[{dt_str}] [{self.strategy_name}] [{level:<5}] {msg}"

        level_map = {
            "DEBUG":   logger.debug,
            "INFO":    logger.info,
            "WARNING": logger.warning,
            "ERROR":   logger.error,
        }
        log_fn = level_map.get(level.upper(), logger.info)
        log_fn(full_msg)

    # ------------------------------------------------------------------ #
    #  订单 & 交易通知                                                     #
    # ------------------------------------------------------------------ #
    def notify_order(self, order: bt.Order) -> None:
        if order.status in [order.Submitted, order.Accepted]:
            return

        if order.status == order.Completed:
            direction = "BUY" if order.isbuy() else "SELL"
            self.log(
                f"ORDER {direction} EXECUTED | "
                f"Price={order.executed.price:.2f} | "
                f"Size={order.executed.size:.0f} | "
                f"Cost={order.executed.value:.2f} | "
                f"Comm={order.executed.comm:.2f}"
            )
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log(
                f"ORDER CANCELLED/MARGIN/REJECTED | Status={order.getstatusname()}",
                level="WARNING"
            )

    def notify_trade(self, trade: bt.Trade) -> None:
        if not trade.isclosed:
            return
        self.log(
            f"TRADE CLOSED | "
            f"Gross PnL={trade.pnl:.2f} | "
            f"Net PnL={trade.pnlcomm:.2f}"
        )
