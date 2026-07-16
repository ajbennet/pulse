"""
Quarterly derivation from the transaction ledger.

Builds per-quarter activity and a snapshot of basic metrics (positions, values,
contributions, quarter-over-quarter change) straight from the recorded
transactions, valued with the imported Daily_Prices. Also derives the current
quarter's (QTD) start date and starting value from activity rather than the
sheet's hardcoded Inputs.

Accuracy reflects whatever transactions have been imported — import all broker
statements for a complete history.
"""

from typing import Dict, Optional

import pandas as pd

from services import transactions_service as tx
from storage.sqlite_store import SqliteStore

TICKERS = ["TQQQ", "AGG", "BRK.B"]
_ADD = {"BUY", "TRANSFER_IN", "REINVEST"}
_SUB = {"SELL", "TRANSFER_OUT"}


def _signed_shares(action: str, shares) -> float:
    a = (action or "").upper()
    s = float(shares) if shares not in (None, "") and not pd.isna(shares) else 0.0
    if a in _ADD:
        return s
    if a in _SUB:
        return -s
    return 0.0


def prices_frame(store: SqliteStore) -> pd.DataFrame:
    df = store.load_table("Daily_Prices")
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for c in ("TQQQ Close", "AGG Close", "BRK.B Close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["Date"]).sort_values("Date")


def price_asof(pf: pd.DataFrame, date, ticker: str) -> Optional[float]:
    col = f"{ticker} Close"
    if pf.empty or col not in pf.columns:
        return None
    sub = pf[pf["Date"] <= date]
    vals = sub[col].dropna()
    return float(vals.iloc[-1]) if len(vals) else None


def ledger(store: SqliteStore) -> pd.DataFrame:
    """Transaction ledger with parsed date, normalized ticker, signed shares, quarter."""
    df = tx.transactions_df(store)
    if df.empty:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df["ticker"] = df["ticker"].map(tx.normalize_ticker)
    df["signed_shares"] = df.apply(lambda r: _signed_shares(r["action"], r["shares"]), axis=1)
    df["quarter"] = df["date"].dt.to_period("Q").astype(str)
    return df


def positions_asof(led: pd.DataFrame, date) -> Dict[str, float]:
    sub = led[led["date"] <= date]
    return sub.groupby("ticker")["signed_shares"].sum().to_dict()


def _q_end(quarter: str):
    return pd.Period(quarter, freq="Q").to_timestamp(how="end").normalize()


def _q_start(quarter: str):
    return pd.Period(quarter, freq="Q").to_timestamp(how="start").normalize()


def quarterly_snapshots(store: Optional[SqliteStore] = None) -> pd.DataFrame:
    """
    One row per quarter present in the ledger, valued at quarter-end prices:
    positions, TQQQ/reserve/total value, contributions, and QoQ change.
    """
    store = store or SqliteStore()
    led = ledger(store)
    if led.empty:
        return pd.DataFrame()
    pf = prices_frame(store)
    quarters = sorted(led["quarter"].unique())

    rows, prev_total = [], None
    for q in quarters:
        qend = _q_end(q)
        pos = positions_asof(led, qend)
        vals = {t: pos.get(t, 0.0) * (price_asof(pf, qend, t) or 0.0) for t in TICKERS}
        tqqq = vals["TQQQ"]
        reserve = vals["AGG"] + vals["BRK.B"]
        total = tqqq + reserve
        qtx = led[led["quarter"] == q]
        contrib = qtx.loc[qtx["action"] == "CONTRIBUTION", "cash_flow"].fillna(0).sum()
        rows.append({
            "quarter": q,
            "start": _q_start(q).date().isoformat(),
            "end": qend.date().isoformat(),
            "transactions": int(len(qtx)),
            "contributions": round(float(contrib), 2),
            "tqqq_value": round(tqqq, 2),
            "reserve_value": round(reserve, 2),
            "total_value": round(total, 2),
            "tqqq_alloc": round(tqqq / total, 4) if total else 0.0,
            "qoq_change": round(total - prev_total, 2) if prev_total is not None else None,
        })
        prev_total = total
    return pd.DataFrame(rows)


def quarter_activity(store: SqliteStore, quarter: str) -> pd.DataFrame:
    led = ledger(store)
    if led.empty:
        return pd.DataFrame()
    q = led[led["quarter"] == quarter]
    cols = ["date", "account", "ticker", "action", "shares", "price", "cash_flow",
            "source", "notes"]
    return q[[c for c in cols if c in q.columns]].reset_index(drop=True)


def qtd_context(store: Optional[SqliteStore] = None) -> Dict:
    """
    Derive current-quarter (QTD) start date, starting value (prior quarter-end
    derived value), contributions so far, and latest positions — from activity.
    """
    store = store or SqliteStore()
    snaps = quarterly_snapshots(store)
    if snaps.empty:
        return {}
    current = snaps.iloc[-1]
    prior = snaps.iloc[-2] if len(snaps) >= 2 else None
    return {
        "quarter": current["quarter"],
        "qtd_start_date": current["start"],
        "qtd_start_value": float(prior["total_value"]) if prior is not None else 0.0,
        "qtd_contributions": float(current["contributions"]),
        "current_total_value": float(current["total_value"]),
        "current_tqqq_value": float(current["tqqq_value"]),
        "current_reserve_value": float(current["reserve_value"]),
    }
