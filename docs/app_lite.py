"""
PULSE Lite — runs entirely in your phone's browser (stlite / WebAssembly).

Two tabs:
  • My 9-Sig — on-device live tracker. You enter current holding values + prices;
    it computes the 9-Sig signal. Your data is saved ONLY in this browser
    (localStorage) — nothing is uploaded anywhere.
  • Research — strategy backtests/comparison from the bundled price history.

No server, no live network calls (prices are entered manually to avoid browser
CORS limits). Research only — not investment advice.
"""

import json

import numpy as np
import pandas as pd
import streamlit as st

# Enable synchronous HTTP from Python in the browser (stlite runs in a Web
# Worker where sync XHR is allowed). No-op outside the browser.
try:
    import pyodide_http
    pyodide_http.patch_all()
except Exception:
    pass

st.set_page_config(page_title="PULSE", page_icon="📈", layout="wide")
TRADING_DAYS = 252
PRICES = "prices"


def fetch_quote(symbol, api_key):
    """Current price via Finnhub (CORS-enabled, free key). Works in-browser."""
    import requests
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": symbol, "token": api_key}, timeout=15)
    r.raise_for_status()
    return float(r.json().get("c") or 0.0)


# ----------------------------------------------------------------------
# Browser-local persistence (localStorage); falls back to session for testing
# ----------------------------------------------------------------------
def _localstorage():
    try:
        import js
        return js.localStorage
    except Exception:
        return None


def save_state(key, value):
    ls = _localstorage()
    if ls is not None:
        try:
            ls.setItem(key, json.dumps(value))
            return
        except Exception:
            pass
    st.session_state[key] = value


def load_state(key, default):
    ls = _localstorage()
    if ls is not None:
        try:
            v = ls.getItem(key)
            if v:
                return json.loads(v)
        except Exception:
            pass
    return st.session_state.get(key, default)


# ======================================================================
# 9-Sig signal (pure)
# ======================================================================
def compute_signal(s):
    tqqq = s["tqqq_value"]
    reserve = s["agg_value"] + s["brkb_value"] + s["cash"]
    total = tqqq + reserve
    line9 = s["signal_base"] * (1 + s["growth"])
    modified = line9 + 0.5 * s["contributions"]
    diff = tqqq - modified
    band = s["hold_band"] * modified
    max_buy = s["buy_power"] * reserve
    if diff < -band:
        raw, action = "BUY", "BUY"
        trade = min(abs(diff), max_buy) * s["throttle"]
    elif diff > band:
        raw, action, trade = "SELL", "SELL", abs(diff) * s["throttle"]
    else:
        raw, action, trade = "HOLD", "HOLD", 0.0
    px = s.get("tqqq_price", 0.0)
    return {
        "tqqq": tqqq, "reserve": reserve, "total": total,
        "tqqq_alloc": tqqq / total if total else 0.0,
        "reserve_alloc": reserve / total if total else 0.0,
        "line9": line9, "modified": modified, "difference": diff, "band": band,
        "raw": raw, "action": action, "trade": trade, "max_buy": max_buy,
        "shares": (trade / px) if px else 0.0,
        "reserve_warn": (reserve / total if total else 0.0) < s["min_reserve"],
        "capped": raw == "BUY" and abs(diff) > max_buy,
    }


