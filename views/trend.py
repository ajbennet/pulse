"""Trend Backtest — SMA trend rotation on TQQQ (the bake-off leader).

Above the SMA: hold TQQQ (optionally de-levered). Below: rotate to a defensive
basket (managed futures + gold). Adjustable levers: SMA length, re-entry band,
TQQQ weight, defensive mix. Research only — not investment advice.
"""

import pandas as pd
import streamlit as st

from services import compare as C
from ui import drawdowns

STRAT = "SMA Trend Rotation"
st.title(f"📈 {STRAT}")
st.caption("Hold TQQQ above its SMA; rotate to a defensive basket below it. "
           "The bake-off leader for drawdown-adjusted returns. Not investment advice.")

with st.sidebar:
    st.header("Levers")
    sma_len = st.slider("SMA length (days)", 50, 250, 150, 5)
    band = st.slider("Re-entry buffer band", 0.0, 0.10, 0.0, 0.01, format="%.2f",
                     help="Re-enter only when TQQQ is this fraction above the SMA (cuts whipsaw).")
    tqqq_w = st.slider("TQQQ weight when 'on'", 0.3, 1.0, 1.0, 0.05,
                       help="< 1.0 de-levers the aggressive sleeve to cut drawdown.")
    st.subheader("Defensive basket (below SMA)")
    w_kmlm = st.slider("KMLM", 0.0, 1.0, 0.30, 0.05)
    w_dbmf = st.slider("DBMF", 0.0, 1.0, 0.30, 0.05)
    w_ugl = st.slider("UGL", 0.0, 1.0, 0.40, 0.05)
    w_cash = st.slider("Cash", 0.0, 1.0, 0.0, 0.05)
    start = st.text_input("Start date", "2020-12-01",
                          help="KMLM/DBMF begin ~2020-12; earlier starts drop them.")
    initial = st.number_input("Initial investment ($)", 1000, 10_000_000, 10_000, step=1000)
    go = st.button("▶ Run backtest", type="primary", use_container_width=True)

defensive = {"KMLM": w_kmlm, "DBMF": w_dbmf, "UGL": w_ugl, "CASH": w_cash}
tot = sum(defensive.values()) or 1.0
defensive = {k: v / tot for k, v in defensive.items() if v > 0}
on_weights = {"TQQQ": tqqq_w, **{k: (1 - tqqq_w) * w for k, w in defensive.items()}}

if go:
    need = sorted({"TQQQ", *[t for t in defensive if t != "CASH"], *[t for t in on_weights if t != "CASH"]})
    cl = C.load_closes(need, start)
    if cl.empty or "TQQQ" not in cl.columns:
        st.error("Not enough data for that start/basket.")
        st.stop()
    detail = C.rotation_detail(cl, on_weights, defensive, sma_len, band=band, initial=initial)
    eq = pd.Series(detail["portfolio_value"].values, index=detail["date"])
    bh = C.sim_buyhold(cl, "TQQQ", initial=initial).reindex(detail["date"]).ffill()
    bh = bh / bh.iloc[0] * initial
    st.session_state["trend"] = {
        "detail": detail, "eq": eq, "bh": bh, "initial": initial,
        "m": C.metrics(eq), "mbh": C.metrics(bh),
        "start": str(detail["date"].min().date()), "end": str(detail["date"].max().date()),
    }

if "trend" not in st.session_state:
    st.info("Set the levers in the sidebar and click **Run backtest**.")
    st.stop()

R = st.session_state["trend"]
detail, m, mbh = R["detail"], R["m"], R["mbh"]

# ----------------------------------------------------------------------
# Summary + comparison vs Buy & Hold
# ----------------------------------------------------------------------
st.caption(f"Window {R['start']} → {R['end']} · initial ${R['initial']:,.0f}")
c = st.columns(4)
c[0].metric("Final value", f"${m['Final $']:,.0f}")
c[1].metric("CAGR", f"{m['CAGR']*100:.1f}%")
c[2].metric("Max drawdown", f"{m['Max DD']*100:.1f}%")
c[3].metric("Calmar", f"{m['Calmar']:.2f}" if pd.notna(m["Calmar"]) else "—")

