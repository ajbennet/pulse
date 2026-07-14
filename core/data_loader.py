"""
Data loading for the LDR strategy.

Downloads split/dividend-adjusted daily OHLCV data from Yahoo Finance via
yfinance, cleans the columns into a Backtrader-friendly shape, aligns the
feeds onto a common date range (handling differing inception dates), and
optionally caches results to disk.
"""

import os

import pandas as pd
import yfinance as yf

from core import config


# Canonical column order Backtrader's PandasData feed expects.
_OHLCV = ["open", "high", "low", "close", "volume"]


def _cache_path(ticker):
    return os.path.join(config.CACHE_DIR, f"{ticker.replace('-', '_')}.csv")


def _download_one(ticker, start, end, logger=None):
    """Download a single ticker and return a cleaned OHLCV DataFrame."""
    df = yf.download(
        ticker,
        start=start,
        end=end,
        auto_adjust=True,     # adjust for splits & dividends
        progress=False,
        actions=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"No data returned for {ticker}")

    # yfinance may return MultiIndex columns (('Close', 'TQQQ')...). Flatten.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns=str.lower)
    df = df[[c for c in _OHLCV if c in df.columns]].copy()
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    df = df.dropna(how="any")
    return df


def _load_cached(ticker):
    path = _cache_path(ticker)
    if config.USE_CACHE and os.path.exists(path):
        df = pd.read_csv(path, index_col="date", parse_dates=True)
        return df
    return None


def _write_cache(ticker, df):
    if config.USE_CACHE:
        os.makedirs(config.CACHE_DIR, exist_ok=True)
        df.to_csv(_cache_path(ticker))


def load_data(tickers=None, start=None, end=None, logger=None):
    """
    Load and align data for all tickers.

    Returns a dict {ticker: DataFrame} where every DataFrame shares the same
    DatetimeIndex (the intersection of all available dates). Because TQQQ has
    the latest inception (~Feb 2010), the intersection effectively starts when
    TQQQ data becomes available — a practical, fully-invested start point.
    """
    tickers = tickers or config.TICKERS
    start = start or config.START_DATE
    end = end or config.END_DATE

    frames = {}
    for t in tickers:
        df = _load_cached(t)
        if df is None:
            if logger:
                logger.info(f"Downloading {t} from Yahoo Finance ...")
            df = _download_one(t, start, end, logger=logger)
            _write_cache(t, df)
        else:
            if logger:
                logger.info(f"Loaded {t} from cache ({len(df)} rows).")
        frames[t] = df

    # Align on the intersection of dates so all feeds are synchronized.
    common_index = None
    for df in frames.values():
        common_index = df.index if common_index is None else common_index.intersection(df.index)
    common_index = common_index.sort_values()

    aligned = {t: df.reindex(common_index).dropna(how="any") for t, df in frames.items()}

    # Re-intersect after dropna in case any residual gaps existed.
    common_index = None
    for df in aligned.values():
        common_index = df.index if common_index is None else common_index.intersection(df.index)
    aligned = {t: df.reindex(common_index) for t, df in aligned.items()}

    if logger:
        logger.info(
            f"Aligned data: {len(common_index)} common trading days "
            f"from {common_index[0].date()} to {common_index[-1].date()}."
        )
    return aligned
