"""
PULSE — 9-Sig TQQQ Tracker (main app).

Consolidated hub: one page with tabs for the signal, quarterly activity,
transactions/import, holdings, and sheet metrics. Run with:

    streamlit run app.py
"""

import streamlit as st

from storage.sqlite_store import SqliteStore
from ui import ninesig

st.set_page_config(page_title="9-Sig Tracker", page_icon="🎯", layout="wide")
st.title("🎯 9-Sig TQQQ Tracker")

store = SqliteStore()
imp = store.latest_import("Holdings_By_Account")
if imp is None:
    st.warning("No 9-Sig data imported yet. Import the workbook with "
               "`services.sheet_import.import_xlsx(...)`.")
    st.stop()

st.caption(f"Source: **{imp['source_name']}** · imported {imp['imported_at']} · "
           "research/portfolio-assistance only, not investment advice.")

tab_overview, tab_signal, tab_quarterly, tab_txns, tab_holdings, tab_metrics = st.tabs(
    ["Overview", "Signal", "Quarterly", "Transactions", "Holdings", "Metrics"])

with tab_overview:
    ninesig.render_overview(store)
with tab_signal:
    ninesig.render_signal(store)
with tab_quarterly:
    ninesig.render_quarterly(store)
with tab_txns:
    ninesig.render_transactions(store)
with tab_holdings:
    ninesig.render_holdings(store)
with tab_metrics:
    ninesig.render_metrics(store)
