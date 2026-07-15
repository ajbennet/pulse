"""Transactions page — editable ledger + statement import (strategy tickers only)."""

import pandas as pd
import streamlit as st

from services import statement_parser as sp
from services import transactions_service as tx
from storage.sqlite_store import SqliteStore

st.set_page_config(page_title="PULSE · Transactions", page_icon="🧾", layout="wide")
st.title("🧾 Transactions")

store = SqliteStore()

# Seed from the imported sheet if the ledger is empty.
if store.count_transactions() == 0:
    st.info("The transaction ledger is empty.")
    if st.button("Seed from imported sheet"):
        tx.seed_accounts_from_sheet(store)
        res = tx.seed_from_sheet(store)
        st.success(f"Seeded {res['added']} transactions from the sheet.")
        st.rerun()
    st.stop()

edit_tab, import_tab, acct_tab = st.tabs(["Ledger (edit)", "Import statement", "Accounts"])

# ----------------------------------------------------------------------
# Editable ledger
# ----------------------------------------------------------------------
with edit_tab:
    df = tx.transactions_df(store)
    only_strategy = st.toggle("Strategy tickers only (TQQQ/AGG/BRK.B/UGL + cash)", value=True)
    view = df.copy()
    if only_strategy and not view.empty:
        mask = view.apply(lambda r: tx.is_strategy_row(r.get("ticker"), r.get("action")), axis=1)
        view = view[mask]

    cols = ["id", "date", "account", "ticker", "action", "shares", "price",
            "fees", "cash_flow", "include_9sig", "notes", "source"]
    for c in cols:
        if c not in view.columns:
            view[c] = None
    view = view[cols]

    st.caption(f"{len(view)} transactions. Edit cells, add rows at the bottom, or clear a "
               "row's cells to delete it, then **Save changes**.")
    edited = st.data_editor(
        view, num_rows="dynamic", use_container_width=True, height=420,
        key="txn_editor",
        column_config={
            "id": st.column_config.NumberColumn("id", disabled=True),
            "source": st.column_config.TextColumn("source", disabled=True),
            "include_9sig": st.column_config.CheckboxColumn("9Sig?"),
            "action": st.column_config.SelectboxColumn(
                "action", options=["BUY", "SELL", "CONTRIBUTION", "WITHDRAWAL", "TRANSFER_IN",
                                       "TRANSFER_OUT", "REINVEST",
                                   "DIVIDEND", "INTEREST"]),
        },
    )

    if st.button("💾 Save changes", type="primary"):
        orig_ids = set(int(i) for i in df["id"].dropna())
        kept_ids = set(int(i) for i in edited["id"].dropna())
        deleted_ids = orig_ids - kept_ids
        for did in deleted_ids:
            store.delete_transaction(did)
        editable = ["date", "account", "ticker", "action", "shares", "price",
                    "fees", "cash_flow", "include_9sig", "notes"]
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
        st.success(f"Saved — {added} added, {updated} updated, {len(deleted_ids)} deleted.")
        st.rerun()

