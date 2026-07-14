"""
Market data access for live/portfolio features.

Wraps yfinance for latest prices and reuses core.data_loader for aligned
history. Kept separate from core so live-data concerns stay out of the engine.
"""

from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from core import config, data_loader


def latest_prices(tickers: Optional[List[str]] = None) -> Dict[str, float]:
    """Return the most recent close for each ticker."""
    tickers = tickers or config.TICKERS
    out: Dict[str, float] = {}
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period="5d", auto_adjust=True)
            if not hist.empty:
                out[t] = float(hist["Close"].dropna().iloc[-1])
        except Exception:
            out[t] = float("nan")
    return out


def latest_closes_with_dates(tickers: Optional[List[str]] = None):
    """Return {ticker: (date, close)} for the latest available bar."""
    tickers = tickers or config.TICKERS
    out = {}
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period="5d", auto_adjust=True)
            hist = hist.dropna(subset=["Close"])
            if not hist.empty:
                out[t] = (hist.index[-1].date().isoformat(), float(hist["Close"].iloc[-1]))
        except Exception:
            out[t] = (None, float("nan"))
    return out


def history(tickers: Optional[List[str]] = None, start=None, end=None) -> Dict[str, pd.DataFrame]:
    """Aligned adjusted OHLCV history (delegates to core.data_loader)."""
    return data_loader.load_data(tickers=tickers, start=start, end=end)
