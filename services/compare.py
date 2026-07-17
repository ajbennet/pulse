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

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252
# Committed price cache (public market data, safe to check into git) so backtests
# work offline and without network calls. Refreshed only when it falls behind.
_PRICE_CACHE = "prices"
_STALE_DAYS = 4                 # refetch only if newest cached date is older than this
_HISTORY_START = "2005-01-01"   # cache full history per ticker, slice per request


def _download_one(ticker: str) -> pd.Series:
    """Download one ticker's full adjusted-close history, with retries."""
    for _ in range(3):
        try:
            df = yf.download(ticker, start=_HISTORY_START, auto_adjust=True,
                             progress=False, threads=False)
        except Exception:
            df = None
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            s = close.dropna()
            if not s.empty:
                s.index = pd.to_datetime(s.index)
                return s
    return pd.Series(dtype=float)


def _cached_series(ticker: str) -> pd.Series:
    """Full-history adjusted closes for one ticker, backed by an on-disk cache
    (so one flaky yfinance call can't truncate a whole comparison)."""
    os.makedirs(_PRICE_CACHE, exist_ok=True)
    path = os.path.join(_PRICE_CACHE, ticker.replace("/", "_").replace(".", "_") + ".csv")
    cached = None
    if os.path.exists(path):
        try:
            cached = pd.read_csv(path, index_col=0, parse_dates=True)["close"]
        except Exception:
            cached = None
    # Fresh if the newest cached date is within _STALE_DAYS of today (covers
    # weekends/holidays) — so a committed file avoids network calls entirely.
    fresh = (cached is not None and not cached.empty
             and (pd.Timestamp.now().normalize() - cached.index[-1]).days <= _STALE_DAYS)
    if fresh:
        return cached

    dl = _download_one(ticker)
    if not dl.empty:
        dl.to_frame("close").to_csv(path)
        return dl
    if cached is not None and not cached.empty:
        return cached           # fall back to (stale) committed cache if offline
    raise RuntimeError(f"No data for {ticker} (yfinance).")


def load_closes(tickers: List[str], start: str, end: Optional[str] = None) -> pd.DataFrame:
    """Adjusted daily closes for the tickers, aligned on their common dates.
    Each ticker is fetched over full history (cached), then sliced — so a partial
    download of one ticker can't truncate the others."""
    series = {t: _cached_series(t) for t in tickers}
    df = pd.DataFrame(series)
    df = df[df.index >= pd.to_datetime(start)]
    if end is not None:
        df = df[df.index <= pd.to_datetime(end)]
    df = df.dropna(how="any")
    if df.empty:
        raise RuntimeError(f"No overlapping data for {tickers} from {start}.")
    return df


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
        "Total growth": round(float(equity.iloc[-1] / equity.iloc[0] - 1.0), 4),
    }


# ----------------------------------------------------------------------
# Simulators (each returns an equity Series, start = initial)
# ----------------------------------------------------------------------
def _has(closes, *tickers):
    return (closes is not None and not closes.empty
            and all(t in closes.columns for t in tickers) and len(closes) > 0)


def sim_buyhold(closes: pd.DataFrame, ticker: str, initial: float = 100_000) -> pd.Series:
    if not _has(closes, ticker):
        return pd.Series(dtype=float)
    r = closes[ticker].pct_change().fillna(0.0)
    return initial * (1 + r).cumprod()


def _basket_return(closes, weights, rets):
    out = pd.Series(0.0, index=closes.index)
    for t, w in weights.items():
        if t == "CASH":
            continue
        out = out + w * rets[t]
    return out


def _regime_on(closes: pd.DataFrame, signal_ticker: str, sma_len: int,
               band: float) -> pd.Series:
    """Boolean 'above trend' regime with hysteresis, lagged one day (no look-ahead)."""
    price = closes[signal_ticker]
    sma = price.rolling(sma_len).mean()
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
    return pd.Series(states, index=closes.index).shift(1).fillna(False)


def sim_rotation(closes: pd.DataFrame, on_weights: Dict[str, float],
                 off_weights: Dict[str, float], sma_len: int = 150,
                 signal_ticker: str = "TQQQ", band: float = 0.0,
                 initial: float = 100_000) -> pd.Series:
    """
    Trend rotation: hold `on_weights` when signal_ticker is above its SMA (with
    an optional re-entry buffer `band`), else `off_weights`. Weights rebalanced
    daily within a regime. Signal is lagged one day (no look-ahead).
    """
    if not _has(closes, signal_ticker):
        return pd.Series(dtype=float)
    rets = closes.pct_change().fillna(0.0)
    on = _regime_on(closes, signal_ticker, sma_len, band)
    port = _basket_return(closes, on_weights, rets).where(
        on, _basket_return(closes, off_weights, rets))
    equity = initial * (1 + port).cumprod()
    return equity.iloc[sma_len:]  # drop the pre-SMA warmup


