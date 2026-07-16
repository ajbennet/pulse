"""Strategy Lab — compare leveraged-TQQQ strategies and tune the trend/lever knobs.

Research/education only — not investment advice. Uses vectorized simulators in
services.compare; 9-Sig here is the core rule set (no 30-Down/spike/contribs).
"""

import pandas as pd
import streamlit as st

from services import backtest_service as bt
from services import compare as C
from ui import helpers

st.title("🧪 Strategy Lab")
st.caption("Compare leveraged-TQQQ strategies and tune the trend/lever knobs. "
           "Vectorized backtests, dividend-adjusted. Research only — not investment advice.")


@st.cache_data(show_spinner="Loading prices…")
def _closes(tickers, start):
    return C.load_closes(list(tickers), start)


@st.cache_data(show_spinner="Running Sentinel (LDR)…")
def _sentinel_equity():
    r = bt.run(bt.RunConfig(run_benchmark=False))
    d = r.daily[["date", "portfolio_value"]]
    return pd.Series(d["portfolio_value"].values, index=pd.to_datetime(d["date"]))


def _assemble(equities: dict, initial: float):
    df = pd.DataFrame(equities).dropna(how="any")
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()
    df = df / df.iloc[0] * initial
    mdf = pd.DataFrame({n: C.metrics(df[n]) for n in df.columns}).T
    return mdf, df


def _fmt_metrics(mdf):
    show = mdf.copy()
    for c in ["CAGR", "Max DD", "Vol", "% underwater"]:
        show[c] = (show[c] * 100).map(lambda v: f"{v:.1f}%")
    show["Final $"] = show["Final $"].map(lambda v: f"${v:,.0f}")
    for c in ["Calmar", "Sharpe"]:
        show[c] = show[c].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
    return show


# ----------------------------------------------------------------------
tab_cmp, tab_lab = st.tabs(["Compare winners", "Tune levers"])

# ======================================================================
# Compare
# ======================================================================
with tab_cmp:
    c1, c2 = st.columns(2)
    window = c1.radio("Window", ["Recent (2021+, incl KMLM/DBMF)", "Long (2010+, honest cycle)"],
                      index=0)
    initial = c2.number_input("Initial investment ($)", 1000, 10_000_000, 10_000, step=1000)
    include_sentinel = st.checkbox("Include Sentinel (LDR)", value=True)

    recent = window.startswith("Recent")
    equities = {}
    if recent:
        cl = _closes(("TQQQ", "AGG", "BRK-B", "UGL", "KMLM", "DBMF"), "2020-12-01")
        equities["SMA150 rot (KMLM/DBMF/UGL)"] = C.sim_rotation(
            cl, {"TQQQ": 1.0}, {"KMLM": 0.3, "DBMF": 0.3, "UGL": 0.4}, 150)
        equities["SMA150 +levers (60% TQQQ)"] = C.sim_rotation(
            cl, {"TQQQ": 0.6, "KMLM": 0.2, "DBMF": 0.2}, {"KMLM": 0.3, "DBMF": 0.3, "UGL": 0.4},
            150, band=0.02)
    else:
        cl = _closes(("TQQQ", "AGG", "BRK-B", "UGL"), "2010-01-01")
        equities["SMA150 → UGL (proxy)"] = C.sim_rotation(cl, {"TQQQ": 1.0}, {"UGL": 1.0}, 150)
        equities["SMA150 +levers (60% TQQQ)"] = C.sim_rotation(
            cl, {"TQQQ": 0.6, "UGL": 0.4}, {"UGL": 1.0}, 150, band=0.02)

    equities["B&H TQQQ"] = C.sim_buyhold(cl, "TQQQ")
    equities["9-Sig (15/15 UGL/BRK.B)"] = C.sim_9sig(cl, {"UGL": 0.5, "BRK-B": 0.5})
    equities["9-Sig (30% AGG)"] = C.sim_9sig(cl, {"AGG": 1.0})
    if include_sentinel:
        equities["Sentinel (LDR)"] = _sentinel_equity()

    mdf, eqdf = _assemble(equities, initial)
    if mdf.empty:
        st.warning("Not enough overlapping data.")
    else:
        st.caption(f"Window {eqdf.index[0].date()} → {eqdf.index[-1].date()} · "
                   f"all rebased to ${initial:,.0f} at the common start.")
        mdf = mdf.sort_values("Calmar", ascending=False)
        st.dataframe(_fmt_metrics(mdf), use_container_width=True)
        st.subheader("Growth of your investment")
        st.line_chart(eqdf, height=340)
        st.subheader("Drawdown")
        st.area_chart(eqdf / eqdf.cummax() - 1.0, height=260)
        st.download_button("⬇ Comparison metrics CSV", mdf.to_csv(),
                           "strategy_comparison.csv", "text/csv")

