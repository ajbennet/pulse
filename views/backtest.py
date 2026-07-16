"""Sentinel backtest — configure, run, and explore the LDR strategy day by day.

Sentinel = Leveraged Drawdown Reduction (LDR): 70/15/15 TQQQ/UGL/BRK-B that
rotates out of TQQQ into 50/50 UGL/BRK-B on a 30% drawdown, re-entering when
TQQQ reclaims the exit price.
"""

import pandas as pd
import streamlit as st

from core import config
from ui import drawdowns, helpers

STRATEGY_NAME = "Sentinel"

st.title(f"🛡️ {STRATEGY_NAME} Backtest")
st.caption("Leveraged Drawdown Reduction (LDR) — 70/15/15 TQQQ/UGL/BRK-B with a 30% "
           "TQQQ drawdown stop into a 50/50 UGL/BRK-B defensive sleeve.")

# ----------------------------------------------------------------------
# Sidebar config
# ----------------------------------------------------------------------
with st.sidebar:
    st.header(f"{STRATEGY_NAME} configuration")
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
    if abs(n_tqqq + n_ugl + n_brk - 1.0) > 1e-6:
        st.warning(f"Weights sum to {n_tqqq + n_ugl + n_brk:.2f} (should be 1.00).")
    run_benchmark = st.checkbox("Compare vs 70/15/15 benchmark", value=True)
    go = st.button("▶ Run backtest", type="primary", use_container_width=True)

settings = {
    "tickers": tuple(config.TICKERS), "start_date": start_date, "end_date": end_date or None,
    "initial_capital": float(initial_capital), "stop_drawdown": stop_dd,
    "rebalance_drift": drift, "cash_buffer": config.CASH_BUFFER,
    "normal_weights": (("TQQQ", n_tqqq), ("UGL", n_ugl), ("BRK-B", n_brk)),
    "defensive_weights": tuple(config.DEFENSIVE_WEIGHTS.items()), "run_benchmark": run_benchmark,
}

if not go and "bt_result" not in st.session_state:
    st.info("Set parameters in the sidebar and click **Run backtest**.")
    st.stop()
if go:
    st.session_state["bt_result"] = helpers.run_backtest(settings)

result = st.session_state["bt_result"]
m, b = result.metrics, result.benchmark_metrics

# ----------------------------------------------------------------------
# Summary metrics
# ----------------------------------------------------------------------
st.subheader("Summary")
c = st.columns(4)
c[0].metric("Final value", helpers.fmt_money(m["final_value"]))
c[1].metric("CAGR", helpers.fmt_pct(m["cagr"]))
c[2].metric("Max drawdown", helpers.fmt_pct(m["max_drawdown"]))
c[3].metric("Sharpe", f"{m['sharpe_ratio']:.2f}")
c[0].metric("Annual vol", helpers.fmt_pct(m["annualized_volatility"]))
c[1].metric("Regime switches", m["regime_switches"])
c[2].metric("Total trades", m["total_trades"])
c[3].metric("Time defensive", helpers.fmt_pct(m.get("pct_time_defensive")))

bh = result.bh_metrics
with st.expander("Comparison vs benchmark & buy-and-hold", expanded=True):
    cols = {STRATEGY_NAME: [m["cagr"], m["max_drawdown"], m["sharpe_ratio"], m["final_value"]]}
    if b is not None:
        cols["Benchmark 70/15/15"] = [b["cagr"], b["max_drawdown"], b["sharpe_ratio"],
                                      b["final_value"]]
    if bh is not None:
        cols["B&H TQQQ"] = [bh["cagr"], bh["max_drawdown"], bh["sharpe_ratio"],
                            bh["final_value"]]
    comp = pd.DataFrame(cols, index=["CAGR", "Max drawdown", "Sharpe", "Final value"])
    st.dataframe(comp.style.format({c: (lambda v: f"{v:.2%}") for c in comp.columns},
                                   subset=(["CAGR", "Max drawdown"], slice(None)))
                 .format({c: (lambda v: f"{v:.2f}") for c in comp.columns},
                         subset=(["Sharpe"], slice(None)))
                 .format({c: (lambda v: f"${v:,.0f}") for c in comp.columns},
                         subset=(["Final value"], slice(None))),
                 use_container_width=True)

# ----------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------
st.subheader("Equity curve")
eq = result.equity.copy()
eq["date"] = pd.to_datetime(eq["date"])
chart = eq.set_index("date")[["portfolio_value"]].rename(columns={"portfolio_value": STRATEGY_NAME})
if result.benchmark_equity is not None:
    be = result.benchmark_equity.copy()
    be["date"] = pd.to_datetime(be["date"])
    chart["Benchmark 70/15/15"] = be.set_index("date")["portfolio_value"]
if result.bh_equity is not None:
    bhe = result.bh_equity.copy()
    bhe["date"] = pd.to_datetime(bhe["date"])
    chart["B&H TQQQ"] = bhe.set_index("date")["portfolio_value"]
st.line_chart(chart, height=360)
st.subheader("Drawdown")
st.area_chart(chart / chart.cummax() - 1.0)

