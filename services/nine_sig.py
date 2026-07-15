"""
9-Sig service — the live signal engine for the "9-Sig TQQQ Tracker".

Reads the imported workbook data from SQLite (config from `Inputs`, positions
from `Holdings_By_Account`, price history from `Daily_Prices`) and reproduces
the sheet's quarterly signal:

    modified signal line = prior TQQQ base × (1 + 9%) + ½ × new contributions
    difference           = current TQQQ value − modified signal line
    BUY  if difference < −band, SELL if difference > +band, else HOLD

with the sheet's overlays: 30-Down (skip sells when TQQQ is ≥30% below its
8-quarter high), Spike-Reset (toward 60/40 after a +100% quarter), a 90%
reserve buying-power cap on buys, and a personal throttle. Buys/sells are then
allocated across accounts pro-rata to reserves (buys) or TQQQ (sells).

This is advisory only — no orders are placed.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from storage.sqlite_store import SqliteStore

TICKERS = ["TQQQ", "AGG", "BRK.B"]

# kv key holding the current (rolled-forward) signal base + its history.
KV_BASE = "nine_sig_signal_base"


# ----------------------------------------------------------------------
# Config (from the Inputs tab, with sensible defaults)
# ----------------------------------------------------------------------
@dataclass
class NineSigConfig:
    growth_target: float = 0.09
    hold_band: float = 0.01
    personal_throttle: float = 1.0
    signal_base: float = 230_000.0
    spike_reset_alloc: float = 0.60
    agg_share: float = 0.50
    brkb_share: float = 0.50
    buying_power_throttle: float = 0.90
    down30_threshold: float = -0.30
    down30_sell_skips: float = 2.0
    spike_gain_trigger: float = 1.00
    min_reserve_warning: float = 0.10
    target_tqqq: float = 0.70
    target_reserve: float = 0.30
    prices: Dict[str, float] = field(default_factory=dict)  # stored current prices


_INPUT_KEYS = {
    "Quarterly TQQQ Growth Target": "growth_target",
    "Hold Band / No-Trade Band": "hold_band",
    "Personal Throttle %": "personal_throttle",
    "Prior Quarter / Starting TQQQ Signal Base": "signal_base",
    "Spike Reset Stock Allocation": "spike_reset_alloc",
    "Reserve AGG Share": "agg_share",
    "Reserve BRK.B Share": "brkb_share",
    "90% Buying Power Throttle": "buying_power_throttle",
    "30-Down Drawdown Threshold": "down30_threshold",
    "30-Down Sell Skips": "down30_sell_skips",
    "Spike Reset Quarterly Gain Trigger": "spike_gain_trigger",
    "Minimum Reserve Warning": "min_reserve_warning",
    "Target TQQQ Allocation": "target_tqqq",
    "Target Reserve Allocation": "target_reserve",
}
_PRICE_KEYS = {"Current TQQQ Price": "TQQQ", "Current AGG Price": "AGG",
               "Current BRK.B Price": "BRK.B"}


def inputs_base(store: SqliteStore) -> float:
    """The starting signal base as configured in the sheet's Inputs tab."""
    for r in store.latest_metrics("Inputs"):
        if r["key"] == "Prior Quarter / Starting TQQQ Signal Base" and r["value_num"] is not None:
            return r["value_num"]
    return NineSigConfig.signal_base


def load_config(store: Optional[SqliteStore] = None) -> NineSigConfig:
    store = store or SqliteStore()
    cfg = NineSigConfig()
    for r in store.latest_metrics("Inputs"):
        key, num = r["key"], r["value_num"]
        if key in _INPUT_KEYS and num is not None:
            setattr(cfg, _INPUT_KEYS[key], num)
        if key in _PRICE_KEYS and num is not None:
            cfg.prices[_PRICE_KEYS[key]] = num
    # A rolled-forward base stored in the DB takes precedence over the sheet's
    # starting value, so quarter closes accumulate over time.
    rec = store.load(KV_BASE)
    if rec and rec.get("base") is not None:
        cfg.signal_base = float(rec["base"])
    return cfg