# ======================================================================
# Tune levers
# ======================================================================
with tab_lab:
    st.caption("Trend rotation with your knobs: hold TQQQ (± partial leverage) above the SMA, "
               "rotate to a defensive basket below it, with a re-entry buffer to cut whipsaw.")
    a, b, c = st.columns(3)
    sma_len = a.slider("SMA length (days)", 50, 250, 150, 5)
    band = b.slider("Re-entry buffer band", 0.0, 0.10, 0.02, 0.01,
                    help="Re-enter only when TQQQ is this % above the SMA (reduces whipsaw).")
    tqqq_w = c.slider("TQQQ weight when 'on'", 0.3, 1.0, 0.6, 0.05,
                      help="< 1.0 de-levers the aggressive sleeve to cut drawdown.")

    st.markdown("**Defensive basket (when below the SMA)** — weights are normalized.")
    d1, d2, d3, d4 = st.columns(4)
    w_kmlm = d1.slider("KMLM", 0.0, 1.0, 0.30, 0.05)
    w_dbmf = d2.slider("DBMF", 0.0, 1.0, 0.30, 0.05)
    w_ugl = d3.slider("UGL", 0.0, 1.0, 0.40, 0.05)
    w_cash = d4.slider("Cash", 0.0, 1.0, 0.0, 0.05)

    e1, e2 = st.columns(2)
    start = e1.text_input("Start date", "2020-12-01")
    initial2 = e2.number_input("Initial investment ($)", 1000, 10_000_000, 10_000, step=1000,
                               key="lab_init")

    defensive = {"KMLM": w_kmlm, "DBMF": w_dbmf, "UGL": w_ugl, "CASH": w_cash}
    total_def = sum(defensive.values()) or 1.0
    defensive = {k: v / total_def for k, v in defensive.items() if v > 0}
    on_weights = {"TQQQ": tqqq_w, **{k: (1 - tqqq_w) * w for k, w in defensive.items()}}

    if st.button("▶ Run", type="primary"):
        need = [t for t in set(list(on_weights) + list(defensive)) if t != "CASH"]
        cl = _closes(tuple(sorted(set(["TQQQ"] + need))), start)
        if cl.empty or "TQQQ" not in cl.columns:
            st.error("Not enough data for that start/basket (KMLM/DBMF begin ~2020-12).")
        else:
            strat_eq = C.sim_rotation(cl, on_weights, defensive, sma_len, band=band,
                                      initial=initial2)
            bh_eq = C.sim_buyhold(cl, "TQQQ", initial=initial2)
            eqdf = pd.DataFrame({"Your strategy": strat_eq, "B&H TQQQ": bh_eq}).dropna()
            eqdf = eqdf / eqdf.iloc[0] * initial2
            mdf = pd.DataFrame({n: C.metrics(eqdf[n]) for n in eqdf.columns}).T
            st.session_state["lab_out"] = (eqdf, mdf, initial2)

    if "lab_out" in st.session_state:
        eqdf, mdf, init_used = st.session_state["lab_out"]
        m = mdf.loc["Your strategy"]
        k = st.columns(4)
        k[0].metric("CAGR", f"{m['CAGR']*100:.1f}%")
        k[1].metric("Max drawdown", f"{m['Max DD']*100:.1f}%")
        k[2].metric("Calmar", f"{m['Calmar']:.2f}" if pd.notna(m["Calmar"]) else "—")
        k[3].metric(f"Final on ${init_used:,.0f}", f"${m['Final $']:,.0f}")
        st.dataframe(_fmt_metrics(mdf), use_container_width=True)
        st.line_chart(eqdf, height=320)
        st.area_chart(eqdf / eqdf.cummax() - 1.0, height=220)