def render_tracker():
    st.subheader("My 9-Sig — on-device tracker")
    st.caption("Saved only in this browser (localStorage) — nothing is uploaded. "
               "Enter shares + prices; value = shares × price.")

    d = load_state("ninesig", {
        "tqqq_shares": 0.0, "agg_shares": 0.0, "brkb_shares": 0.0, "cash": 0.0,
        "tqqq_price": 0.0, "agg_price": 0.0, "brkb_price": 0.0,
        "signal_base": 230000.0, "contributions": 0.0,
        "growth": 0.09, "hold_band": 0.01, "throttle": 1.0, "buy_power": 0.90,
        "min_reserve": 0.10, "finnhub_key": "",
    })
    d.setdefault("agg_price", 0.0)
    d.setdefault("brkb_price", 0.0)

    st.markdown("**Holdings (shares)**")
    c = st.columns(4)
    d["tqqq_shares"] = c[0].number_input("TQQQ shares", value=float(d.get("tqqq_shares", 0.0)), step=1.0)
    d["agg_shares"] = c[1].number_input("AGG shares", value=float(d.get("agg_shares", 0.0)), step=1.0)
    d["brkb_shares"] = c[2].number_input("BRK.B shares", value=float(d.get("brkb_shares", 0.0)), step=1.0)
    d["cash"] = c[3].number_input("Cash ($)", value=float(d["cash"]), step=100.0)

    st.markdown("**Prices**")
    p = st.columns(4)
    d["tqqq_price"] = p[0].number_input("TQQQ price ($)", value=float(d["tqqq_price"]), step=0.01)
    d["agg_price"] = p[1].number_input("AGG price ($)", value=float(d["agg_price"]), step=0.01)
    d["brkb_price"] = p[2].number_input("BRK.B price ($)", value=float(d["brkb_price"]), step=0.01)
    with p[3]:
        st.write("")
        st.write("")
        if st.button("🔄 Fetch live", use_container_width=True):
            key = (d.get("finnhub_key") or "").strip()
            if not key:
                st.warning("Add a free Finnhub API key below first.")
            else:
                try:
                    d["tqqq_price"] = fetch_quote("TQQQ", key)
                    d["agg_price"] = fetch_quote("AGG", key)
                    d["brkb_price"] = fetch_quote("BRK.B", key)
                    save_state("ninesig", d)
                    st.success("Prices updated.")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Fetch failed: {ex}")

    b = st.columns(2)
    d["signal_base"] = b[0].number_input("Signal base ($)", value=float(d["signal_base"]), step=1000.0,
                                         help="Prior quarter's TQQQ target; grows 9%/quarter.")
    d["contributions"] = b[1].number_input("Contributions this qtr ($)", value=float(d["contributions"]),
                                           step=100.0)

    with st.expander("Live prices, backup & advanced"):
        d["finnhub_key"] = st.text_input(
            "Finnhub API key (free at finnhub.io)", value=d.get("finnhub_key", ""), type="password",
            help="Stored only in this browser. Enables the 🔄 Fetch live button.")
        st.markdown("**Backup / move devices**")
        bcol = st.columns(2)
        bcol[0].download_button("⬇ Export data (JSON)", json.dumps(d, indent=2),
                                "pulse_9sig.json", "application/json", use_container_width=True)
        up = bcol[1].file_uploader("Import data (JSON)", type=["json"])
        if up is not None:
            try:
                incoming = json.loads(up.getvalue().decode("utf-8"))
                d.update(incoming)
                save_state("ninesig", d)
                st.success("Imported. Reloading…")
                st.rerun()
            except Exception as ex:
                st.error(f"Import failed: {ex}")
        st.markdown("**Strategy settings**")
        e = st.columns(5)
        d["growth"] = e[0].number_input("Growth/qtr", value=float(d["growth"]), step=0.01, format="%.2f")
        d["hold_band"] = e[1].number_input("Hold band", value=float(d["hold_band"]), step=0.01, format="%.2f")
        d["throttle"] = e[2].number_input("Throttle", value=float(d["throttle"]), step=0.05, format="%.2f")
        d["buy_power"] = e[3].number_input("Buy-power cap", value=float(d["buy_power"]), step=0.05, format="%.2f")
        d["min_reserve"] = e[4].number_input("Min reserve", value=float(d["min_reserve"]), step=0.05, format="%.2f")

    # value = shares × price
    d["tqqq_value"] = d["tqqq_shares"] * d["tqqq_price"]
    d["agg_value"] = d["agg_shares"] * d["agg_price"]
    d["brkb_value"] = d["brkb_shares"] * d["brkb_price"]

    save_state("ninesig", d)   # auto-persist latest entries
    sig = compute_signal(d)

    m = st.columns(4)
    m[0].metric("Total value", f"${sig['total']:,.0f}")
    m[1].metric("TQQQ", f"${sig['tqqq']:,.0f}", f"{sig['tqqq_alloc']:.1%} (target 70%)")
    m[2].metric("Reserve", f"${sig['reserve']:,.0f}", f"{sig['reserve_alloc']:.1%} (target 30%)")
    m[3].metric("9% signal line", f"${sig['modified']:,.0f}")

    banner = {"BUY": st.success, "SELL": st.warning}.get(sig["raw"], st.info)
    if sig["trade"]:
        extra = f" (~{sig['shares']:.2f} TQQQ shares)" if sig["shares"] else ""
        banner(f"### {sig['action']} ${sig['trade']:,.0f}{extra}")
    else:
        banner(f"### {sig['action']} — within the {d['hold_band']:.0%} band, no trade")

    notes = []
    if sig["capped"]:
        notes.append(f"Buy capped by {d['buy_power']:.0%} of reserve (${sig['max_buy']:,.0f}).")
    if sig["reserve_warn"]:
        st.error(f"🔴 Reserve {sig['reserve_alloc']:.1%} is below the {d['min_reserve']:.0%} minimum — "
                 "buying power is limited.")
    st.caption(f"TQQQ − signal line = ${sig['difference']:,.0f}. " + " ".join(notes))

    if st.button("📅 Close quarter (roll signal base → this quarter's line)"):
        d["signal_base"] = sig["modified"]
        d["contributions"] = 0.0
        save_state("ninesig", d)
        st.success(f"New signal base: ${d['signal_base']:,.0f}")
        st.rerun()