# ----------------------------------------------------------------------
# Daily detail — filterable, highlighted, downloadable
# ----------------------------------------------------------------------
st.subheader("Daily detail")
daily = result.daily.copy()
daily["date"] = pd.to_datetime(daily["date"])

f = st.columns(4)
min_move = f[0].slider("Significant move = |daily %| ≥", 0.0, 0.30, 0.05, 0.01)
regimes = f[1].multiselect("Regime", ["normal", "defensive"], default=["normal", "defensive"])
only_moves = f[2].checkbox("Only significant-move days", value=False)
direction = f[3].selectbox("Direction", ["All", "Up only", "Down only"])

dmin, dmax = daily["date"].min().date(), daily["date"].max().date()
dr = st.slider("Date range", min_value=dmin, max_value=dmax, value=(dmin, dmax))

view = daily[(daily["date"].dt.date >= dr[0]) & (daily["date"].dt.date <= dr[1])]
if regimes:
    view = view[view["regime"].isin(regimes)]
if only_moves:
    view = view[view["daily_return"].abs() >= min_move]
if direction == "Up only":
    view = view[view["daily_return"] > 0]
elif direction == "Down only":
    view = view[view["daily_return"] < 0]

# Move summary.
sig = view[view["daily_return"].abs() >= min_move]
s = st.columns(4)
s[0].metric("Rows shown", f"{len(view):,} / {len(daily):,}")
s[1].metric(f"Significant days (≥{min_move:.0%})", f"{len(sig):,}")
if not view.empty:
    s[2].metric("Best day", helpers.fmt_pct(view["daily_return"].max()))
    s[3].metric("Worst day", helpers.fmt_pct(view["daily_return"].min()))

# Downloads first (full + filtered), then the collapsible-by-quarter table.
d1, d2 = st.columns(2)
d1.download_button(f"⬇ Filtered rows ({len(view):,}) CSV", view.to_csv(index=False),
                   f"{STRATEGY_NAME.lower()}_daily_filtered.csv", "text/csv")
d2.download_button(f"⬇ Full daily table ({len(daily):,}) CSV", daily.to_csv(index=False),
                   f"{STRATEGY_NAME.lower()}_daily.csv", "text/csv")

pct_cols = [c for c in view.columns if c.endswith("return") or c.endswith("weight")
            or c == "drawdown"]
money_cols = [c for c in view.columns if c.endswith("_value") or c == "portfolio_value"
              or c == "cash"]
fmt = {**{c: "{:.2%}" for c in pct_cols}, **{c: "${:,.0f}" for c in money_cols}}


def _hl(row):
    r = row["daily_return"]
    if abs(r) >= min_move:
        color = "rgba(38,166,91,0.22)" if r > 0 else "rgba(229,57,53,0.22)"
        return [f"background-color: {color}"] * len(row)
    return [""] * len(row)


daily["quarter"] = daily["date"].dt.to_period("Q").astype(str)
view = view.assign(quarter=view["date"].dt.to_period("Q").astype(str))
qagg = daily.groupby("quarter")["portfolio_value"].agg(["first", "last"])
qagg["ret"] = qagg["last"] / qagg["first"] - 1.0

st.caption("Rows grouped by quarter — collapsed by default; expand a quarter to inspect its "
           "days. Green/red = significant up/down day. Use the date range to limit quarters.")
quarters = sorted(view["quarter"].unique(), reverse=True)
if len(quarters) > 120:
    st.info(f"{len(quarters)} quarters after filtering — narrow the date range for speed.")
for q in quarters:
    sub = view[view["quarter"] == q].drop(columns=["quarter"])
    nsig = int((sub["daily_return"].abs() >= min_move).sum())
    r = qagg.loc[q, "ret"] if q in qagg.index else float("nan")
    with st.expander(f"{q} · {len(sub)} days · quarter {r:+.1%} · {nsig} big day(s)",
                     expanded=False):
        st.dataframe(sub.style.format(fmt, na_rep="—").apply(_hl, axis=1),
                     use_container_width=True, hide_index=True)

# ----------------------------------------------------------------------
# Defensive assets during TQQQ drawdowns
# ----------------------------------------------------------------------
st.subheader("Defensive assets during TQQQ drawdowns")
_bt_start = str(pd.to_datetime(result.equity["date"]).min().date())
_bt_end = str(pd.to_datetime(result.equity["date"]).max().date())
drawdowns.render("bt", start=_bt_start, end=_bt_end)

# ----------------------------------------------------------------------
# Logs
# ----------------------------------------------------------------------
t_regime, t_trades, t_annual = st.tabs(["Regime log", "Trade log", "Annual returns"])
with t_regime:
    st.dataframe(result.regimes.replace("", pd.NA), use_container_width=True, height=300)
    st.download_button("Download regime_log.csv", result.regimes.to_csv(index=False),
                       "regime_log.csv", "text/csv")
with t_trades:
    st.dataframe(result.trades, use_container_width=True, height=300)
    st.download_button("Download trade_log.csv", result.trades.to_csv(index=False),
                       "trade_log.csv", "text/csv")
with t_annual:
    st.dataframe(result.annual, use_container_width=True, height=300)
    st.download_button("Download annual_returns.csv", result.annual.to_csv(index=False),
                       "annual_returns.csv", "text/csv")
