"""
PULSE — Protected Ultra Leverage Strategy Engine
Streamlit home page.

Run with:
    streamlit run app.py

The multi-page app lives in pages/:
    1_Backtest   — run and analyse LDR backtests
    2_Portfolio  — manual / paper portfolio tracking
    3_Signals    — live LDR rebalance recommendations
    4_Alerts     — stop / re-entry / drift alerts
"""

import streamlit as st

from core import config

st.set_page_config(page_title="PULSE", page_icon="📈", layout="wide")

st.title("📈 PULSE")
st.caption("Protected Ultra Leverage Strategy Engine — LDR (Leveraged Drawdown Reduction)")

st.markdown(
    """
PULSE backtests and helps you manage a leveraged-ETF **drawdown-control**
strategy that rotates out of **TQQQ** during deep drawdowns into defensive
assets (**UGL**, **BRK-B**).

Use the pages in the sidebar:

| Page | What it does |
|------|--------------|
| **Backtest** | Configure thresholds/weights and run a full historical backtest with charts and downloadable CSVs. |
| **Portfolio** | Track a manual / paper portfolio: holdings, cash, transactions, weights, and P&L. |
| **Signals** | Apply the live LDR rules to your holdings and get recommended buy/sell trades. |
| **Alerts** | See stop / re-entry / drift alerts for your portfolio. |
| **9-Sig Tracker** | Your imported 9-Sig strategy: multi-account holdings, the quarterly signal, and per-account trade allocation. |

> ⚠️ Research and portfolio-assistance tool only — **not investment advice.**
Leveraged ETFs carry significant risk. See the README for the full caveats,
including the known whipsaw behaviour of the re-entry rule.
    """
)

with st.expander("Current default configuration"):
    st.write(
        {
            "Universe": config.TICKERS,
            "Normal weights": config.NORMAL_WEIGHTS,
            "Defensive weights": config.DEFENSIVE_WEIGHTS,
            "Stop drawdown": config.STOP_DRAWDOWN_THRESHOLD,
            "Rebalance drift": config.REBALANCE_DRIFT_THRESHOLD,
            "Initial capital": config.INITIAL_CAPITAL,
        }
    )

st.info("Tip: the Backtest page shares the exact same engine and rules "
        "(`core.rules`) as the live Signals page, so backtested and live "
        "behaviour stay consistent.")
