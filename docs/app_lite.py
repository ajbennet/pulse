"""
PULSE Lite — browser-only strategy research (stlite / WebAssembly).

Runs entirely in the browser: no server, no live network. Reads the bundled,
dividend-adjusted price CSVs (committed under prices/) and runs the same
vectorized strategy math as the full app. Live prices / imports / the personal
9-Sig tracker are NOT here (those need the server app).
"""

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="PULSE Lite", page_icon="📈", layout="wide")
TRADING_DAYS = 252
PRICES = "prices"


# ----------------------------------------------------------------------
# Data (bundled CSVs)
# ----------------------------------------------------------------------
@st.cache_data
def _series(ticker):
    df = pd.read_csv(f"{PRICES}/{ticker}.csv", index_col=0, parse_dates=True)
    s = df["close"] if "close" in df.columns else df.iloc[:, 0]
    s.index = pd.to_datetime(s.index)
    return s


def load(tickers, start):
    data = {t: _series(t) for t in tickers}
    df = pd.DataFrame(data)
    df = df[df.index >= pd.to_datetime(start)]
    return df.dropna(how="any")


# ----------------------------------------------------------------------
# Metrics + simulators (pure pandas/numpy — Pyodide-safe)
# ----------------------------------------------------------------------
def metrics(equity):
    equity = equity.dropna()
    ret = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = equity / equity.cummax() - 1.0
    max_dd = float(dd.min())
    sharpe = float(ret.mean() / ret.std() * np.sqrt(TRADING_DAYS)) if ret.std() else 0.0
    return {"CAGR": cagr, "Max DD": max_dd,
            "Calmar": (cagr / abs(max_dd)) if max_dd else float("nan"),
            "Sharpe": sharpe, "Final $": float(equity.iloc[-1]),
            "Total growth": float(equity.iloc[-1] / equity.iloc[0] - 1.0)}


def _basket(closes, weights, rets):
    out = pd.Series(0.0, index=closes.index)
    for t, w in weights.items():
        if t != "CASH":
            out = out + w * rets[t]
    return out


def _regime_on(closes, sig, sma_len, band):
    price, sma = closes[sig], closes[sig].rolling(sma_len).mean()
    state, states = False, []
    for p, s in zip(price.values, sma.values):
        if np.isnan(s):
            states.append(False)
            continue
        if not state and p > s * (1 + band):
            state = True
        elif state and p < s:
            state = False
        states.append(state)
    return pd.Series(states, index=closes.index).shift(1).fillna(False)


def sim_buyhold(closes, ticker, initial=10000):
    return initial * (1 + closes[ticker].pct_change().fillna(0.0)).cumprod()


def sim_rotation(closes, on_w, off_w, sma_len=150, sig="TQQQ", band=0.0, initial=10000):
    rets = closes.pct_change().fillna(0.0)
    on = _regime_on(closes, sig, sma_len, band)
    port = _basket(closes, on_w, rets).where(on, _basket(closes, off_w, rets))
    return (initial * (1 + port).cumprod()).iloc[sma_len:]


def sim_9sig(closes, reserve_w, tqqq="TQQQ", tqqq_w=0.70, growth=0.09, buy_cap=0.90, initial=10000):
    idx = closes.index
    px = {t: closes[t].values for t in closes.columns}
    tpx = closes[tqqq].values
    sh = (tqqq_w * initial) / tpx[0]
    res = {a: (w * (1 - tqqq_w) * initial) / closes[a].values[0] for a, w in reserve_w.items()}
    base = tqqq_w * initial
    q = pd.Series(idx, index=idx).dt.to_period("Q")
    qend = q != q.shift(-1)
    eq = np.empty(len(idx))
    for i in range(len(idx)):
        tv = sh * tpx[i]
        rv = sum(res[a] * px[a][i] for a in res)
        eq[i] = tv + rv
        if qend.iloc[i] and i > 0:
            target = base * (1 + growth)
            diff = tv - target
            if diff < 0 and rv > 0:
                buy = min(-diff, buy_cap * rv)
                sh += buy / tpx[i]
                for a in res:
                    res[a] -= (buy * (res[a] * px[a][i]) / rv) / px[a][i]
            elif diff > 0:
                sh -= diff / tpx[i]
                for a, w in reserve_w.items():
                    res[a] += (diff * w) / px[a][i]
            base = target
    return pd.Series(eq, index=idx)