# ----------------------------------------------------------------------
# Import a brokerage statement
# ----------------------------------------------------------------------
with import_tab:
    st.caption("**Flow:** upload → extract relevant transactions → review & edit → commit. "
               "Only TQQQ/AGG/BRK.B/UGL trades (plus contributions/income) are extracted; "
               "other stocks are dropped, and duplicates are skipped on commit.")

    with st.expander("📥 Where to export statements from"):
        st.markdown(
            "- **Robinhood** — [Account → History](https://robinhood.com/account/history), "
            "then print the page to PDF (⌘P → *Save as PDF*).\n"
            "- **Fidelity** — [Activity & Orders]"
            "(https://digital.fidelity.com/ftgw/digital/portfolio/activity) → **Download** → CSV.\n"
            "- **TradeStation** — [Portfolio → Activity]"
            "(https://my.tradestation.com/portfolio/activity?account=12012959) → export "
            "**Trade Activity** as CSV. ⚠️ The *Cash Activity* report has no buy/sell trades."
        )

    up = st.file_uploader("Statement file (CSV or PDF)", type=["csv", "pdf"])

    if up is not None:
        # Parse once per uploaded file; keep the extracted rows in session so the
        # editable review table survives reruns.
        fkey = f"{up.name}:{up.size}"
        if st.session_state.get("imp_key") != fkey:
            parsed = sp.parse_file(up.name, up.getvalue())
            relevant = [r for r in parsed["rows"]
                        if tx.is_strategy_row(r.get("ticker"), r.get("action"))]
            st.session_state["imp_key"] = fkey
            st.session_state["imp_meta"] = {
                "broker": parsed["broker"], "format": parsed["format"],
                "last4": parsed.get("last4"), "warnings": parsed.get("warnings", []),
                "total": len(parsed["rows"]), "kept": len(relevant),
            }
            st.session_state["imp_rows"] = relevant

        meta = st.session_state["imp_meta"]
        st.write(f"**Detected:** broker `{meta['broker']}`, format `{meta['format']}`"
                 + (f", account ending `{meta['last4']}`" if meta.get("last4") else ""))
        for w in meta["warnings"]:
            st.warning(w)
        st.write(f"Extracted **{meta['kept']}** relevant of {meta['total']} parsed rows "
                 "(other stocks dropped). Review/fix below, then commit.")

        if not st.session_state["imp_rows"]:
            st.error("No relevant transactions extracted. If this is a PDF, a broker-specific "
                     "adapter is likely needed — share this sample and I'll add it.")
        else:
            accounts = store.list_accounts()
            names = [a["name"] for a in accounts]
            csel1, csel2 = st.columns(2)
            default_acct = csel1.selectbox(
                "Default account (for rows without one)", names or ["(unassigned)"])
            last4 = csel2.text_input("Account last-4 (optional)", value=meta.get("last4") or "")

            # Pre-fill a per-row account from the parser's hint (multi-account files),
            # falling back to the default selection.
            src = pd.DataFrame(st.session_state["imp_rows"])
            src["account"] = src.get("account_hint", pd.Series(dtype=str)).fillna("")
            src.loc[src["account"] == "", "account"] = default_acct
            review = src[["date", "account", "ticker", "action", "shares", "price",
                          "fees", "cash_flow", "notes"]]

            acct_opts = sorted(set(names) | set(review["account"].dropna()))
            confirmed = st.data_editor(
                review, num_rows="dynamic", use_container_width=True, height=320,
                key="imp_editor",
                column_config={
                    "account": st.column_config.SelectboxColumn("account", options=acct_opts),
                    "action": st.column_config.SelectboxColumn(
                        "action", options=["BUY", "SELL", "CONTRIBUTION", "WITHDRAWAL", "TRANSFER_IN",
                                       "TRANSFER_OUT", "REINVEST",
                                           "DIVIDEND", "INTEREST"]),
                },
            )
            st.caption("Accounts are pre-filled per row from the statement. Review the "
                       "custodial/UTMA rows — exclude any account not part of the strategy.")

            if st.button("✅ Confirm & commit", type="primary"):
                rows = confirmed.to_dict("records")
                for r in rows:
                    if last4:
                        r["account_last4"] = last4
                res = tx.import_rows(rows, source=meta["broker"], store=store)
                st.success(f"Committed {res['added']} • {res['duplicates']} duplicates skipped "
                           f"• {res['filtered_out']} filtered.")
                for k in ("imp_key", "imp_meta", "imp_rows"):
                    st.session_state.pop(k, None)

# ----------------------------------------------------------------------
# Accounts (name + last-4 mapping)
# ----------------------------------------------------------------------
with acct_tab:
    st.caption("Map each brokerage account by **name + last-4 digits**. Used to route "
               "imported statements and dedupe across brokers. (Legacy A-IDs kept only "
               "to link the originally imported data.)")
    acc = pd.DataFrame([dict(a) for a in store.list_accounts()])
    if acc.empty:
        if st.button("Seed accounts from sheet"):
            tx.seed_accounts_from_sheet(store)
            st.rerun()
    else:
        show = acc[["name", "last4", "broker", "legacy_id"]].copy()
        edited_acc = st.data_editor(
            show, num_rows="dynamic", use_container_width=True, key="acct_editor",
            column_config={"legacy_id": st.column_config.TextColumn("legacy_id", disabled=True)},
        )
        if st.button("💾 Save accounts"):
            for _, r in edited_acc.iterrows():
                if pd.isna(r["name"]) or not str(r["name"]).strip():
                    continue
                store.upsert_account(
                    name=str(r["name"]).strip(),
                    last4=(None if pd.isna(r["last4"]) or not str(r["last4"]).strip()
                           else str(r["last4"]).strip()),
                    broker=(None if pd.isna(r["broker"]) else str(r["broker"]).strip() or None),
                )
            st.success("Accounts saved.")
            st.rerun()
