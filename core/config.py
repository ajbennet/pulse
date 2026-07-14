"""
PULSE — Protected Ultra Leverage Strategy Engine
LDR (Leveraged Drawdown Reduction) strategy configuration.

All tunable parameters live here so the strategy can be adjusted without
touching the engine code.
"""

# --------------------------------------------------------------------------
# Universe
# --------------------------------------------------------------------------
# NOTE: yfinance expects "BRK-B" (hyphen), not "BRK.B".
TICKERS = ["TQQQ", "UGL", "BRK-B"]

# --------------------------------------------------------------------------
# Backtest window
# --------------------------------------------------------------------------
START_DATE = "2010-01-01"     # requested start; effective start is bounded by
                              # the latest inception among all assets (TQQQ ~2010-02)
END_DATE = None               # None -> latest available date from Yahoo Finance

# --------------------------------------------------------------------------
# Capital
# --------------------------------------------------------------------------
INITIAL_CAPITAL = 100_000.0
COMMISSION = 0.0              # per-trade commission (fraction of trade value)

# --------------------------------------------------------------------------
# Strategy thresholds
# --------------------------------------------------------------------------
STOP_DRAWDOWN_THRESHOLD = 0.30    # exit TQQQ sleeve if >= 30% below tracked peak
REBALANCE_DRIFT_THRESHOLD = 0.09  # rebalance if any weight drifts > 9 pts from target

# Small headroom applied to BUY legs during a rebalance so an order is never
# nullified by a penny-level cash shortfall (whole-share rounding on the sell
# legs frees slightly less cash than a full-weight buy would consume, and
# Backtrader voids the ENTIRE order if cash would go negative). This leaves a
# tiny, transient idle-cash residue that stays well inside the drift threshold.
CASH_BUFFER = 0.003

# --------------------------------------------------------------------------
# Target weights per regime (must sum to 1.0 within each regime)
# --------------------------------------------------------------------------
NORMAL_WEIGHTS = {
    "TQQQ": 0.70,
    "UGL": 0.15,
    "BRK-B": 0.15,
}

DEFENSIVE_WEIGHTS = {
    "TQQQ": 0.00,
    "UGL": 0.50,
    "BRK-B": 0.50,
}

# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------
RISK_FREE_RATE = 0.0          # annualized risk-free rate for Sharpe
TRADING_DAYS_PER_YEAR = 252

# --------------------------------------------------------------------------
# Benchmark (nice-to-have): 70/15/15 quarterly rebalance, no regime switching
# --------------------------------------------------------------------------
RUN_BENCHMARK = True

# --------------------------------------------------------------------------
# Data handling
# --------------------------------------------------------------------------
USE_CACHE = True              # cache downloaded data under data/ to speed reruns
CACHE_DIR = "data"

# --------------------------------------------------------------------------
# Output directories
# --------------------------------------------------------------------------
RESULTS_DIR = "results"
LOGS_DIR = "logs"
