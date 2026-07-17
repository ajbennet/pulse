"""Shared UI: "how defensives held up during TQQQ drawdowns".

Always analyses the full TQQQ history (2010+), independent of any strategy
window. Defensive assets are user-extendable: type a ticker to add it and every
table updates. A short-history ticker can't truncate the analysis (outer join
on TQQQ's dates; missing values show as "—").
"""

import pandas as pd
import streamlit as st

from services import compare as C

DEFAULT_DEFENSIVE = ["UGL", "BRK-B", "AGG"]
_BASE = "TQQQ"
_START = "2010-01-01"


@st.cache_data(show_spinner="Loading prices…")
def _load_outer(tickers):
    """Full-history closes for the tickers, outer-joined on TQQQ's dates (2010+)."""
    series = {t: C._cached_series(t) for t in tickers}
    df = pd.DataFrame(series)                       # outer join (union of dates)
    df = df[df.index >= pd.to_datetime(_START)]
    return df.dropna(subset=[_BASE])                # keep every TQQQ day; others NaN if missing


def render(key: str):
    ss = st.session_state
    ss.setdefault("dd_defensive", list(DEFAULT_DEFENSIVE))

    st.caption("Peak→trough legs of every TQQQ drawdown since 2010, and how each defensive "
               "asset did over the *same* decline. Green = it held up while TQQQ fell.")

    c1, c2, c3 = st.columns([2, 1, 1])
    add = c1.text_input("Add defensive ticker(s) — comma-separated", key=f"{key}_add",
                        placeholder="e.g. GLD, BTAL, DBMF, KMLM")
    if c2.button("➕ Add", key=f"{key}_addb") and add.strip():
        for t in [x.strip().upper() for x in add.split(",") if x.strip()]:
            if t in ss["dd_defensive"]:
                continue
            try:
                C._cached_series(t)                 # validate it has data
                ss["dd_defensive"].append(t)
            except Exception:
                st.warning(f"Couldn't add '{t}' — no data from the price source.")
        st.rerun()
    if c3.button("↺ Reset", key=f"{key}_reset"):
        ss["dd_defensive"] = list(DEFAULT_DEFENSIVE)
        st.rerun()

    defensive = ss["dd_defensive"]
    st.caption("Comparing defensives: **" + ", ".join(defensive) + "**")

    depth = st.slider("Min TQQQ drawdown to include", 0.10, 0.60, 0.30, 0.05,
                      key=f"{key}_depth", format="%.0f%%")
    try:
        closes = _load_outer(tuple([_BASE] + defensive))
    except Exception as e:
        st.warning(f"Drawdown analysis unavailable: {e}")
        return

    ep = C.drawdown_episodes(closes, base=_BASE, defensive=defensive, min_depth=depth)
    if ep.empty:
        st.info("No TQQQ drawdowns beyond that threshold since 2010.")
        return

    present = [d for d in defensive if d in ep.columns]
    pct_cols = [f"{_BASE} DD"] + present

    def _color(v):
        if pd.isna(v):
            return ""
        return "color:#1a9850;font-weight:600" if v > 0 else "color:#d6604d"

    styler = (ep.style
              .format({c: "{:+.1%}" for c in pct_cols}, na_rep="—")
              .format({"Recovery days": "{:.0f}"}, na_rep="ongoing")
              .applymap(_color, subset=present))
    st.dataframe(styler, use_container_width=True, hide_index=True)

    s = C.drawdown_defensive_summary(ep, defensive=present)
    if s:
        avg = " · ".join(f"**{k}** {v*100:+.1f}%" for k, v in s["avg_return"].items())
        wins = ", ".join(f"{k} ×{c}" for k, c in s["best_counts"].items())
        st.markdown(f"Across **{s['n']}** TQQQ drawdowns ≥{depth:.0%} since 2010 — average over "
                    f"the decline: {avg}. Best-hedge count: {wins}. "
                    f"**Most reliable: {s['best_overall']}.**")
    st.download_button("⬇ Drawdown table CSV", ep.to_csv(index=False),
                       "tqqq_drawdown_defensives.csv", "text/csv", key=f"{key}_dl")
