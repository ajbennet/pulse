"""Backtest page — configure and run an LDR backtest with charts and CSVs."""

import pandas as pd
import streamlit as st

from core import config
from ui import helpers

st.set_page_config(page_title="PULSE · Backtest", page_icon="🧪", layout="wide")
st.title("🧪 Backtest")

# ----------------------------------------------------------------------
# Sidebar: strategy configuration
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("Configuration")
    initial_capital = st.number_input("Initial capital ($)", 1000, 100_000_000,
                                      int(config.INITIAL_CAPITAL), step=1000)
    start_date = st.text_input("Start date", config.START_DATE)
    end_date = st.text_input("End date (blank = latest)", "")

    st.subheader("Thresholds")
    stop_dd = st.slider("Stop drawdown", 0.05, 0.60, config.STOP_DRAWDOWN_THRESHOLD, 0.01)
    drift = st.slider("Rebalance drift", 0.01, 0.25, config.REBALANCE_DRIFT_THRESHOLD, 0.01)

    st.subheader("Normal weights")
    n_tqqq = st.slider("TQQQ", 0.0, 1.0, config.NORMAL_WEIGHTS["TQQQ"], 0.05)
    n_ugl = st.slider("UGL", 0.0, 1.0, config.NORMAL_WEIGHTS["UGL"], 0.05)
    n_brk = st.slider("BRK-B", 0.0, 1.0, config.NORMAL_WEIGHTS["BRK-B"], 0.05)
    n_sum = n_tqqq + n_ugl + n_brk
    if abs(n_sum - 1.0) > 1e-6:
        st.warning(f"Normal weights sum to {n_sum:.2f} (should be 1.00).")

    run_benchmark = st.checkbox("Compare vs 70/15/15 benchmark", value=True)
    go = st.button("▶ Run backtest", type="primary", use_container_width=True)

settings = {
    "tickers": tuple(config.TICKERS),
    "start_date": start_date,
    "end_date": end_date or None,
    "initial_capital": float(initial_capital),
    "stop_drawdown": stop_dd,
    "rebalance_drift": drift,
    "cash_buffer": config.CASH_BUFFER,
    "normal_weights": (("TQQQ", n_tqqq), ("UGL", n_ugl), ("BRK-B", n_brk)),
    "defensive_weights": tuple(config.DEFENSIVE_WEIGHTS.items()),
    "run_benchmark": run_benchmark,
}

if not go and "bt_result" not in st.session_state:
    st.info("Set your parameters in the sidebar and click **Run backtest**.")
    st.stop()

if go:
    st.session_state["bt_result"] = helpers.run_backtest(settings)

result = st.session_state["bt_result"]
m = result.metrics
b = result.benchmark_metrics

# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------
st.subheader("Summary")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Final value", helpers.fmt_money(m["final_value"]))
c2.metric("CAGR", helpers.fmt_pct(m["cagr"]))
c3.metric("Max drawdown", helpers.fmt_pct(m["max_drawdown"]))
c4.metric("Sharpe", f"{m['sharpe_ratio']:.2f}")
c1.metric("Annual vol", helpers.fmt_pct(m["annualized_volatility"]))
c2.metric("Regime switches", m["regime_switches"])
c3.metric("Total trades", m["total_trades"])
c4.metric("Time defensive", helpers.fmt_pct(m.get("pct_time_defensive")))

if b is not None:
    with st.expander("Benchmark comparison (70/15/15, no stop)"):
        comp = pd.DataFrame({
            "LDR": [m["cagr"], m["max_drawdown"], m["sharpe_ratio"], m["final_value"]],
            "Benchmark": [b["cagr"], b["max_drawdown"], b["sharpe_ratio"], b["final_value"]],
        }, index=["CAGR", "Max drawdown", "Sharpe", "Final value"])
        st.dataframe(comp, use_container_width=True)

# ----------------------------------------------------------------------
# Equity + drawdown charts
# ----------------------------------------------------------------------
st.subheader("Equity curve")
eq = result.equity.copy()
eq["date"] = pd.to_datetime(eq["date"])
chart_df = eq.set_index("date")[["portfolio_value"]].rename(columns={"portfolio_value": "LDR"})
if result.benchmark_equity is not None:
    be = result.benchmark_equity.copy()
    be["date"] = pd.to_datetime(be["date"])
    chart_df["Benchmark"] = be.set_index("date")["portfolio_value"]
st.line_chart(chart_df)

st.subheader("Drawdown")
dd = chart_df / chart_df.cummax() - 1.0
st.area_chart(dd)

# ----------------------------------------------------------------------
# Tables + downloads
# ----------------------------------------------------------------------
tab_regime, tab_trades, tab_annual = st.tabs(["Regime log", "Trade log", "Annual returns"])
with tab_regime:
    st.dataframe(result.regimes, use_container_width=True, height=300)
    st.download_button("Download regime_log.csv", result.regimes.to_csv(index=False),
                       "regime_log.csv", "text/csv")
with tab_trades:
    st.dataframe(result.trades, use_container_width=True, height=300)
    st.download_button("Download trade_log.csv", result.trades.to_csv(index=False),
                       "trade_log.csv", "text/csv")
with tab_annual:
    st.dataframe(result.annual, use_container_width=True, height=300)
    st.download_button("Download annual_returns.csv", result.annual.to_csv(index=False),
                       "annual_returns.csv", "text/csv")

st.download_button("⬇ Download daily_equity_curve.csv", result.equity.to_csv(index=False),
                   "daily_equity_curve.csv", "text/csv")
