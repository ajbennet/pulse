"""Optional password gate.

If a password is configured (Streamlit secret `password` or env `PULSE_PASSWORD`)
the app requires it before rendering — so a hosted deploy of personal financial
data stays private. With no password set (local use), there is no gate.
"""

import os

import streamlit as st


def _configured_password():
    try:
        pw = st.secrets.get("password")
        if pw:
            return str(pw)
    except Exception:
        pass
    return os.environ.get("PULSE_PASSWORD")


def require_auth():
    pw = _configured_password()
    if not pw:                      # no password configured -> local, no gate
        return
    if st.session_state.get("_authed"):
        return
    st.title("🔒 PULSE")
    st.caption("Private — enter the password to continue.")
    entered = st.text_input("Password", type="password")
    if entered:
        if entered == pw:
            st.session_state["_authed"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()