# ----------------------------------------------------------------------
# Signal base persistence & quarter roll-forward
# ----------------------------------------------------------------------
def set_signal_base(store: SqliteStore, base: float, effective_quarter: str = "",
                    note: str = "") -> dict:
    """Manually set the signal base (records history)."""
    rec = store.load(KV_BASE) or {}
    history = rec.get("history", [])
    history.append({"at": datetime.now().isoformat(timespec="seconds"),
                    "prev_base": rec.get("base"), "new_base": float(base),
                    "effective_quarter": effective_quarter, "note": note or "manual set"})
    out = {"base": float(base), "effective_quarter": effective_quarter, "history": history}
    store.save(KV_BASE, out, updated_at=datetime.now().isoformat(timespec="seconds"))
    return out


def reset_signal_base(store: SqliteStore) -> dict:
    """Reset the signal base back to the sheet's Inputs starting value."""
    return set_signal_base(store, inputs_base(store), note="reset to Inputs starting base")


def close_quarter(store: SqliteStore, new_contributions: float = 0.0,
                  effective_quarter: str = "", note: str = "") -> dict:
    """
    Roll the signal base forward for the next quarter:

        new base = current base × (1 + growth_target) + ½ × new contributions

    i.e. the modified signal line that TQQQ was rebalanced to becomes next
    quarter's base. Records history so the roll-forward is auditable/reversible.
    """
    cfg = load_config(store)
    new_base = cfg.signal_base * (1 + cfg.growth_target) + 0.5 * new_contributions
    rec = store.load(KV_BASE) or {}
    history = rec.get("history", [])
    history.append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "prev_base": cfg.signal_base, "new_base": new_base,
        "growth_target": cfg.growth_target, "new_contributions": new_contributions,
        "effective_quarter": effective_quarter, "note": note or "quarter close",
    })
    out = {"base": new_base, "effective_quarter": effective_quarter, "history": history}
    store.save(KV_BASE, out, updated_at=datetime.now().isoformat(timespec="seconds"))
    return out


def base_history(store: SqliteStore) -> List[dict]:
    rec = store.load(KV_BASE) or {}
    return rec.get("history", [])


# ----------------------------------------------------------------------
# Positions
# ----------------------------------------------------------------------
def _num(series):
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def holdings(store: Optional[SqliteStore] = None) -> pd.DataFrame:
    """Per-account holdings table from the sheet."""
    store = store or SqliteStore()
    return store.load_table("Holdings_By_Account")


