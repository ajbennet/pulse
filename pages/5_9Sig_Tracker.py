"""9-Sig Tracker page — live-refreshing signal, per-account allocation, quarter close."""

from datetime import datetime

import pandas as pd
import streamlit as st

from services import market_data, nine_sig
from storage.sqlite_store import SqliteStore
from ui import helpers

st.set_page_config(page_title="PULSE · 9-Sig", page_icon="🎯", layout="wide")
st.title("🎯 9-Sig TQQQ Tracker")

store = SqliteStore()
imp = store.latest_import("Holdings_By_Account")
if imp is None:
    st.warning("No 9-Sig data imported yet. Import the workbook with "
               "`services.sheet_import.import_xlsx(...)` first.")
    st.stop()

st.caption(f"Data source: **{imp['source_name']}** · imported {imp['imported_at']}")

# ----------------------------------------------------------------------
# Controls
# ----------------------------------------------------------------------
c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
use_live = c1.toggle("Live prices", value=True,
                     help="Revalue current holdings at today's quotes.")
refresh_label = c2.selectbox("Auto-refresh", ["Off", "30s", "1 min", "5 min"], index=2,
                             disabled=not use_live)
new_contrib = c3.number_input("New contributions this quarter ($)", value=0.0, step=100.0,
                              help="Half of new contributions is added to the signal line.")
_REFRESH = {"Off": None, "30s": 30, "1 min": 60, "5 min": 300}
run_every = _REFRESH[refresh_label] if use_live else None


@st.fragment(run_every=run_every)
def live_view():
    prices = None
    if use_live:
        live = market_data.latest_prices(["TQQQ", "AGG", "BRK-B"])
        prices = {"TQQQ": live.get("TQQQ"), "AGG": live.get("AGG"), "BRK.B": live.get("BRK-B")}

    result = nine_sig.evaluate(store=store, prices=prices, new_contributions=new_contrib)
    cfg, snap, sig, alloc = (result["config"], result["snapshot"],
                             result["signal"], result["allocation"])

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.caption(f"Prices: **{'live' if use_live else 'from sheet'}** · "
               f"updated {stamp}" + (f" · auto-refresh {refresh_label}" if run_every else ""))

    # -- Snapshot --
    st.subheader("Portfolio snapshot")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total value", helpers.fmt_money(snap["total_value"]))
    m2.metric("TQQQ", helpers.fmt_money(snap["tqqq_value"]),
              f"{snap['tqqq_alloc']:.1%} (target {cfg.target_tqqq:.0%})")
    m3.metric("Reserve", helpers.fmt_money(snap["reserve_value"]),
              f"{snap['reserve_alloc']:.1%} (target {cfg.target_reserve:.0%})")
    m4.metric("TQQQ price", helpers.fmt_money(snap["prices"].get("TQQQ")))

    if sig["reserve_warning"]:
        st.error(f"🔴 Reserve below minimum ({snap['reserve_alloc']:.1%} < "
                 f"{cfg.min_reserve_warning:.0%}). Buying power is limited.")

    # -- Signal --
    st.subheader("Quarterly signal")
    banner = {"BUY": st.success, "SELL": st.warning}.get(sig["raw_signal"], st.info)
    if sig["trade_amount"]:
        banner(f"### {sig['action']} — {helpers.fmt_money(sig['trade_amount'])} "
               f"(~{sig['est_tqqq_shares']:.2f} TQQQ shares)")
    else:
        banner(f"### {sig['action']} — no trade")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("9% signal line", helpers.fmt_money(sig["signal_line_9"]))
    s2.metric("Modified line", helpers.fmt_money(sig["modified_line"]))
    s3.metric("TQQQ − line", helpers.fmt_money(sig["difference"]))
    s4.metric("90% buy power", helpers.fmt_money(sig["max_buy_power"]))

    ov = sig["overlays"]
    b1, b2, b3 = st.columns(3)
    b1.metric("30-Down active", "YES" if sig["down30_active"] else "no",
              f"dd {helpers.fmt_pct(ov['drawdown_from_high'])} vs 8Q high")
    b2.metric("Spike-Reset", "YES" if sig["spike_reset"] else "no",
              f"Q gain {helpers.fmt_pct(ov['quarterly_gain'])}")
    b3.metric("Personal throttle", f"{cfg.personal_throttle:.0%}")

    if sig["trade_amount"]:
        st.caption(f"Reserve legs — AGG {helpers.fmt_money(sig['agg_reserve_trade'])}, "
                   f"BRK.B {helpers.fmt_money(sig['brkb_reserve_trade'])} "
                   f"(− = sell reserve to fund a TQQQ buy).")
    for note in sig["notes"]:
        st.caption(f"• {note}")

    # -- Detail tabs --
    tab_metrics, tab_alloc, tab_hold, tab_qlog = st.tabs(
        ["Sheet metrics", "Trade allocation", "Holdings", "Quarterly log"])
    with tab_metrics:
        mdf = nine_sig.dashboard_metrics(store)
        if mdf.empty:
            st.info("No dashboard metrics imported yet.")
        else:
            st.caption(f"All {len(mdf)} metrics from the imported Dashboard tab "
                       f"(captured {mdf.attrs.get('captured_at') or '—'}).")
            for section in mdf["Section"].unique():
                sec = mdf[mdf["Section"] == section]
                st.markdown(f"**{section or 'Other'}**")
                st.dataframe(sec[["Metric", "Value"]], use_container_width=True,
                             hide_index=True)
    with tab_alloc:
        st.caption("Total TQQQ trade split across accounts "
                   f"(pro-rata to {'reserves' if sig['action'].startswith('BUY') else 'TQQQ'}).")
        st.dataframe(
            alloc.style.format({"TQQQ Value": "${:,.0f}", "Reserve Value": "${:,.0f}",
                                "TQQQ Trade $": "${:,.2f}", "Est TQQQ Shares": "{:,.4f}"}),
            use_container_width=True,
        )
    with tab_hold:
        hdf = nine_sig.holdings(store)
        show_zero = st.checkbox("Show zero-value rows", value=False, key="show_zero_holdings")
        if not show_zero and "Market Value" in hdf.columns:
            mv = pd.to_numeric(hdf["Market Value"], errors="coerce").fillna(0.0)
            hdf = hdf[mv.abs() > 1e-9]
        st.dataframe(hdf, use_container_width=True, height=380)

    with tab_qlog:
        qdf = store.load_table("Quarterly_Log")
        if qdf.empty:
            st.info("No Quarterly_Log data imported.")
        else:
            # Drop fully-empty columns/rows for readability.
            qdf = qdf.dropna(axis=1, how="all").dropna(axis=0, how="all")
            st.caption(f"{len(qdf)} settlement row(s), {qdf.shape[1]} columns.")
            st.dataframe(qdf, use_container_width=True, height=380)


