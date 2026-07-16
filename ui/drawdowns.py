"""Shared UI: "how defensives held up during TQQQ drawdowns" table + summary."""

import pandas as pd
import streamlit as st

from services import compare as C

_DEFENSIVE = ("UGL", "BRK-B", "AGG")


@st.cache_data(show_spinner="Loading defensive prices…")
def _load(tickers, start):
    return C.load_closes(list(tickers), start)


def render(key: str, closes: pd.DataFrame = None, start: str = None, end: str = None):
    """Render the drawdown-episode table. Pass `closes` (already windowed) or a
    `start` (and optional `end`) to load TQQQ/UGL/BRK-B/AGG."""
    if closes is None:
        try:
            closes = _load(("TQQQ", "UGL", "BRK-B", "AGG"), start or "2010-01-01")
        except Exception as e:
            st.warning(f"Drawdown analysis unavailable (price load failed): {e}")
            return
    if end is not None and not closes.empty:
        closes = closes[closes.index <= pd.to_datetime(end)]

    st.caption("Peak→trough legs of every TQQQ drawdown, and how each defensive asset "
               "did over the *same* decline. Green = it held up while TQQQ fell.")
    depth = st.slider("Min TQQQ drawdown to include", 0.10, 0.60, 0.30, 0.05,
                      key=f"{key}_depth", format="%.0f%%")
    ep = C.drawdown_episodes(closes, min_depth=depth)
    if ep.empty:
        st.info("No TQQQ drawdowns beyond that threshold in this window.")
        return

    defensive = [d for d in _DEFENSIVE if d in ep.columns]
    pct_cols = ["TQQQ DD"] + defensive

    def _color(v):
        if pd.isna(v):
            return ""
        return "color:#1a9850;font-weight:600" if v > 0 else "color:#d6604d"

    styler = (ep.style
              .format({c: "{:+.1%}" for c in pct_cols}, na_rep="—")
              .format({"Recovery days": "{:.0f}"}, na_rep="ongoing")
              .applymap(_color, subset=defensive))
    st.dataframe(styler, use_container_width=True, hide_index=True)

    s = C.drawdown_defensive_summary(ep)
    if s:
        avg = " · ".join(f"**{k}** {v*100:+.1f}%" for k, v in s["avg_return"].items())
        wins = ", ".join(f"{k} ×{c}" for k, c in s["best_counts"].items())
        st.markdown(f"Across **{s['n']}** TQQQ drawdowns ≥{depth:.0%} — average over the decline: "
                    f"{avg}. Best-hedge count: {wins}. **Most reliable: {s['best_overall']}.**")
    st.download_button("⬇ Drawdown table CSV", ep.to_csv(index=False),
                       "tqqq_drawdown_defensives.csv", "text/csv", key=f"{key}_dl")
