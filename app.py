"""
PULSE — entry point.

Uses st.navigation so nav entries have proper titles (the main page shows as
"9-Sig Tracker", not "app"). Run with:

    streamlit run app.py
"""

import streamlit as st

st.set_page_config(page_title="9-Sig Tracker", page_icon="🎯", layout="wide")

tracker = st.Page("views/tracker.py", title="9-Sig Tracker", icon="🎯", default=True)
lab = st.Page("views/lab.py", title="Strategy Lab", icon="🧪")
trend = st.Page("views/trend.py", title="Trend Backtest", icon="📈")

st.navigation([tracker, lab, trend]).run()
