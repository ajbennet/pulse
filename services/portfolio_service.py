"""
Portfolio service — manual / paper portfolio tracking.

Persists holdings (shares + average cost), cash, an audit log of transactions,
and the live LDR regime state. Computes current weights, drift vs. targets, and
realized/unrealized P&L. Persistence goes through storage.JsonStore, so the
backing store can later become a database without changing callers.
"""

from typing import Dict, List, Optional

from core import config
from storage.json_store import JsonStore

_STORE = JsonStore()
_KEY_PREFIX = "portfolio_"


def _key(name: str) -> str:
    return f"{_KEY_PREFIX}{name}"


def _empty(name: str) -> Dict:
    return {
        "name": name,
        "cash": float(config.INITIAL_CAPITAL),
        # holdings[ticker] = {"shares": float, "avg_cost": float}
        "holdings": {t: {"shares": 0.0, "avg_cost": 0.0} for t in config.TICKERS},
        "transactions": [],
        "realized_pnl": 0.0,
        "ldr_state": {"regime": "normal", "peak": None, "exit_price": None},
    }


def load(name: str = "default") -> Dict:
    return _STORE.load(_key(name), default=_empty(name))


def save(portfolio: Dict) -> None:
    _STORE.save(_key(portfolio["name"]), portfolio)


def reset(name: str = "default") -> Dict:
    p = _empty(name)
    save(p)
    return p


# ----------------------------------------------------------------------
# Mutations
# ----------------------------------------------------------------------
def set_cash(name: str, cash: float) -> Dict:
    p = load(name)
    p["cash"] = float(cash)
    save(p)
    return p


def set_holding(name: str, ticker: str, shares: float, avg_cost: float) -> Dict:
    """Directly set a holding (paper tracking without a full transaction history)."""
    p = load(name)
    p["holdings"][ticker] = {"shares": float(shares), "avg_cost": float(avg_cost)}
    save(p)
    return p


def add_transaction(name: str, ticker: str, action: str, shares: float,
                    price: float, date: str, note: str = "") -> Dict:
    """Record a BUY/SELL, updating shares (moving-average cost), cash, and P&L."""
    action = action.upper()
    shares = float(shares)
    price = float(price)
    p = load(name)
    h = p["holdings"].setdefault(ticker, {"shares": 0.0, "avg_cost": 0.0})
    old_shares, old_cost = h["shares"], h["avg_cost"]

    if action == "BUY":
        new_shares = old_shares + shares
        h["avg_cost"] = ((old_shares * old_cost) + (shares * price)) / new_shares if new_shares else 0.0
        h["shares"] = new_shares
        p["cash"] -= shares * price
    elif action == "SELL":
        p["realized_pnl"] += (price - old_cost) * shares
        h["shares"] = old_shares - shares
        p["cash"] += shares * price
        if h["shares"] <= 1e-9:
            h["shares"] = 0.0
    else:
        raise ValueError(f"Unknown action: {action}")

    p["transactions"].append({
        "date": date, "ticker": ticker, "action": action,
        "shares": shares, "price": price, "note": note,
    })
    save(p)
    return p


def set_ldr_state(name: str, regime: str, peak, exit_price) -> Dict:
    p = load(name)
    p["ldr_state"] = {"regime": regime, "peak": peak, "exit_price": exit_price}
    save(p)
    return p


# ----------------------------------------------------------------------
# Derived views
# ----------------------------------------------------------------------
def current_state(name: str, prices: Dict[str, float],
                  target_weights: Optional[Dict[str, float]] = None) -> Dict:
    """
    Compute the portfolio's current market value, per-asset weights, drift vs.
    target weights, and unrealized/realized P&L given a set of prices.
    """
    p = load(name)
    positions = []
    invested = 0.0
    for ticker, h in p["holdings"].items():
        price = float(prices.get(ticker, float("nan")))
        shares = h["shares"]
        value = shares * price if shares else 0.0
        invested += value if value == value else 0.0  # skip NaN
        unrealized = (price - h["avg_cost"]) * shares if shares else 0.0
        positions.append({
            "ticker": ticker, "shares": shares, "avg_cost": h["avg_cost"],
            "price": price, "value": value, "unrealized_pnl": unrealized,
        })

    total = p["cash"] + invested
    for pos in positions:
        pos["weight"] = (pos["value"] / total) if total else 0.0
        if target_weights is not None:
            tw = target_weights.get(pos["ticker"], 0.0)
            pos["target_weight"] = tw
            pos["drift"] = pos["weight"] - tw

    return {
        "name": name,
        "cash": p["cash"],
        "cash_weight": (p["cash"] / total) if total else 0.0,
        "invested_value": invested,
        "total_value": total,
        "positions": positions,
        "realized_pnl": p["realized_pnl"],
        "unrealized_pnl": sum(pos["unrealized_pnl"] for pos in positions),
        "ldr_state": p["ldr_state"],
        "transactions": p["transactions"],
    }


def list_portfolios() -> List[str]:
    """Names of saved portfolios."""
    import glob
    import os
    names = []
    pattern = os.path.join(_STORE.base_dir, f"{_KEY_PREFIX}*.json")
    for path in glob.glob(pattern):
        base = os.path.basename(path)[len(_KEY_PREFIX):-len(".json")]
        names.append(base)
    return sorted(names) or ["default"]
