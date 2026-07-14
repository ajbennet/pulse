"""
Shared Streamlit helpers: cached wrappers around the services so pages stay
thin and re-runs are fast.
"""

import streamlit as st

from services import backtest_service, market_data


@st.cache_data(show_spinner="Loading market data…")
def load_history(tickers, start, end):
    """Cached aligned OHLCV history keyed by (tickers, start, end)."""
    return market_data.history(list(tickers), start, end)


@st.cache_data(show_spinner="Running backtest…")
def run_backtest(settings: dict):
    """
    Cached backtest. `settings` is a plain dict of primitives (hashable) that is
    turned into a RunConfig. Weights arrive as tuples of (ticker, weight) pairs.
    """
    rc = backtest_service.RunConfig(
        tickers=list(settings["tickers"]),
        start_date=settings["start_date"],
        end_date=settings["end_date"],
        initial_capital=settings["initial_capital"],
        stop_drawdown=settings["stop_drawdown"],
        rebalance_drift=settings["rebalance_drift"],
        cash_buffer=settings["cash_buffer"],
        normal_weights=dict(settings["normal_weights"]),
        defensive_weights=dict(settings["defensive_weights"]),
        run_benchmark=settings["run_benchmark"],
    )
    return backtest_service.run(rc, write_csv=settings.get("write_csv", False))


@st.cache_data(ttl=300, show_spinner="Fetching latest prices…")
def latest_prices(tickers):
    return market_data.latest_prices(list(tickers))


def fmt_pct(x):
    return "—" if x is None else f"{x * 100:.2f}%"


def fmt_money(x):
    return "—" if x is None else f"${x:,.2f}"
