"""
Performance analytics computed from an equity curve.

These functions operate on a pandas Series of daily portfolio values indexed
by date, keeping the metric logic independent of the Backtrader engine.
"""

import numpy as np
import pandas as pd

from core import config


def equity_series(equity_curve):
    """Convert a list of (date, value) tuples into a clean pandas Series."""
    s = pd.Series(
        data=[v for _, v in equity_curve],
        index=pd.to_datetime([d for d, _ in equity_curve]),
    )
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def cagr(initial, final, years):
    if years <= 0 or initial <= 0:
        return 0.0
    return (final / initial) ** (1.0 / years) - 1.0


def max_drawdown(equity):
    """Return the maximum drawdown as a negative fraction (e.g. -0.42)."""
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    return float(dd.min())


def annualized_volatility(daily_returns, periods=config.TRADING_DAYS_PER_YEAR):
    if len(daily_returns) < 2:
        return 0.0
    return float(daily_returns.std(ddof=1) * np.sqrt(periods))


def sharpe_ratio(daily_returns, rf_annual=config.RISK_FREE_RATE,
                 periods=config.TRADING_DAYS_PER_YEAR):
    if len(daily_returns) < 2:
        return 0.0
    rf_daily = rf_annual / periods
    excess = daily_returns - rf_daily
    std = excess.std(ddof=1)
    if std == 0:
        return 0.0
    return float((excess.mean() / std) * np.sqrt(periods))


def annual_returns(equity):
    """
    Calendar-year returns plus running CAGR since inception at each year-end.

    Returns a DataFrame with columns: year, annual_return, running_cagr.
    """
    start_value = float(equity.iloc[0])
    start_date = equity.index[0]

    # Last value in each calendar year.
    year_end = equity.groupby(equity.index.year).last()

    rows = []
    prev_value = start_value
    for year, end_value in year_end.items():
        annual = end_value / prev_value - 1.0
        year_end_date = equity[equity.index.year == year].index[-1]
        years_elapsed = (year_end_date - start_date).days / 365.25
        run_cagr = cagr(start_value, float(end_value), years_elapsed) if years_elapsed > 0 else 0.0
        rows.append(dict(
            year=int(year),
            annual_return=round(annual, 6),
            running_cagr=round(run_cagr, 6),
        ))
        prev_value = end_value

    return pd.DataFrame(rows)


def compute_metrics(equity_curve, regime_records=None, trades_count=0,
                    regime_switches=0, initial_capital=None, label="strategy"):
    """
    Compute the full metric bundle from an equity curve.

    Returns a dict of scalar metrics suitable for the console summary and the
    summary_metrics.csv output.
    """
    equity = equity_series(equity_curve)
    daily_ret = equity.pct_change().dropna()

    initial = initial_capital if initial_capital is not None else float(equity.iloc[0])
    final = float(equity.iloc[-1])
    start_date = equity.index[0]
    end_date = equity.index[-1]
    years = (end_date - start_date).days / 365.25

    metrics = {
        "label": label,
        "start_date": start_date.date().isoformat(),
        "end_date": end_date.date().isoformat(),
        "initial_capital": round(initial, 2),
        "final_value": round(final, 2),
        "cagr": round(cagr(initial, final, years), 6),
        "max_drawdown": round(max_drawdown(equity), 6),
        "annualized_volatility": round(annualized_volatility(daily_ret), 6),
        "sharpe_ratio": round(sharpe_ratio(daily_ret), 6),
        "total_trades": trades_count,
        "regime_switches": regime_switches,
    }

    # Time-in-regime split (LDR only).
    if regime_records is not None:
        normal_days, defensive_days = _time_in_regime(equity, regime_records)
        total_days = normal_days + defensive_days
        metrics["pct_time_normal"] = round(normal_days / total_days, 6) if total_days else 0.0
        metrics["pct_time_defensive"] = round(defensive_days / total_days, 6) if total_days else 0.0

    return metrics


def _time_in_regime(equity, regime_records):
    """
    Count trading days spent in each regime by replaying the regime switches
    over the equity curve's date index.
    """
    switches = [(pd.Timestamp(r["date"]), r["regime_after"])
                for r in regime_records if r["regime_after"] in ("normal", "defensive")]
    switches.sort(key=lambda x: x[0])

    normal_days = 0
    defensive_days = 0
    current = "normal"
    si = 0
    for dt in equity.index:
        while si < len(switches) and switches[si][0] <= dt:
            current = switches[si][1]
            si += 1
        if current == "normal":
            normal_days += 1
        else:
            defensive_days += 1
    return normal_days, defensive_days
