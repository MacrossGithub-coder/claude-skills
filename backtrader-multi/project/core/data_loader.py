import logging
import time
import random
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
import backtrader as bt

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0  # seconds


def download_df(
    ticker: str,
    start: str,
    end: str,
) -> tuple:
    """
    从 yfinance 下载原始 OHLCV DataFrame。

    Returns:
        (df: pd.DataFrame, actual_start: date, actual_end: date)

    Raises:
        ValueError: 数据下载失败或为空时抛出。
    """
    # yfinance 的 end 参数是排他性的，+1 天确保 end 日期本身被包含
    end_dt   = pd.to_datetime(end).date()
    end_plus = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"Downloading {ticker}: {start} → {end}")

    df = None
    for attempt in range(_MAX_RETRIES):
        try:
            df = yf.download(ticker, start=start, end=end_plus, auto_adjust=True, progress=False)
            break
        except Exception as exc:
            exc_name = type(exc).__name__
            is_rate_limit = "RateLimit" in exc_name or "TooManyRequests" in exc_name or "429" in str(exc)
            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE ** attempt + random.uniform(0, 1)
                logger.warning(f"Rate limited downloading {ticker}, retrying in {wait:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES})")
                time.sleep(wait)
            else:
                raise ValueError(f"yfinance download failed for {ticker}: {exc}") from exc

    # 展平多层列（yfinance ≥ 0.2 有时返回 MultiIndex）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"Downloaded data for {ticker} is missing columns: {missing}. "
            f"Got: {list(df.columns)}"
        )

    df = df[required_cols].dropna()

    if df.empty:
        raise ValueError(f"No data available for {ticker} in {start}..{end}")

    actual_start: date = df.index[0].date()
    actual_end:   date = df.index[-1].date()
    logger.info(f"Data ready: {ticker} | {actual_start} → {actual_end} | {len(df)} bars")

    return df, actual_start, actual_end


def get_data(
    ticker: str,
    start: str,
    end: str,
) -> tuple:
    """
    下载数据并封装为 bt.feeds.PandasData。

    Returns:
        (bt.feeds.PandasData, actual_start: date, actual_end: date)
    """
    df, actual_start, actual_end = download_df(ticker, start, end)
    feed = bt.feeds.PandasData(dataname=df)
    return feed, actual_start, actual_end
