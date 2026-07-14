"""
LDR (Leveraged Drawdown Reduction) strategy for Backtrader.

Logic summary
-------------
* Normal regime target: 70% TQQQ / 15% UGL / 15% BRK-B.
* While in the normal regime, track the highest TQQQ close since the last
  TQQQ entry/re-entry (the "peak").
* Exit trigger: if TQQQ closes >= STOP_DRAWDOWN_THRESHOLD below that peak,
  switch immediately to the defensive regime and store the exit price.
* Defensive regime target: 0% TQQQ / 50% UGL / 50% BRK-B (the 70% TQQQ sleeve
  is split evenly onto the two always-on defensive sleeves).
* Re-entry trigger: while defensive, re-enter TQQQ when TQQQ closes >= the
  stored exit price. Restore normal weights and reset the peak to the current
  close.
* Rebalancing: threshold-based drift check, evaluated ONLY on quarter-end
  bars. Regime switches happen immediately on any bar, independent of the
  quarter-end schedule.
"""

import backtrader as bt

from core import config, rules


class LDRStrategy(bt.Strategy):
    params = dict(
        normal_weights=config.NORMAL_WEIGHTS,
        defensive_weights=config.DEFENSIVE_WEIGHTS,
        stop_drawdown=config.STOP_DRAWDOWN_THRESHOLD,
        rebalance_drift=config.REBALANCE_DRIFT_THRESHOLD,
        quarter_end_dates=frozenset(),   # set of datetime.date objects
        tqqq_name="TQQQ",
        cash_buffer=config.CASH_BUFFER,
    )

    def __init__(self):
        # Map ticker name -> data feed for weight lookups.
        self.feeds = {d._name: d for d in self.datas}
        self.tqqq = self.feeds[self.p.tqqq_name]

        # Shared decision config (see core.rules) so backtest and live signals
        # evaluate identical rules.
        self._cfg = rules.RegimeConfig(
            stop_drawdown=self.p.stop_drawdown,
            normal_weights=dict(self.p.normal_weights),
            defensive_weights=dict(self.p.defensive_weights),
        )

        # Strategy state.
        self.regime = "normal"
        self.peak = None                 # tracked TQQQ peak (normal regime only)
        self.exit_price = None           # stored TQQQ exit price for re-entry
        self.target_weights = dict(self.p.normal_weights)
        self.started = False

        # Counters / records.
        self.regime_switches = 0
        self.trades_count = 0
        self.rejected_orders = 0
        self.equity_curve = []           # list of (date, portfolio_value)
        self.trade_records = []          # execution log
        self.regime_records = []         # regime-switch log
        self._pending_note = ""          # note attached to next order fill

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _set_targets(self, regime):
        self.target_weights = dict(
            self.p.normal_weights if regime == "normal" else self.p.defensive_weights
        )

    def _rebalance_to_target(self, note=""):
        """
        Issue order_target_percent for every feed toward the current target.

        Sell/reduce orders are submitted before buy orders so the freed cash is
        available to fund the buys in the same bar (avoids cash-shortfall
        rejections during regime switches).
        """
        self._pending_note = note
        plan = [(name, feed, self.target_weights.get(name, 0.0))
                for name, feed in self.feeds.items()]
        reducers = [p for p in plan if self._current_weight(p[1]) >= p[2]]
        increasers = [p for p in plan if self._current_weight(p[1]) < p[2]]
        # Reduce/sell legs first (free cash), then buy legs with a small
        # headroom so whole-share rounding never voids the buy.
        for _, feed, tw in reducers:
            self.order_target_percent(data=feed, target=tw)
        for _, feed, tw in increasers:
            self.order_target_percent(data=feed, target=tw * (1.0 - self.p.cash_buffer))

    def _current_weight(self, feed):
        total = self.broker.getvalue()
        if total <= 0:
            return 0.0
        return self.broker.get_value(datas=[feed]) / total

    def _needs_rebalance(self):
        """True if any asset weight drifts beyond the threshold from target."""
        for name, feed in self.feeds.items():
            drift = abs(self._current_weight(feed) - self.target_weights.get(name, 0.0))
            if drift > self.p.rebalance_drift:
                return True
        return False

    def _log_regime(self, dt, before, after, trigger, tqqq_close, peak, dd, exit_ref):
        self.regime_records.append(dict(
            date=dt,
            regime_before=before,
            regime_after=after,
            trigger=trigger,
            tqqq_close=round(tqqq_close, 6),
            tracked_peak=(round(peak, 6) if peak is not None else ""),
            drawdown_from_peak=(round(dd, 6) if dd is not None else ""),
            exit_price_reference=(round(exit_ref, 6) if exit_ref is not None else ""),
        ))

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------
    def next(self):
        dt = self.datas[0].datetime.date(0)
        tqqq_close = float(self.tqqq.close[0])

        # Record equity every bar for the daily equity curve.
        self.equity_curve.append((dt, self.broker.getvalue()))

        # First bar: establish the initial normal allocation.
        if not self.started:
            self.started = True
            self.peak = tqqq_close
            self.regime = "normal"
            self._set_targets("normal")
            self._log_regime(dt, "init", "normal", "init", tqqq_close, self.peak, 0.0, None)
            self._rebalance_to_target(note="initial allocation")
            return

        # -- Regime logic evaluated on daily closes (shared core.rules.step) --
        prev_peak, prev_exit = self.peak, self.exit_price
        result = rules.step(
            rules.RegimeState(self.regime, self.peak, self.exit_price),
            tqqq_close, self._cfg,
        )
        self.regime = result.state.regime
        self.peak = result.state.peak
        self.exit_price = result.state.exit_price
        self.target_weights = result.target_weights

        if result.trigger == "stop":
            self._log_regime(dt, "normal", "defensive", "stop",
                             tqqq_close, self.peak, result.drawdown_from_peak, self.exit_price)
            self.regime_switches += 1
            self._rebalance_to_target(note="stop -> defensive")
            return
        if result.trigger == "reentry":
            self._log_regime(dt, "defensive", "normal", "reentry",
                             tqqq_close, prev_peak, None, prev_exit)
            self.regime_switches += 1
            self._rebalance_to_target(note="reentry -> normal")
            return

        # -- Threshold rebalance evaluated ONLY on quarter-end bars --
        if dt in self.p.quarter_end_dates and self._needs_rebalance():
            self._rebalance_to_target(note="quarter-end rebalance")

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def notify_order(self, order):
        if order.status in (order.Completed,):
            action = "BUY" if order.isbuy() else "SELL"
            self.trade_records.append(dict(
                date=self.datas[0].datetime.date(0),
                ticker=order.data._name,
                action=action,
                size=round(order.executed.size, 6),
                executed_price=round(order.executed.price, 6),
                value=round(order.executed.value, 6),
                regime=self.regime,
                note=self._pending_note,
            ))
            self.trades_count += 1
        elif order.status in (order.Margin, order.Rejected, order.Canceled):
            # Surface (do not silently drop) any order that failed to fill.
            self.rejected_orders += 1

    def notify_trade(self, trade):
        # Trade round-trips are captured via notify_order fills; nothing extra
        # needed here, but the hook is kept per the spec for extensibility.
        pass


