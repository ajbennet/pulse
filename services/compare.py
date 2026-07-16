"""
Strategy bake-off harness (vectorized / event simulation).

Compares leveraged-TQQQ strategies on the same price series:
  * Buy & Hold TQQQ
  * SMA trend rotation (100% TQQQ above the SMA, else a defensive basket),
    with optional re-entry buffer band and partial leverage (DD-reduction levers)
  * Core 9-Sig (quarterly value-averaging to a 9%-growth signal line, funded by
    a configurable reserve, with a 90% reserve buying-power cap)

Prices are dividend/split-adjusted (yfinance auto_adjust). 9-Sig here is the
core rule set (no 30-Down / spike-reset / contributions) — enough for an
apples-to-apples drawdown/return comparison.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252


# ----------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------
def load_closes(tickers: List[str], start: str, end: Optional[str] = None) -> pd.DataFrame:
    """Adjusted daily closes for the tickers, aligned on their common dates."""
    data = yf.download(tickers, start=start, end=end, auto_adjust=True,
                       progress=False)["Close"]
    if isinstance(data, pd.Series):
        data = data.to_frame(tickers[0])
    return data.dropna(how="any")


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------
def metrics(equity: pd.Series, rf: float = 0.0) -> Dict:
    equity = equity.dropna()
    ret = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = equity / equity.cummax() - 1.0
    max_dd = float(dd.min())
    vol = float(ret.std() * np.sqrt(TRADING_DAYS))
    sharpe = float((ret.mean() - rf / TRADING_DAYS) / ret.std() * np.sqrt(TRADING_DAYS)) \
        if ret.std() else 0.0
    underwater = dd < -1e-9
    pct_underwater = float(underwater.mean())
    # longest underwater streak (days)
    longest = cur = 0
    for u in underwater:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return {
        "CAGR": round(cagr, 4),
        "Max DD": round(max_dd, 4),
        "Calmar": round(cagr / abs(max_dd), 2) if max_dd else None,
        "Sharpe": round(sharpe, 2),
        "Vol": round(vol, 4),
        "% underwater": round(pct_underwater, 3),
        "Max underwater (days)": int(longest),
        "Final $": round(float(equity.iloc[-1]), 0),
    }


# ----------------------------------------------------------------------
# Simulators (each returns an equity Series, start = initial)
# ----------------------------------------------------------------------
def sim_buyhold(closes: pd.DataFrame, ticker: str, initial: float = 100_000) -> pd.Series:
    r = closes[ticker].pct_change().fillna(0.0)
    return initial * (1 + r).cumprod()


def _basket_return(closes, weights, rets):
    out = pd.Series(0.0, index=closes.index)
    for t, w in weights.items():
        if t == "CASH":
            continue
        out = out + w * rets[t]
    return out


def sim_rotation(closes: pd.DataFrame, on_weights: Dict[str, float],
                 off_weights: Dict[str, float], sma_len: int = 150,
                 signal_ticker: str = "TQQQ", band: float = 0.0,
                 initial: float = 100_000) -> pd.Series:
    """
    Trend rotation: hold `on_weights` when signal_ticker is above its SMA (with
    an optional re-entry buffer `band`), else `off_weights`. Weights rebalanced
    daily within a regime. Signal is lagged one day (no look-ahead).
    """
    rets = closes.pct_change().fillna(0.0)
    price = closes[signal_ticker]
    sma = price.rolling(sma_len).mean()

    # Regime with hysteresis: exit below SMA, re-enter above SMA*(1+band).
    state = False
    states = []
    for p, s in zip(price.values, sma.values):
        if np.isnan(s):
            states.append(False)
            continue
        if not state and p > s * (1 + band):
            state = True
        elif state and p < s:
            state = False
        states.append(state)
    on = pd.Series(states, index=closes.index).shift(1).fillna(False)

    on_ret = _basket_return(closes, on_weights, rets)
    off_ret = _basket_return(closes, off_weights, rets)
    port = on_ret.where(on, off_ret)
    equity = initial * (1 + port).cumprod()
    return equity.iloc[sma_len:]  # drop the pre-SMA warmup


def sim_9sig(closes: pd.DataFrame, reserve_weights: Dict[str, float], tqqq: str = "TQQQ",
             tqqq_w: float = 0.70, growth: float = 0.09, buy_cap: float = 0.90,
             initial: float = 100_000) -> pd.Series:
    """
    Core 9-Sig: each quarter, value-average TQQQ toward base*(1+growth); buys are
    capped at buy_cap of the reserve; sells add to the reserve. Reserve held in
    reserve_weights (summing to 1), earning their returns between rebalances.
    """
    idx = closes.index
    px = {t: closes[t].values for t in closes.columns}
    tpx = closes[tqqq].values

    tqqq_shares = (tqqq_w * initial) / tpx[0]
    reserve_shares = {a: (w * (1 - tqqq_w) * initial) / closes[a].values[0]
                      for a, w in reserve_weights.items()}
    base = tqqq_w * initial

    # Quarter-end trading days.
    q = pd.Series(idx, index=idx).dt.to_period("Q")
    is_qend = q != q.shift(-1)

    equity = np.empty(len(idx))
    for i in range(len(idx)):
        tqqq_val = tqqq_shares * tpx[i]
        reserve_val = sum(reserve_shares[a] * px[a][i] for a in reserve_shares)
        equity[i] = tqqq_val + reserve_val

        if is_qend.iloc[i] and i > 0:
            target = base * (1 + growth)
            diff = tqqq_val - target
            if diff < 0 and reserve_val > 0:               # BUY, capped by reserve
                buy = min(-diff, buy_cap * reserve_val)
                tqqq_shares += buy / tpx[i]
                for a in reserve_shares:                     # fund pro-rata from reserve
                    share_val = reserve_shares[a] * px[a][i]
                    reserve_shares[a] -= (buy * share_val / reserve_val) / px[a][i]
            elif diff > 0:                                   # SELL into reserve
                tqqq_shares -= diff / tpx[i]
                for a, w in reserve_weights.items():
                    reserve_shares[a] += (diff * w) / px[a][i]
            base = target
    return pd.Series(equity, index=idx)


# ----------------------------------------------------------------------
# Bake-off driver
# ----------------------------------------------------------------------
@dataclass
class Strat:
    name: str
    kind: str                      # 'bh' | 'rotation' | '9sig'
    params: dict = field(default_factory=dict)
    tickers: List[str] = field(default_factory=list)


def run_strategy(spec: Strat, start: str, end: Optional[str] = None,
                 initial: float = 100_000) -> Optional[pd.Series]:
    closes = load_closes(spec.tickers, start, end)
    if closes.empty:
        return None
    if spec.kind == "bh":
        return sim_buyhold(closes, spec.params["ticker"], initial)
    if spec.kind == "rotation":
        return sim_rotation(closes, initial=initial, **spec.params)
    if spec.kind == "9sig":
        return sim_9sig(closes, initial=initial, **spec.params)
    return None


def compare(specs: List[Strat], start: str, end: Optional[str] = None,
            align: bool = True, initial: float = 100_000):
    """Run each strategy; return (metrics_df, equity_df). If align, restrict all
    to their common date range for a fair comparison."""
    equities = {}
    for s in specs:
        eq = run_strategy(s, start, end, initial)
        if eq is not None and len(eq) > 5:
            equities[s.name] = eq
    if not equities:
        return pd.DataFrame(), pd.DataFrame()

    eq_df = pd.DataFrame(equities)
    if align:
        eq_df = eq_df.dropna(how="any")
        # rebase all to the same initial at the common start
        eq_df = eq_df / eq_df.iloc[0] * initial

    rows = {name: metrics(eq_df[name].dropna()) for name in eq_df.columns}
    mdf = pd.DataFrame(rows).T
    return mdf, eq_df