def dashboard_metrics(store: Optional[SqliteStore] = None) -> pd.DataFrame:
    """
    The full set of imported dashboard metrics (latest 'dashboard' import,
    case-insensitive), as a DataFrame: Section, Metric, Value, Number.
    """
    store = store or SqliteStore()
    with store._conn() as c:
        imp = c.execute(
            "SELECT id, imported_at, captured_at FROM sheet_imports "
            "WHERE lower(tab) = 'dashboard' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not imp:
            return pd.DataFrame(columns=["Section", "Metric", "Value", "Number"])
        rows = c.execute(
            "SELECT section, key, value_text, value_num FROM metrics "
            "WHERE import_id = ? ORDER BY id", (imp["id"],)
        ).fetchall()
    df = pd.DataFrame(
        [(r["section"], r["key"], r["value_text"], r["value_num"]) for r in rows],
        columns=["Section", "Metric", "Value", "Number"],
    )
    df.attrs["imported_at"] = imp["imported_at"]
    df.attrs["captured_at"] = imp["captured_at"]
    return df


def portfolio_snapshot(store: Optional[SqliteStore] = None,
                       prices: Optional[Dict[str, float]] = None) -> Dict:
    """
    Aggregate holdings across all accounts and value them.

    `prices` overrides the stored current prices (e.g. live quotes). Reserve =
    AGG + BRK.B + cash.
    """
    store = store or SqliteStore()
    cfg_prices = load_config(store).prices
    px = {**cfg_prices, **(prices or {})}

    df = holdings(store)
    if df.empty:
        return {}

    df = df.copy()
    df["Shares Held"] = _num(df.get("Shares Held"))
    df["Cash Balance"] = _num(df.get("Cash Balance"))

    def shares(ticker):
        return df.loc[df["Ticker"] == ticker, "Shares Held"].sum()

    tqqq_sh, agg_sh, brkb_sh = shares("TQQQ"), shares("AGG"), shares("BRK.B")
    cash = df.loc[df["Ticker"] == "CASH", "Cash Balance"].sum()

    tqqq_val = tqqq_sh * px.get("TQQQ", 0.0)
    agg_val = agg_sh * px.get("AGG", 0.0)
    brkb_val = brkb_sh * px.get("BRK.B", 0.0)
    reserve = agg_val + brkb_val + cash
    total = tqqq_val + reserve

    return {
        "prices": px,
        "shares": {"TQQQ": tqqq_sh, "AGG": agg_sh, "BRK.B": brkb_sh},
        "tqqq_value": tqqq_val, "agg_value": agg_val, "brkb_value": brkb_val,
        "cash": cash, "reserve_value": reserve, "total_value": total,
        "tqqq_alloc": (tqqq_val / total) if total else 0.0,
        "reserve_alloc": (reserve / total) if total else 0.0,
    }


# ----------------------------------------------------------------------
# 30-Down / Spike-Reset from quarter-end closes
# ----------------------------------------------------------------------
def quarter_end_closes(store: Optional[SqliteStore] = None) -> pd.Series:
    """TQQQ close on the last trading day of each quarter, from Daily_Prices."""
    store = store or SqliteStore()
    df = store.load_table("Daily_Prices")
    if df.empty or "TQQQ Close" not in df.columns:
        return pd.Series(dtype=float)
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["close"] = _num(df["TQQQ Close"])
    df = df.dropna(subset=["Date"]).sort_values("Date")
    df["q"] = df["Date"].dt.to_period("Q")
    return df.groupby("q")["close"].last()


def down30_and_spike(current_tqqq_price: float, store=None) -> Dict:
    """Compute 8-quarter high, drawdown, 30-down flag, and quarterly gain."""
    qec = quarter_end_closes(store)
    if qec.empty:
        return {"high_8q": None, "drawdown_from_high": None, "down30_active": False,
                "prior_q_close": None, "quarterly_gain": None}
    prior_q_close = float(qec.iloc[-1])
    high_8q = float(qec.iloc[-8:].max())
    drawdown = (current_tqqq_price - high_8q) / high_8q if high_8q else 0.0
    quarterly_gain = (current_tqqq_price / prior_q_close - 1.0) if prior_q_close else 0.0
    return {
        "high_8q": high_8q,
        "drawdown_from_high": drawdown,
        "prior_q_close": prior_q_close,
        "quarterly_gain": quarterly_gain,
    }


# ----------------------------------------------------------------------
# Signal
# ----------------------------------------------------------------------
def compute_signal(snapshot: Dict, cfg: NineSigConfig,
                   new_contributions: float = 0.0, store=None) -> Dict:
    """Compute the 9-Sig signal from a portfolio snapshot."""
    tqqq_value = snapshot["tqqq_value"]
    reserve = snapshot["reserve_value"]
    total = snapshot["total_value"]

    signal_line_9 = cfg.signal_base * (1 + cfg.growth_target)
    modified_line = signal_line_9 + 0.5 * new_contributions
    difference = tqqq_value - modified_line
    band = cfg.hold_band * modified_line

    px = snapshot["prices"].get("TQQQ", 0.0)
    overlays = down30_and_spike(px, store)
    down30_active = (overlays["drawdown_from_high"] is not None
                     and overlays["drawdown_from_high"] <= cfg.down30_threshold)
    spike_reset = (not down30_active and overlays["quarterly_gain"] is not None
                   and overlays["quarterly_gain"] >= cfg.spike_gain_trigger)

    # Raw signal from the band.
    if difference < -band:
        raw = "BUY"
    elif difference > band:
        raw = "SELL"
    else:
        raw = "HOLD"

    max_buy = cfg.buying_power_throttle * reserve   # 90% of reserve
    notes = []
    action = raw
    trade_amount = 0.0

    if raw == "BUY":
        trade_amount = min(abs(difference), max_buy) * cfg.personal_throttle
        if abs(difference) > max_buy:
            notes.append(f"Buy capped by {cfg.buying_power_throttle:.0%} reserve buying power.")
    elif raw == "SELL":
        if down30_active:
            action = "HOLD (30-Down skip)"
            notes.append("30-Down active — sell signal skipped.")
        else:
            trade_amount = abs(difference) * cfg.personal_throttle
    else:
        notes.append("Within hold band — no trade.")

    if spike_reset:
        notes.append(f"Spike-Reset triggered (+{overlays['quarterly_gain']:.0%} quarter) "
                     f"→ move toward {cfg.spike_reset_alloc:.0%} TQQQ.")

    reserve_alloc = snapshot["reserve_alloc"]
    reserve_warning = reserve_alloc < cfg.min_reserve_warning

    # Reserve-side legs (buys spend reserve; sells add to reserve).
    sign = -1 if action.startswith("BUY") else (1 if action.startswith("SELL") and trade_amount else 0)
    agg_leg = sign * trade_amount * cfg.agg_share
    brkb_leg = sign * trade_amount * cfg.brkb_share

    return {
        "signal_line_9": signal_line_9,
        "modified_line": modified_line,
        "difference": difference,
        "hold_band_$": band,
        "raw_signal": raw,
        "action": action,
        "max_buy_power": max_buy,
        "personal_throttle": cfg.personal_throttle,
        "trade_amount": trade_amount,
        "est_tqqq_shares": (trade_amount / px) if px else 0.0,
        "agg_reserve_trade": agg_leg,
        "brkb_reserve_trade": brkb_leg,
        "down30_active": down30_active,
        "spike_reset": spike_reset,
        "reserve_warning": reserve_warning,
        "overlays": overlays,
        "notes": notes,
    }


def allocate_trade(snapshot_df: pd.DataFrame, action: str, trade_amount: float,
                   prices: Dict[str, float]) -> pd.DataFrame:
    """
    Split the total TQQQ trade across accounts: buys pro-rata to each account's
    reserve (where the cash comes from), sells pro-rata to each account's TQQQ.
    """
    df = snapshot_df.copy()
    df["Shares Held"] = _num(df.get("Shares Held"))
    df["Cash Balance"] = _num(df.get("Cash Balance"))

    px = prices
    grp = df.groupby(["Account ID", "Account Name"])

    rows = []
    for (aid, aname), g in grp:
        tqqq_val = g.loc[g["Ticker"] == "TQQQ", "Shares Held"].sum() * px.get("TQQQ", 0.0)
        agg_val = g.loc[g["Ticker"] == "AGG", "Shares Held"].sum() * px.get("AGG", 0.0)
        brkb_val = g.loc[g["Ticker"] == "BRK.B", "Shares Held"].sum() * px.get("BRK.B", 0.0)
        cash = g.loc[g["Ticker"] == "CASH", "Cash Balance"].sum()
        reserve = agg_val + brkb_val + cash
        rows.append({"Account ID": aid, "Account Name": aname,
                     "TQQQ Value": tqqq_val, "Reserve Value": reserve})

    adf = pd.DataFrame(rows)
    if adf.empty or not trade_amount:
        adf["TQQQ Trade $"] = 0.0
        return adf

    basis = "Reserve Value" if action.startswith("BUY") else "TQQQ Value"
    weights = adf[basis].clip(lower=0)
    wsum = weights.sum()
    share = weights / wsum if wsum else 0.0
    signed = trade_amount if action.startswith("BUY") else -trade_amount
    adf["TQQQ Trade $"] = (share * signed).round(2)
    adf["Est TQQQ Shares"] = (adf["TQQQ Trade $"] / px.get("TQQQ", 1.0)).round(4)
    return adf


def evaluate(store: Optional[SqliteStore] = None,
             prices: Optional[Dict[str, float]] = None,
             new_contributions: float = 0.0) -> Dict:
    """One-call convenience: snapshot + signal + per-account allocation."""
    store = store or SqliteStore()
    cfg = load_config(store)
    snap = portfolio_snapshot(store, prices=prices)
    if not snap:
        return {"error": "No holdings data imported yet."}
    signal = compute_signal(snap, cfg, new_contributions=new_contributions, store=store)
    alloc = allocate_trade(holdings(store), signal["action"], signal["trade_amount"],
                           snap["prices"])
    return {"config": cfg, "snapshot": snap, "signal": signal, "allocation": alloc}
