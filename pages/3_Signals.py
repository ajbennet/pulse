"""Signals page — live LDR rebalance recommendations for a portfolio."""

import pandas as pd
import streamlit as st

from core import config
from services import portfolio_service, signal_service
from services.broker.paper import PaperBroker
from services.broker.base import Order
from ui import helpers

st.set_page_config(page_title="PULSE · Signals", page_icon="📡", layout="wide")
st.title("📡 Live Signals")

names = portfolio_service.list_portfolios()
name = st.selectbox("Portfolio", names, index=0)

st.caption("Applies the same LDR rules as the backtest (`core.rules`) to the "
           "latest close and your current holdings. Advisory only.")

cola, colb = st.columns([1, 1])
apply_state = cola.checkbox("Advance & save tracked peak/regime state", value=False,
                            help="When on, evaluating updates the stored peak so it tracks "
                                 "forward over time. Turn off for a dry-run check.")
evaluate = colb.button("🔄 Evaluate now", type="primary")

if not evaluate and "signal" not in st.session_state:
    st.info("Click **Evaluate now** to compute the current signal.")
    st.stop()

if evaluate:
    st.session_state["signal"] = signal_service.evaluate(name, apply_state=apply_state)

sig = st.session_state["signal"]

# ----------------------------------------------------------------------
# Signal overview
# ----------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("As of", sig["as_of_date"] or "—")
c2.metric("Regime", f"{sig['regime_before']} → {sig['regime_after']}")
c3.metric("TQQQ close", helpers.fmt_money(sig["tqqq_close"]))
c4.metric("Max drift", helpers.fmt_pct(sig["max_drift"]))

if sig["trigger"] == "stop":
    st.error(f"🛑 STOP triggered — {sig['reason']}")
elif sig["trigger"] == "reentry":
    st.success(f"✅ RE-ENTRY available — {sig['reason']}")
elif sig["rebalance_recommended"]:
    st.warning(f"⚖️ Rebalance suggested — {sig['reason']}")
else:
    st.info(f"👍 {sig['reason']}")

# ----------------------------------------------------------------------
# Recommended trades
# ----------------------------------------------------------------------
st.subheader("Recommended trades")
trades = sig["recommended_trades"]
if not trades:
    st.write("No trades needed.")
else:
    tdf = pd.DataFrame(trades)
    st.dataframe(
        tdf.style.format({"shares": "{:,.4f}", "est_price": "${:,.2f}", "est_value": "${:,.2f}"}),
        use_container_width=True,
    )
    st.caption("Target weights: " +
               ", ".join(f"{k} {v:.0%}" for k, v in sig["target_weights"].items()))

    if st.button("📝 Apply as paper trades", type="secondary"):
        broker = PaperBroker(name)
        for tr in trades:
            broker.place_order(Order(ticker=tr["ticker"], action=tr["action"],
                                     shares=tr["shares"], price=tr["est_price"],
                                     note="signal rebalance"))
        st.success(f"Applied {len(trades)} paper trade(s) to '{name}'. See the Portfolio page.")
        st.session_state.pop("signal", None)

with st.expander("Details"):
    st.write({
        "tracked_peak": sig["tracked_peak"],
        "exit_price_reference": sig["exit_price_reference"],
        "drawdown_from_peak": sig["drawdown_from_peak"],
        "drift_by_asset": sig["drift"],
        "total_value": sig["total_value"],
    })