# ======================================================================
# Research (bundled price history)
# ======================================================================
@st.cache_data
def _series(ticker):
    df = pd.read_csv(f"{PRICES}/{ticker}.csv", index_col=0, parse_dates=True)
    s = df["close"] if "close" in df.columns else df.iloc[:, 0]
    s.index = pd.to_datetime(s.index)
    return s


def load(tickers, start):
    df = pd.DataFrame({t: _series(t) for t in tickers})
    df = df[df.index >= pd.to_datetime(start)]
    return df.dropna(how="any")


def metrics(equity):
    equity = equity.dropna()
    ret = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = float((equity / equity.cummax() - 1.0).min())
    sharpe = float(ret.mean() / ret.std() * np.sqrt(TRADING_DAYS)) if ret.std() else 0.0
    return {"CAGR": cagr, "Max DD": dd, "Calmar": (cagr / abs(dd)) if dd else float("nan"),
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
    for p, sv in zip(price.values, sma.values):
        if np.isnan(sv):
            states.append(False)
            continue
        if not state and p > sv * (1 + band):
            state = True
        elif state and p < sv:
            state = False
        states.append(state)
    return pd.Series(states, index=closes.index).shift(1).fillna(False)


def sim_buyhold(closes, ticker, initial):
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


def render_research():
    st.subheader("Strategy research")
    c1, c2 = st.columns(2)
    window = c1.radio("Window", ["Recent (2021+)", "Long (2010+)"], index=0, horizontal=True)
    initial = c2.number_input("Initial ($)", 1000, 10_000_000, 10_000, step=1000, key="res_init")
    recent = window.startswith("Recent")
    if recent:
        cl = load(["TQQQ", "AGG", "BRK-B", "UGL", "KMLM", "DBMF", "QQQM"], "2020-12-01")
        eq = {"SMA150 rot (KMLM/DBMF/UGL)": sim_rotation(cl, {"TQQQ": 1.0}, {"KMLM": .3, "DBMF": .3, "UGL": .4}, initial=initial),
              "SMA150 +levers": sim_rotation(cl, {"TQQQ": .6, "KMLM": .2, "DBMF": .2}, {"KMLM": .3, "DBMF": .3, "UGL": .4}, band=0.02, initial=initial),
              "B&H QQQM": sim_buyhold(cl, "QQQM", initial)}
    else:
        cl = load(["TQQQ", "AGG", "BRK-B", "UGL", "QQQ"], "2010-01-01")
        eq = {"SMA150 → UGL": sim_rotation(cl, {"TQQQ": 1.0}, {"UGL": 1.0}, initial=initial),
              "SMA150 +levers": sim_rotation(cl, {"TQQQ": .6, "UGL": .4}, {"UGL": 1.0}, band=0.02, initial=initial),
              "B&H QQQ": sim_buyhold(cl, "QQQ", initial)}
    eq["B&H TQQQ"] = sim_buyhold(cl, "TQQQ", initial)
    eq["9-Sig (15/15 UGL/BRK.B)"] = sim_9sig(cl, {"UGL": .5, "BRK-B": .5}, initial=initial)
    eq["9-Sig (30% AGG)"] = sim_9sig(cl, {"AGG": 1.0}, initial=initial)

    eqdf = pd.DataFrame(eq).dropna(how="any")
    eqdf = eqdf / eqdf.iloc[0] * initial
    mdf = pd.DataFrame({n: metrics(eqdf[n]) for n in eqdf.columns}).T.sort_values("Calmar", ascending=False)
    show = mdf.copy()
    for col in ["CAGR", "Max DD"]:
        show[col] = (show[col] * 100).map(lambda v: f"{v:.1f}%")
    show["Total growth"] = (show["Total growth"] * 100).map(lambda v: f"{v:+,.0f}%")
    show["Final $"] = show["Final $"].map(lambda v: f"${v:,.0f}")
    for col in ["Calmar", "Sharpe"]:
        show[col] = show[col].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
    st.caption(f"{eqdf.index[0].date()} → {eqdf.index[-1].date()} · rebased to ${initial:,.0f}")
    st.dataframe(show, use_container_width=True)
    st.line_chart(eqdf, height=300)
    st.area_chart(eqdf / eqdf.cummax() - 1.0, height=220)


# ======================================================================
st.title("📈 PULSE")
tab_track, tab_research = st.tabs(["📊 My 9-Sig", "🔬 Research"])
with tab_track:
    render_tracker()
with tab_research:
    render_research()
