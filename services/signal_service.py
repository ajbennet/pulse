"""
Signal service — apply the live LDR rules to a real/paper portfolio.

Uses the SAME core.rules.step as the backtest, so live recommendations match
backtested behaviour. Given the latest TQQQ close it advances the stored regime
state (peak / exit price), detects stop / re-entry triggers, checks drift vs.
the current regime target, and computes the trades needed to reach target.

No orders are placed — this is advisory. Broker execution can later be wired in
via services.broker.
"""

from typing import Dict, Optional

from core import config, rules
from services import market_data, portfolio_service


def _bootstrap_peak(ldr_state, tqqq_close):
    """If the portfolio has no tracked peak yet, seed it with the latest close."""
    if ldr_state.get("peak") is None and ldr_state.get("regime", "normal") == "normal":
        ldr_state["peak"] = tqqq_close
    return ldr_state


def evaluate(name: str = "default", cfg: Optional[rules.RegimeConfig] = None,
             prices: Optional[Dict[str, float]] = None,
             apply_state: bool = True,
             drift_threshold: float = config.REBALANCE_DRIFT_THRESHOLD) -> Dict:
    """
    Evaluate the LDR rules for portfolio `name` against the latest prices and
    return a signal report with recommended trades. If `apply_state`, the
    advanced regime state is persisted so the peak tracks forward over time.
    """
    cfg = cfg or rules.RegimeConfig()
    p = portfolio_service.load(name)

    closes = market_data.latest_closes_with_dates(config.TICKERS)
    if prices is None:
        prices = {t: closes[t][1] for t in config.TICKERS}
    tqqq_close = prices.get(config.TICKERS[0], float("nan"))
    as_of = closes.get(config.TICKERS[0], (None, None))[0]

    ldr_state = _bootstrap_peak(dict(p["ldr_state"]), tqqq_close)
    prev = rules.RegimeState(ldr_state["regime"], ldr_state["peak"], ldr_state["exit_price"])
    result = rules.step(prev, tqqq_close, cfg)

    target = result.target_weights
    state = portfolio_service.current_state(name, prices, target_weights=target)

    # Drift vs. current-regime targets.
    drift = {pos["ticker"]: pos.get("drift", 0.0) for pos in state["positions"]}
    max_drift = max((abs(d) for d in drift.values()), default=0.0)

    if result.trigger:
        reason = f"Regime switch ({result.trigger}) — move to {prev.regime} → {result.state.regime} targets"
        rebalance = True
    elif max_drift > drift_threshold:
        reason = f"Drift {max_drift:.1%} exceeds {drift_threshold:.0%} threshold"
        rebalance = True
    else:
        reason = f"Within tolerance (max drift {max_drift:.1%})"
        rebalance = False

    recommended = _recommended_trades(state, target, prices) if rebalance else []

    if apply_state:
        portfolio_service.set_ldr_state(name, result.state.regime,
                                        result.state.peak, result.state.exit_price)

    return {
        "as_of_date": as_of,
        "tqqq_close": tqqq_close,
        "regime_before": prev.regime,
        "regime_after": result.state.regime,
        "trigger": result.trigger,
        "drawdown_from_peak": result.drawdown_from_peak,
        "tracked_peak": result.state.peak,
        "exit_price_reference": prev.exit_price,
        "target_weights": target,
        "drift": drift,
        "max_drift": max_drift,
        "rebalance_recommended": rebalance,
        "reason": reason,
        "recommended_trades": recommended,
        "total_value": state["total_value"],
        "positions": state["positions"],
    }


def _recommended_trades(state, target_weights, prices, min_dollars: float = 1.0):
    """Compute BUY/SELL trades to move current holdings toward target weights."""
    total = state["total_value"]
    trades = []
    by_ticker = {pos["ticker"]: pos for pos in state["positions"]}
    for ticker, tw in target_weights.items():
        price = float(prices.get(ticker, float("nan")))
        if not price or price != price:  # skip missing/NaN price
            continue
        pos = by_ticker.get(ticker, {"shares": 0.0})
        target_value = tw * total
        target_shares = target_value / price
        delta = target_shares - pos["shares"]
        if abs(delta * price) < min_dollars:
            continue
        trades.append({
            "ticker": ticker,
            "action": "BUY" if delta > 0 else "SELL",
            "shares": round(abs(delta), 4),
            "est_price": round(price, 4),
            "est_value": round(abs(delta) * price, 2),
        })
    return trades