live_view()

# ----------------------------------------------------------------------
# Signal base & quarter close (full rerun, outside the auto-refresh fragment)
# ----------------------------------------------------------------------
st.divider()
with st.expander("Signal base & quarter close", expanded=False):
    cfg = nine_sig.load_config(store)
    st.metric("Current signal base", helpers.fmt_money(cfg.signal_base),
              help="Rolls forward each quarter close; otherwise the Inputs starting base.")
    st.caption(f"Next 9% line would be **{helpers.fmt_money(cfg.signal_base * (1 + cfg.growth_target))}**.")

    cc1, cc2 = st.columns(2)
    with cc1:
        q_contrib = st.number_input("Contributions during the closing quarter ($)",
                                    value=0.0, step=100.0, key="close_contrib")
        q_label = st.text_input("Quarter label (optional)", placeholder="e.g. 2026-Q2")
        if st.button("📅 Close quarter (roll base forward)", type="primary"):
            rec = nine_sig.close_quarter(store, new_contributions=q_contrib,
                                         effective_quarter=q_label)
            st.success(f"New signal base: {helpers.fmt_money(rec['base'])}")
            st.rerun()
    with cc2:
        st.caption("Reset the base back to the sheet's Inputs starting value.")
        if st.button("↩ Reset base to Inputs value"):
            nine_sig.reset_signal_base(store)
            st.rerun()

    hist = nine_sig.base_history(store)
    if hist:
        st.dataframe(pd.DataFrame(hist), use_container_width=True)

st.caption("Advisory only — recomputed from imported holdings + Inputs config. Not investment advice.")
