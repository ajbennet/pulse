"""
Render helpers for the consolidated 9-Sig Tracker hub (app.py).

Each function renders one tab so the page file stays thin. Everything reads the
imported workbook + transaction ledger from SQLite via the services.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

from services import market_data, nine_sig, quarterly
from services import statement_parser as sp
from services import transactions_service as tx
from ui import helpers


# ----------------------------------------------------------------------
# Overview: snapshot + signal + alerts
# ----------------------------------------------------------------------
def render_overview(store):
    c1, c2 = st.columns([1, 3])
    use_live = c1.toggle("Live prices", value=True, key="ov_live")
    if c2.button("🔄 Refresh"):
        helpers.latest_prices.clear()

    prices = None
    if use_live:
        live = market_data.latest_prices(["TQQQ", "AGG", "BRK-B"])
        prices = {"TQQQ": live.get("TQQQ"), "AGG": live.get("AGG"), "BRK.B": live.get("BRK-B")}

    result = nine_sig.evaluate(store=store, prices=prices)
    cfg, snap, sig = result["config"], result["snapshot"], result["signal"]
    st.session_state["_ninesig_result"] = result  # reused by Signal/Alerts tabs

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.caption(f"Prices: **{'live' if use_live else 'from sheet'}** · updated {stamp}")

    # High-level snapshot — formatted tiles.
    st.subheader("High-level snapshot")
    m = st.columns(4)
    m[0].metric("Total portfolio", helpers.fmt_money(snap["total_value"]))
    m[1].metric("TQQQ value", helpers.fmt_money(snap["tqqq_value"]),
                f"{snap['tqqq_alloc']:.2%} (target {cfg.target_tqqq:.0%})")
    m[2].metric("Reserve value", helpers.fmt_money(snap["reserve_value"]),
                f"{snap['reserve_alloc']:.2%} (target {cfg.target_reserve:.0%})")
    m[3].metric("TQQQ price", helpers.fmt_money(snap["prices"].get("TQQQ")))
    m2 = st.columns(4)
    m2[0].metric("AGG value", helpers.fmt_money(snap["agg_value"]))
    m2[1].metric("BRK.B value", helpers.fmt_money(snap["brkb_value"]))
    m2[2].metric("Cash", helpers.fmt_money(snap["cash"]))
    m2[3].metric("Reserve health",
                 "🔴 below min" if sig["reserve_warning"] else "🟢 ok",
                 f"min {cfg.min_reserve_warning:.0%}")

    # Signal banner.
    st.subheader("Signal")
    _signal_banner(sig)
    s = st.columns(4)
    s[0].metric("9% signal line", helpers.fmt_money(sig["signal_line_9"]))
    s[1].metric("Modified line", helpers.fmt_money(sig["modified_line"]))
    s[2].metric("TQQQ − line", helpers.fmt_money(sig["difference"]))
    s[3].metric("Trade amount", helpers.fmt_money(sig["trade_amount"]))

    # Inline alerts.
    alerts = _build_alerts(sig, cfg, snap)
    if alerts:
        st.subheader("Alerts")
        for level, msg in alerts:
            {"CRITICAL": st.error, "WARNING": st.warning}.get(level, st.info)(msg)

    # Full imported dashboard metrics, below the overview.
    st.divider()
    render_metrics(store)


def _signal_banner(sig):
    banner = {"BUY": st.success, "SELL": st.warning}.get(sig["raw_signal"], st.info)
    if sig["trade_amount"]:
        banner(f"### {sig['action']} — {helpers.fmt_money(sig['trade_amount'])} "
               f"(~{sig['est_tqqq_shares']:.2f} TQQQ shares)")
    else:
        banner(f"### {sig['action']} — no trade")
    for note in sig["notes"]:
        st.caption(f"• {note}")


def _build_alerts(sig, cfg, snap):
    out = []
    if sig["trigger"] if False else sig["raw_signal"] == "BUY":
        out.append(("WARNING", f"📉 BUY signal — TQQQ is {helpers.fmt_money(-sig['difference'])} "
                    f"below the signal line."))
    elif sig["raw_signal"] == "SELL":
        out.append(("WARNING", f"📈 SELL signal — TQQQ is {helpers.fmt_money(sig['difference'])} "
                    f"above the signal line."))
    if sig["reserve_warning"]:
        out.append(("CRITICAL", f"🔴 Reserve below minimum "
                    f"({snap['reserve_alloc']:.1%} < {cfg.min_reserve_warning:.0%}) — "
                    "buying power is limited."))
    if sig["down30_active"]:
        out.append(("CRITICAL", "🛑 30-Down active — sell signals are skipped."))
    if sig["spike_reset"]:
        out.append(("WARNING", "⚡ Spike-Reset triggered — move toward "
                    f"{cfg.spike_reset_alloc:.0%} TQQQ."))
    return out


# ----------------------------------------------------------------------
# Signal & allocation detail
# ----------------------------------------------------------------------
def render_signal(store):
    result = st.session_state.get("_ninesig_result") or nine_sig.evaluate(store=store)
    sig, alloc, cfg = result["signal"], result["allocation"], result["config"]
    _signal_banner(sig)

    ov = sig["overlays"]
    b = st.columns(3)
    b[0].metric("30-Down", "YES" if sig["down30_active"] else "no",
                f"dd {helpers.fmt_pct(ov['drawdown_from_high'])} vs 8Q high")
    b[1].metric("Spike-Reset", "YES" if sig["spike_reset"] else "no",
                f"Q gain {helpers.fmt_pct(ov['quarterly_gain'])}")
    b[2].metric("90% buy power", helpers.fmt_money(sig["max_buy_power"]))

    if sig["trade_amount"]:
        st.caption(f"Reserve legs — AGG {helpers.fmt_money(sig['agg_reserve_trade'])}, "
                   f"BRK.B {helpers.fmt_money(sig['brkb_reserve_trade'])}.")
    st.subheader("Per-account trade allocation")
    st.dataframe(
        alloc.style.format({"TQQQ Value": "${:,.0f}", "Reserve Value": "${:,.0f}",
                            "TQQQ Trade $": "${:,.2f}", "Est TQQQ Shares": "{:,.4f}"}),
        use_container_width=True)

    with st.expander("Signal base & quarter close"):
        _render_base_controls(store, cfg)


def _render_base_controls(store, cfg):
    st.metric("Current signal base", helpers.fmt_money(cfg.signal_base))
    st.caption(f"Next 9% line ≈ {helpers.fmt_money(cfg.signal_base * (1 + cfg.growth_target))}.")
    cc1, cc2 = st.columns(2)
    with cc1:
        contrib = st.number_input("Contributions during closing quarter ($)",
                                  value=0.0, step=100.0, key="qc_contrib")
        qlabel = st.text_input("Quarter label", key="qc_label", placeholder="2026-Q2")
        if st.button("📅 Close quarter (roll base)", type="primary"):
            rec = nine_sig.close_quarter(store, new_contributions=contrib, effective_quarter=qlabel)
            st.success(f"New base {helpers.fmt_money(rec['base'])}")
            st.rerun()
    with cc2:
        if st.button("↩ Reset base to Inputs value"):
            nine_sig.reset_signal_base(store)
            st.rerun()
    hist = nine_sig.base_history(store)
    if hist:
        st.dataframe(pd.DataFrame(hist), use_container_width=True)


# ----------------------------------------------------------------------
# Quarterly (derived from ledger)
# ----------------------------------------------------------------------
def render_quarterly(store):
    st.caption("Derived from your transaction ledger, valued at quarter-end prices. "
               "Reflects imported transactions — import all statements for full history.")
    snaps = quarterly.quarterly_snapshots(store)
    if snaps.empty:
        st.info("No transactions in the ledger yet — import statements in the Transactions tab.")
        return

    qtd = quarterly.qtd_context(store)
    if qtd:
        st.subheader(f"Current quarter — {qtd['quarter']}")
        k = st.columns(4)
        k[0].metric("QTD start", qtd["qtd_start_date"])
        k[1].metric("QTD start value", helpers.fmt_money(qtd["qtd_start_value"]))
        k[2].metric("Current value", helpers.fmt_money(qtd["current_total_value"]))
        k[3].metric("QTD contributions", helpers.fmt_money(qtd["qtd_contributions"]))

    st.subheader("All quarters")
    st.dataframe(
        snaps.style.format(
            {"contributions": "${:,.0f}", "tqqq_value": "${:,.0f}",
             "reserve_value": "${:,.0f}", "total_value": "${:,.0f}",
             "tqqq_alloc": "{:.1%}", "qoq_change": "${:,.0f}"},
            na_rep="—"),
        use_container_width=True, hide_index=True)

    sel = st.selectbox("Quarter activity", snaps["quarter"].tolist(),
                       index=len(snaps) - 1)
    act = quarterly.quarter_activity(store, sel)
    st.caption(f"{len(act)} transactions in {sel}")
    st.dataframe(act, use_container_width=True, hide_index=True, height=320)


# ----------------------------------------------------------------------
# Holdings
# ----------------------------------------------------------------------
def render_holdings(store):
    hdf = nine_sig.holdings(store)
    if hdf.empty:
        st.info("No holdings imported.")
        return
    show_zero = st.checkbox("Show zero-value rows", value=False, key="hold_zero")
    if not show_zero and "Market Value" in hdf.columns:
        mv = pd.to_numeric(hdf["Market Value"], errors="coerce").fillna(0.0)
        hdf = hdf[mv.abs() > 1e-9]
    st.dataframe(hdf, use_container_width=True, height=460)


# ----------------------------------------------------------------------
# Sheet metrics (formatted)
# ----------------------------------------------------------------------
_PCT_KEYS = ("return", "allocation", "throttle", "drawdown", "gain", "dietz", "yield")
_MONEY_KEYS = ("value", "price", "line", "base", "amount", "cash", "flow", "difference",
               "reserve", "portfolio", "contribution", "withdrawal", "income",
               "buying power", "max buy")


def _fmt_metric(metric, value_text, value_num):
    """Format as $/% using the numeric value; infer type from the metric name
    (so it works even when the source text has no $/% symbol). Text/dates pass through."""
    vt = value_text or ""
    if value_num is None or value_num != value_num:   # None/NaN -> keep text
        return vt
    money = lambda v: (f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}")
    if "%" in vt:
        return f"{value_num * 100:.2f}%"
    if "$" in vt:
        return money(value_num)
    name = (metric or "").lower()
    if any(k in name for k in _PCT_KEYS):
        return f"{value_num * 100:.2f}%"
    if any(k in name for k in _MONEY_KEYS):
        return money(value_num)
    return f"{value_num:,.2f}"


def render_metrics(store, heading="Dashboard metrics"):
    mdf = nine_sig.dashboard_metrics(store)
    if mdf.empty:
        st.info("No dashboard metrics imported.")
        return
    st.subheader(heading)
    st.caption(f"All {len(mdf)} metrics from the imported Dashboard "
               f"(captured {mdf.attrs.get('captured_at') or '—'}).")
    mdf = mdf.copy()
    mdf["Value"] = [_fmt_metric(mt, vt, vn)
                    for mt, vt, vn in zip(mdf["Metric"], mdf["Value"], mdf["Number"])]
    for section in mdf["Section"].unique():
        sec = mdf[mdf["Section"] == section]
        st.markdown(f"**{section or 'Other'}**")
        st.dataframe(sec[["Metric", "Value"]], use_container_width=True, hide_index=True)


# ----------------------------------------------------------------------
# Transactions (ledger + import + accounts)
# ----------------------------------------------------------------------
_ACTIONS = ["BUY", "SELL", "CONTRIBUTION", "WITHDRAWAL", "TRANSFER_IN", "TRANSFER_OUT",
            "REINVEST", "DIVIDEND", "INTEREST"]


def render_transactions(store):
    if store.count_transactions() == 0:
        st.info("Transaction ledger is empty.")
        if st.button("Seed from imported sheet"):
            tx.seed_accounts_from_sheet(store)
            res = tx.seed_from_sheet(store)
            st.success(f"Seeded {res['added']} transactions.")
            st.rerun()
        return

    t_ledger, t_import, t_acct = st.tabs(["Ledger (edit)", "Import statement", "Accounts"])
    with t_ledger:
        _render_ledger(store)
    with t_import:
        _render_import(store)
    with t_acct:
        _render_accounts(store)


def _render_ledger(store):
    df = tx.transactions_df(store)
    only_strategy = st.toggle("Strategy tickers only", value=True, key="led_strat")
    view = df.copy()
    if only_strategy and not view.empty:
        view = view[view.apply(lambda r: tx.is_strategy_row(r.get("ticker"), r.get("action")),
                               axis=1)]
    cols = ["id", "date", "account", "ticker", "action", "shares", "price", "fees",
            "cash_flow", "include_9sig", "notes", "source"]
    for c in cols:
        if c not in view.columns:
            view[c] = None
    edited = st.data_editor(
        view[cols], num_rows="dynamic", use_container_width=True, height=420, key="led_editor",
        column_config={
            "id": st.column_config.NumberColumn("id", disabled=True),
            "source": st.column_config.TextColumn("source", disabled=True),
            "include_9sig": st.column_config.CheckboxColumn("9Sig?"),
            "action": st.column_config.SelectboxColumn("action", options=_ACTIONS),
        })
    if st.button("💾 Save changes", type="primary", key="led_save"):
        orig = set(int(i) for i in df["id"].dropna())
        kept = set(int(i) for i in edited["id"].dropna())
        for did in orig - kept:
            store.delete_transaction(did)
        editable = ["date", "account", "ticker", "action", "shares", "price", "fees",
                    "cash_flow", "include_9sig", "notes"]
        added = updated = 0
        for _, row in edited.iterrows():
            fields = {k: (None if pd.isna(row[k]) else row[k]) for k in editable}
            if pd.isna(row["id"]):
                if any(v is not None for v in fields.values()):
                    tx.add_transaction(fields, source="manual", store=store)
                    added += 1
            else:
                store.update_transaction(int(row["id"]), fields)
                updated += 1
        st.success(f"Saved — {added} added, {updated} updated, {len(orig - kept)} deleted.")
        st.rerun()


def _render_import(store):
    st.caption("Upload → extract relevant (TQQQ/AGG/BRK.B/UGL) → review & edit → commit "
               "(deduplicated). Other stocks are dropped.")
    with st.expander("📥 Where to export statements from"):
        st.markdown(
            "- **Robinhood** — [History](https://robinhood.com/account/history) → print to PDF.\n"
            "- **Fidelity** — [Activity](https://digital.fidelity.com/ftgw/digital/portfolio/activity)"
            " → Download → CSV.\n"
            "- **TradeStation** — [Activity]"
            "(https://my.tradestation.com/portfolio/activity?account=12012959) → Trade/Other "
            "Activity CSV (Cash Activity has no trades).")

    up = st.file_uploader("Statement file (CSV or PDF)", type=["csv", "pdf"], key="imp_up")
    if up is None:
        return
    fkey = f"{up.name}:{up.size}"
    if st.session_state.get("imp_key") != fkey:
        parsed = sp.parse_file(up.name, up.getvalue())
        relevant = [r for r in parsed["rows"]
                    if tx.is_strategy_row(r.get("ticker"), r.get("action"))]
        st.session_state["imp_key"] = fkey
        st.session_state["imp_meta"] = {"broker": parsed["broker"], "format": parsed["format"],
                                        "last4": parsed.get("last4"),
                                        "warnings": parsed.get("warnings", []),
                                        "total": len(parsed["rows"]), "kept": len(relevant)}
        st.session_state["imp_rows"] = relevant

    meta = st.session_state["imp_meta"]
    st.write(f"**Detected:** `{meta['broker']}` / `{meta['format']}`"
             + (f", account ending `{meta['last4']}`" if meta.get("last4") else ""))
    for w in meta["warnings"]:
        st.warning(w)
    st.write(f"Extracted **{meta['kept']}** relevant of {meta['total']} parsed.")
    if not st.session_state["imp_rows"]:
        st.error("No relevant transactions extracted.")
        return

    names = [a["name"] for a in store.list_accounts()]
    c1, c2 = st.columns(2)
    default_acct = c1.selectbox("Default account", names or ["(unassigned)"], key="imp_def")
    last4 = c2.text_input("Account last-4", value=meta.get("last4") or "", key="imp_l4")

    src = pd.DataFrame(st.session_state["imp_rows"])
    src["account"] = src.get("account_hint", pd.Series(dtype=str)).fillna("")
    src.loc[src["account"] == "", "account"] = default_acct
    review = src[["date", "account", "ticker", "action", "shares", "price", "fees",
                  "cash_flow", "notes"]]
    opts = sorted(set(names) | set(review["account"].dropna()))
    confirmed = st.data_editor(
        review, num_rows="dynamic", use_container_width=True, height=320, key="imp_editor",
        column_config={"account": st.column_config.SelectboxColumn("account", options=opts),
                       "action": st.column_config.SelectboxColumn("action", options=_ACTIONS)})
    if st.button("✅ Confirm & commit", type="primary", key="imp_commit"):
        rows = confirmed.to_dict("records")
        for r in rows:
            if last4:
                r["account_last4"] = last4
        res = tx.import_rows(rows, source=meta["broker"], store=store)
        st.success(f"Committed {res['added']} • {res['duplicates']} dup • {res['filtered_out']} filtered.")
        for k in ("imp_key", "imp_meta", "imp_rows"):
            st.session_state.pop(k, None)


def _render_accounts(store):
    st.caption("Accounts by name + last-4 (used to route imports and dedupe).")
    acc = pd.DataFrame([dict(a) for a in store.list_accounts()])
    if acc.empty:
        if st.button("Seed accounts from sheet"):
            tx.seed_accounts_from_sheet(store)
            st.rerun()
        return
    show = acc[["name", "last4", "broker", "legacy_id"]]
    edited = st.data_editor(show, num_rows="dynamic", use_container_width=True, key="acct_editor",
                            column_config={"legacy_id": st.column_config.TextColumn(
                                "legacy_id", disabled=True)})
    if st.button("💾 Save accounts", key="acct_save"):
        for _, r in edited.iterrows():
            if pd.isna(r["name"]) or not str(r["name"]).strip():
                continue
            store.upsert_account(
                name=str(r["name"]).strip(),
                last4=(None if pd.isna(r["last4"]) or not str(r["last4"]).strip()
                       else str(r["last4"]).strip()),
                broker=(None if pd.isna(r["broker"]) else str(r["broker"]).strip() or None))
        st.success("Saved.")
        st.rerun()