def drawdown_episodes(closes, base="TQQQ", defensive=("UGL", "BRK-B", "AGG"), min_depth=0.30):
    if base not in closes.columns:
        return pd.DataFrame()
    px = closes[base].dropna()
    defensive = [d for d in defensive if d in closes.columns]
    peak_p = tr_p = px.iloc[0]
    peak_d = tr_d = px.index[0]
    eps = []

    def leg(pd_, td_, rec):
        row = {"Peak": pd_.date().isoformat(), "Trough": td_.date().isoformat(),
               "Recovery": rec, f"{base} DD": px.loc[td_] / px.loc[pd_] - 1.0}
        for a in defensive:
            try:
                row[a] = closes.loc[td_, a] / closes.loc[pd_, a] - 1.0
            except KeyError:
                row[a] = None
        return row

    for d, p in px.items():
        if p > peak_p:
            if tr_p / peak_p - 1.0 <= -min_depth:
                eps.append(leg(peak_d, tr_d, d.date().isoformat()))
            peak_p = tr_p = p
            peak_d = tr_d = d
        elif p < tr_p:
            tr_p, tr_d = p, d
    if tr_p / peak_p - 1.0 <= -min_depth:
        eps.append(leg(peak_d, tr_d, "ongoing"))
    df = pd.DataFrame(eps)
    if not df.empty:
        df["Best hedge"] = df.apply(
            lambda r: max({a: r[a] for a in defensive if pd.notna(r.get(a))} or {"—": 0},
                          key=lambda k: {a: r[a] for a in defensive if pd.notna(r.get(a))}.get(k, -9)),
            axis=1)
    return df


# ----------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------
st.title("📈 PULSE Lite")
st.caption("Browser-only strategy research (no server, no live data). Uses bundled "
           "historical prices. Research only — not investment advice.")

c1, c2 = st.columns(2)
window = c1.radio("Window", ["Recent (2021+, incl KMLM/DBMF)", "Long (2010+)"], index=0)
initial = c2.number_input("Initial investment ($)", 1000, 10_000_000, 10_000, step=1000)
recent = window.startswith("Recent")

if recent:
    cl = load(["TQQQ", "AGG", "BRK-B", "UGL", "KMLM", "DBMF", "QQQM"], "2020-12-01")
    eq = {
        "SMA150 rot (KMLM/DBMF/UGL)": sim_rotation(cl, {"TQQQ": 1.0}, {"KMLM": .3, "DBMF": .3, "UGL": .4}, initial=initial),
        "SMA150 +levers (60% TQQQ)": sim_rotation(cl, {"TQQQ": .6, "KMLM": .2, "DBMF": .2}, {"KMLM": .3, "DBMF": .3, "UGL": .4}, band=0.02, initial=initial),
        "B&H QQQM": sim_buyhold(cl, "QQQM", initial),
    }
else:
    cl = load(["TQQQ", "AGG", "BRK-B", "UGL", "QQQ"], "2010-01-01")
    eq = {
        "SMA150 → UGL (proxy)": sim_rotation(cl, {"TQQQ": 1.0}, {"UGL": 1.0}, initial=initial),
        "SMA150 +levers (60% TQQQ)": sim_rotation(cl, {"TQQQ": .6, "UGL": .4}, {"UGL": 1.0}, band=0.02, initial=initial),
        "B&H QQQ (Nasdaq-100)": sim_buyhold(cl, "QQQ", initial),
    }
eq["B&H TQQQ"] = sim_buyhold(cl, "TQQQ", initial)
eq["9-Sig (15/15 UGL/BRK.B)"] = sim_9sig(cl, {"UGL": .5, "BRK-B": .5}, initial=initial)
eq["9-Sig (30% AGG)"] = sim_9sig(cl, {"AGG": 1.0}, initial=initial)

eqdf = pd.DataFrame(eq).dropna(how="any")
eqdf = eqdf / eqdf.iloc[0] * initial
mdf = pd.DataFrame({n: metrics(eqdf[n]) for n in eqdf.columns}).T.sort_values("Calmar", ascending=False)

show = mdf.copy()
for c in ["CAGR", "Max DD"]:
    show[c] = (show[c] * 100).map(lambda v: f"{v:.1f}%")
show["Total growth"] = (show["Total growth"] * 100).map(lambda v: f"{v:+,.0f}%")
show["Final $"] = show["Final $"].map(lambda v: f"${v:,.0f}")
for c in ["Calmar", "Sharpe"]:
    show[c] = show[c].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—")

st.caption(f"Window {eqdf.index[0].date()} → {eqdf.index[-1].date()} · rebased to ${initial:,.0f}")
st.dataframe(show, use_container_width=True)
st.subheader("Growth")
st.line_chart(eqdf, height=320)
st.subheader("Drawdown")
st.area_chart(eqdf / eqdf.cummax() - 1.0, height=240)

st.subheader("Defensives during TQQQ drawdowns (2010+)")
dd_all = load(["TQQQ", "UGL", "BRK-B", "AGG"], "2010-01-01")
ep = drawdown_episodes(dd_all, min_depth=0.30)
if not ep.empty:
    fmt = {c: "{:+.1%}" for c in ["TQQQ DD", "UGL", "BRK-B", "AGG"] if c in ep.columns}
    st.dataframe(ep.style.format(fmt, na_rep="—"), use_container_width=True, hide_index=True)