class BenchmarkStrategy(bt.Strategy):
    """
    Buy-and-hold benchmark: fixed 70/15/15 target with a quarter-end
    threshold rebalance. No regime switching / stop logic.
    """
    params = dict(
        weights=config.NORMAL_WEIGHTS,
        rebalance_drift=config.REBALANCE_DRIFT_THRESHOLD,
        quarter_end_dates=frozenset(),
        cash_buffer=config.CASH_BUFFER,
    )

    def __init__(self):
        self.feeds = {d._name: d for d in self.datas}
        self.started = False
        self.equity_curve = []

    def _current_weight(self, feed):
        total = self.broker.getvalue()
        return self.broker.get_value(datas=[feed]) / total if total > 0 else 0.0

    def _needs_rebalance(self):
        for name, feed in self.feeds.items():
            if abs(self._current_weight(feed) - self.p.weights.get(name, 0.0)) > self.p.rebalance_drift:
                return True
        return False

    def _rebalance(self):
        plan = [(feed, self.p.weights.get(name, 0.0)) for name, feed in self.feeds.items()]
        reducers = [p for p in plan if self._current_weight(p[0]) >= p[1]]
        increasers = [p for p in plan if self._current_weight(p[0]) < p[1]]
        for feed, tw in reducers:
            self.order_target_percent(data=feed, target=tw)
        for feed, tw in increasers:
            self.order_target_percent(data=feed, target=tw * (1.0 - self.p.cash_buffer))

    def next(self):
        dt = self.datas[0].datetime.date(0)
        self.equity_curve.append((dt, self.broker.getvalue()))
        if not self.started:
            self.started = True
            self._rebalance()
            return
        if dt in self.p.quarter_end_dates and self._needs_rebalance():
            self._rebalance()