def rotation_detail(closes: pd.DataFrame, on_weights: Dict[str, float],
                    off_weights: Dict[str, float], sma_len: int = 150,
                    signal_ticker: str = "TQQQ", band: float = 0.0,
                    initial: float = 100_000) -> pd.DataFrame:
    """Per-day detail table for a trend rotation: regime, value, returns, drawdown,
    and the applied target weight per asset."""
    if not _has(closes, signal_ticker):
        return pd.DataFrame()
    rets = closes.pct_change().fillna(0.0)
    on = _regime_on(closes, signal_ticker, sma_len, band)
    port = _basket_return(closes, on_weights, rets).where(
        on, _basket_return(closes, off_weights, rets))
    equity = initial * (1 + port).cumprod()

    df = pd.DataFrame(index=closes.index)
    df["regime"] = np.where(on, "trend (TQQQ)", "defensive")
    df["portfolio_value"] = equity
    df["daily_return"] = port
    df["cum_return"] = equity / equity.iloc[0] - 1.0
    df["drawdown"] = equity / equity.cummax() - 1.0
    for a in sorted(set(on_weights) | set(off_weights)):
        if a == "CASH":
            continue
        df[f"{a}_weight"] = np.where(on, on_weights.get(a, 0.0), off_weights.get(a, 0.0))
    df = df.iloc[sma_len:].reset_index().rename(columns={"index": "date"})
    if "Date" in df.columns:
        df = df.rename(columns={"Date": "date"})
    df["date"] = pd.to_datetime(df["date"])
    return df


def sim_9sig(closes: pd.DataFrame, reserve_weights: Dict[str, float], tqqq: str = "TQQQ",
             tqqq_w: float = 0.70, growth: float = 0.09, buy_cap: float = 0.90,
             initial: float = 100_000) -> pd.Series:
    """
    Core 9-Sig: each quarter, value-average TQQQ toward base*(1+growth); buys are
    capped at buy_cap of the reserve; sells add to the reserve. Reserve held in
    reserve_weights (summing to 1), earning their returns between rebalances.
    """
    if not _has(closes, tqqq, *reserve_weights.keys()):
        return pd.Series(dtype=float)
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
def drawdown_episodes(closes: pd.DataFrame, base: str = "TQQQ",
                      defensive=("UGL", "BRK-B", "AGG"), min_depth: float = 0.30) -> pd.DataFrame:
    """
    Identify peak→trough drawdown episodes in `base` deeper than `min_depth`, and
    report how each defensive asset performed over the SAME decline leg (peak→
    trough). Positive = it held up / hedged while base fell.

    Columns: Peak, Trough, Recovery, Days down, Recovery days, <base> DD,
    <defensive returns...>, Best hedge.
    """
    if base not in closes.columns:
        return pd.DataFrame()
    px = closes[base].dropna()
    defensive = [d for d in defensive if d in closes.columns]

    peak_p = tr_p = px.iloc[0]
    peak_d = tr_d = px.index[0]
    eps = []

    def _leg(pd_, td_, rec, rec_days):
        row = {"Peak": pd_.date().isoformat(), "Trough": td_.date().isoformat(),
               "Recovery": rec, "Days down": (td_ - pd_).days, "Recovery days": rec_days,
               f"{base} DD": px.loc[td_] / px.loc[pd_] - 1.0}
        for a in defensive:
            try:
                row[a] = closes.loc[td_, a] / closes.loc[pd_, a] - 1.0
            except KeyError:
                row[a] = None
        return row

    for d, p in px.items():
        if p > peak_p:
            if tr_p / peak_p - 1.0 <= -min_depth:
                eps.append(_leg(peak_d, tr_d, d.date().isoformat(), (d - tr_d).days))
            peak_p = tr_p = p
            peak_d = tr_d = d
        elif p < tr_p:
            tr_p, tr_d = p, d
    if tr_p / peak_p - 1.0 <= -min_depth:                 # ongoing (unrecovered)
        eps.append(_leg(peak_d, tr_d, "ongoing", None))

    df = pd.DataFrame(eps)
    if df.empty:
        return df

    def _best(row):
        vals = {a: row[a] for a in defensive if pd.notna(row.get(a))}
        return max(vals, key=vals.get) if vals else "—"

    df["Best hedge"] = df.apply(_best, axis=1)
    cols = (["Peak", "Trough", "Recovery", "Days down", "Recovery days", f"{base} DD"]
            + defensive + ["Best hedge"])
    return df[[c for c in cols if c in df.columns]]


def drawdown_defensive_summary(episodes: pd.DataFrame,
                               defensive=("UGL", "BRK-B", "AGG")) -> Dict:
    """Average defensive return across episodes + how often each was the best hedge."""
    defensive = [d for d in defensive if d in episodes.columns]
    if episodes.empty:
        return {}
    avg = {d: float(episodes[d].mean()) for d in defensive}
    wins = episodes["Best hedge"].value_counts().to_dict() if "Best hedge" in episodes else {}
    best_overall = max(avg, key=avg.get) if avg else None
    return {"avg_return": avg, "best_counts": wins, "best_overall": best_overall,
            "n": int(len(episodes))}


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
