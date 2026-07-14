"""Alerts page — stop / re-entry / drift alerts for a portfolio."""

import streamlit as st

from services import alerts as alerts_service
from services import portfolio_service

st.set_page_config(page_title="PULSE · Alerts", page_icon="🔔", layout="wide")
st.title("🔔 Alerts")

names = portfolio_service.list_portfolios()
name = st.selectbox("Portfolio", names, index=0)

st.caption("Evaluates the live LDR rules and surfaces actionable alerts. "
           "Dispatching writes to logs/alerts.log via the default notifier; "
           "email/desktop/Slack notifiers can be added via the Notifier seam.")

col1, col2 = st.columns(2)
check = col1.button("🔍 Check alerts", type="primary")
notify = col2.button("📨 Check & notify")

_LEVEL = {"CRITICAL": st.error, "WARNING": st.warning, "INFO": st.info}

if check or notify:
    if notify:
        alerts = alerts_service.check_and_notify(name, apply_state=False)
        st.toast("Actionable alerts dispatched to logs/alerts.log")
    else:
        alerts = alerts_service.build_alerts(name)

    for a in alerts:
        _LEVEL.get(a["level"], st.info)(f"**{a['type']}** ({a.get('as_of')}): {a['message']}")
else:
    st.info("Click **Check alerts** to evaluate your portfolio.")
