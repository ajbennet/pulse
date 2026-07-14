"""
Pure LDR decision logic — the single source of truth for the strategy rules.

Both the Backtrader backtest (`core.strategy`) and the live signal service
(`services.signal_service`) call `step()` so backtested and live behaviour can
never drift apart. This module has no Backtrader or I/O dependency.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

from core import config


@dataclass
class RegimeConfig:
    """Thresholds/weights needed to evaluate one LDR step."""
    stop_drawdown: float = config.STOP_DRAWDOWN_THRESHOLD
    normal_weights: Dict[str, float] = field(default_factory=lambda: dict(config.NORMAL_WEIGHTS))
    defensive_weights: Dict[str, float] = field(default_factory=lambda: dict(config.DEFENSIVE_WEIGHTS))


@dataclass
class RegimeState:
    """Mutable LDR state carried across daily closes."""
    regime: str = "normal"
    peak: Optional[float] = None          # tracked TQQQ peak (normal regime only)
    exit_price: Optional[float] = None    # stored TQQQ exit price for re-entry


@dataclass
class StepResult:
    """Outcome of evaluating one daily close."""
    state: RegimeState
    trigger: Optional[str]                # 'stop' | 'reentry' | None
    target_weights: Dict[str, float]
    drawdown_from_peak: Optional[float]   # dd at this close (normal regime)


def step(state: RegimeState, tqqq_close: float, cfg: RegimeConfig) -> StepResult:
    """
    Evaluate the LDR regime rules for a single TQQQ close and return the new
    state, any regime trigger, and the resulting target weights.

    Rules (identical to the backtest):
      * Normal regime: update the peak (max close since entry), then exit to
        defensive if the close is >= stop_drawdown below the peak; store the
        exit price.
      * Defensive regime: re-enter (normal) when the close is >= the stored
        exit price; reset the peak to the current close.
    """
    regime = state.regime
    peak = state.peak
    exit_price = state.exit_price
    trigger = None
    dd = None

    if regime == "normal":
        peak = tqqq_close if peak is None else max(peak, tqqq_close)
        dd = (tqqq_close - peak) / peak
        if dd <= -cfg.stop_drawdown:
            regime = "defensive"
            exit_price = tqqq_close
            trigger = "stop"
    else:  # defensive
        if exit_price is not None and tqqq_close >= exit_price:
            regime = "normal"
            peak = tqqq_close        # reset peak on re-entry
            exit_price = None
            trigger = "reentry"

    target = cfg.normal_weights if regime == "normal" else cfg.defensive_weights
    new_state = RegimeState(regime=regime, peak=peak, exit_price=exit_price)
    return StepResult(state=new_state, trigger=trigger,
                      target_weights=dict(target), drawdown_from_peak=dd)


def needs_rebalance(current_weights: Dict[str, float],
                    target_weights: Dict[str, float],
                    drift_threshold: float = config.REBALANCE_DRIFT_THRESHOLD) -> bool:
    """True if any asset's weight drifts beyond the threshold from target."""
    for name, tw in target_weights.items():
        if abs(current_weights.get(name, 0.0) - tw) > drift_threshold:
            return True
    return False