with st.expander("Comparison vs Buy & Hold TQQQ", expanded=True):
    comp = pd.DataFrame({
        STRAT: [m["CAGR"], m["Max DD"], m["Calmar"], m["Sharpe"], m["Final $"]],
        "B&H TQQQ": [mbh["CAGR"], mbh["Max DD"], mbh["Calmar"], mbh["Sharpe"], mbh["Final $"]],
    }, index=["CAGR", "Max drawdown", "Calmar", "Sharpe", "Final value"])
    st.dataframe(comp.style.format(
        {c_: (lambda v: f"{v:.2%}") for c_ in comp.columns},
        subset=(["CAGR", "Max drawdown"], slice(None))).format(
        {c_: (lambda v: f"{v:.2f}") for c_ in comp.columns},
        subset=(["Calmar", "Sharpe"], slice(None))).format(
        {c_: (lambda v: f"${v:,.0f}") for c_ in comp.columns},
        subset=(["Final value"], slice(None))), use_container_width=True)

chart = pd.DataFrame({STRAT: R["eq"], "B&H TQQQ": R["bh"]})
st.subheader("Growth")
st.line_chart(chart, height=320)
st.subheader("Drawdown")
st.area_chart(chart / chart.cummax() - 1.0, height=240)

# ----------------------------------------------------------------------
# Daily detail — filters + collapsible by quarter
# ----------------------------------------------------------------------
st.subheader("Daily detail")
f = st.columns(4)
min_move = f[0].slider("Significant move = |daily %| ≥", 0.0, 0.30, 0.05, 0.01)
regs = f[1].multiselect("Regime", sorted(detail["regime"].unique()),
                        default=sorted(detail["regime"].unique()))
only_moves = f[2].checkbox("Only significant-move days", value=False)
direction = f[3].selectbox("Direction", ["All", "Up only", "Down only"])

dmin, dmax = detail["date"].min().date(), detail["date"].max().date()
dr = st.slider("Date range", min_value=dmin, max_value=dmax, value=(dmin, dmax))
view = detail[(detail["date"].dt.date >= dr[0]) & (detail["date"].dt.date <= dr[1])]
if regs:
    view = view[view["regime"].isin(regs)]
if only_moves:
    view = view[view["daily_return"].abs() >= min_move]
if direction == "Up only":
    view = view[view["daily_return"] > 0]
elif direction == "Down only":
    view = view[view["daily_return"] < 0]

st.download_button(f"⬇ Filtered rows ({len(view):,}) CSV", view.to_csv(index=False),
                   "trend_daily_filtered.csv", "text/csv")
st.download_button(f"⬇ Full daily ({len(detail):,}) CSV", detail.to_csv(index=False),
                   "trend_daily.csv", "text/csv")

pct_cols = [c_ for c_ in view.columns if c_.endswith("return") or c_.endswith("weight")
            or c_ == "drawdown"]
fmt = {**{c_: "{:.2%}" for c_ in pct_cols}, "portfolio_value": "${:,.0f}"}


def _hl(row):
    r = row["daily_return"]
    if abs(r) >= min_move:
        color = "rgba(38,166,91,0.22)" if r > 0 else "rgba(229,57,53,0.22)"
        return [f"background-color: {color}"] * len(row)
    return [""] * len(row)


detail["quarter"] = detail["date"].dt.to_period("Q").astype(str)
view = view.assign(quarter=view["date"].dt.to_period("Q").astype(str))
qagg = detail.groupby("quarter")["portfolio_value"].agg(["first", "last"])
qagg["ret"] = qagg["last"] / qagg["first"] - 1.0
st.caption("Grouped by quarter, collapsed by default — expand to inspect days. "
           "Green/red = significant up/down day.")
for q in sorted(view["quarter"].unique(), reverse=True):
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
drawdowns.render("trend", start=R["start"], end=R["end"])
