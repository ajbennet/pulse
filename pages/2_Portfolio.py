"""Portfolio page — manual / paper portfolio tracking."""

from datetime import date

import pandas as pd
import streamlit as st

from core import config
from services import portfolio_service
from ui import helpers

st.set_page_config(page_title="PULSE · Portfolio", page_icon="💼", layout="wide")
st.title("💼 Portfolio")

# ----------------------------------------------------------------------
# Portfolio selector
# ----------------------------------------------------------------------
names = portfolio_service.list_portfolios()
col_sel, col_new = st.columns([2, 1])
with col_sel:
    name = st.selectbox("Portfolio", names, index=0)
with col_new:
    new_name = st.text_input("Create / switch to")
    if st.button("Create") and new_name:
        portfolio_service.reset(new_name)
        st.rerun()

prices = helpers.latest_prices(config.TICKERS)
p = portfolio_service.load(name)
regime = p["ldr_state"].get("regime", "normal")
target = config.NORMAL_WEIGHTS if regime == "normal" else config.DEFENSIVE_WEIGHTS
state = portfolio_service.current_state(name, prices, target_weights=target)

# ----------------------------------------------------------------------
# Overview
# ----------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total value", helpers.fmt_money(state["total_value"]))
c2.metric("Cash", helpers.fmt_money(state["cash"]),
          help=f"{state['cash_weight']*100:.1f}% of portfolio")
c3.metric("Unrealized P&L", helpers.fmt_money(state["unrealized_pnl"]))
c4.metric("Regime", regime.upper())

st.caption(f"Prices as of latest close · target weights = **{regime}** regime")

pos_df = pd.DataFrame(state["positions"])
if not pos_df.empty:
    pos_df = pos_df[["ticker", "shares", "avg_cost", "price", "value",
                     "weight", "target_weight", "drift", "unrealized_pnl"]]
    st.dataframe(
        pos_df.style.format({
            "shares": "{:,.2f}", "avg_cost": "${:,.2f}", "price": "${:,.2f}",
            "value": "${:,.2f}", "weight": "{:.1%}", "target_weight": "{:.1%}",
            "drift": "{:+.1%}", "unrealized_pnl": "${:,.2f}",
        }),
        use_container_width=True,
    )

# ----------------------------------------------------------------------
# Edit holdings / cash / transactions
# ----------------------------------------------------------------------
st.divider()
edit_tab, txn_tab, log_tab, danger_tab = st.tabs(
    ["Set holdings & cash", "Add transaction", "Transaction log", "Reset"])

with edit_tab:
    st.caption("Directly set current holdings (for paper tracking without full history).")
    with st.form("holdings_form"):
        cash = st.number_input("Cash ($)", value=float(p["cash"]), step=100.0)
        rows = {}
        for t in config.TICKERS:
            h = p["holdings"].get(t, {"shares": 0.0, "avg_cost": 0.0})
            cc1, cc2 = st.columns(2)
            rows[t] = (
                cc1.number_input(f"{t} shares", value=float(h["shares"]), step=1.0, key=f"sh_{t}"),
                cc2.number_input(f"{t} avg cost", value=float(h["avg_cost"]), step=0.01, key=f"ac_{t}"),
            )
        if st.form_submit_button("Save holdings"):
            portfolio_service.set_cash(name, cash)
            for t, (sh, ac) in rows.items():
                portfolio_service.set_holding(name, t, sh, ac)
            st.success("Saved.")
            st.rerun()

with txn_tab:
    with st.form("txn_form"):
        cc1, cc2, cc3 = st.columns(3)
        t_ticker = cc1.selectbox("Ticker", config.TICKERS)
        t_action = cc2.selectbox("Action", ["BUY", "SELL"])
        t_shares = cc3.number_input("Shares", min_value=0.0, step=1.0)
        cc4, cc5 = st.columns(2)
        t_price = cc4.number_input("Price", min_value=0.0,
                                   value=float(prices.get(t_ticker, 0.0) or 0.0), step=0.01)
        t_date = cc5.date_input("Date", value=date.today())
        t_note = st.text_input("Note", "")
        if st.form_submit_button("Record transaction") and t_shares > 0 and t_price > 0:
            portfolio_service.add_transaction(name, t_ticker, t_action, t_shares,
                                              t_price, t_date.isoformat(), t_note)
            st.success(f"Recorded {t_action} {t_shares} {t_ticker} @ {t_price}.")
            st.rerun()

with log_tab:
    txns = pd.DataFrame(state["transactions"])
    if txns.empty:
        st.info("No transactions recorded yet.")
    else:
        st.dataframe(txns, use_container_width=True, height=300)
        st.metric("Realized P&L", helpers.fmt_money(state["realized_pnl"]))

with danger_tab:
    st.warning("This clears holdings, cash, transactions, and LDR state for this portfolio.")
    if st.button("Reset portfolio", type="secondary"):
        portfolio_service.reset(name)
        st.rerun()
