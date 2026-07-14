"""
Alerts service — turn a live signal evaluation into actionable alerts and
dispatch them through a pluggable Notifier.

Only a LogNotifier ships today; email/desktop/Slack notifiers can be added by
implementing the Notifier interface without touching alert generation.
"""

from typing import Dict, List, Protocol

from core import config
from services import signal_service


class Notifier(Protocol):
    def send(self, alert: Dict) -> None: ...


class LogNotifier:
    """Default notifier — records alerts to a logs file and returns them."""
    def __init__(self, path: str = None):
        import os
        self.path = path or os.path.join(config.LOGS_DIR, "alerts.log")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def send(self, alert: Dict) -> None:
        with open(self.path, "a") as fh:
            fh.write(f"{alert.get('as_of')} [{alert['level']}] {alert['type']}: {alert['message']}\n")


def build_alerts(name: str = "default",
                 drift_threshold: float = config.REBALANCE_DRIFT_THRESHOLD,
                 apply_state: bool = False) -> List[Dict]:
    """
    Evaluate portfolio `name` and return a list of alerts. `apply_state` is
    False by default so simply *checking* for alerts doesn't advance the peak.
    """
    sig = signal_service.evaluate(name, apply_state=apply_state,
                                  drift_threshold=drift_threshold)
    alerts: List[Dict] = []
    as_of = sig["as_of_date"]

    if sig["trigger"] == "stop":
        alerts.append(dict(level="CRITICAL", type="STOP",
                           message=f"TQQQ stop triggered (dd {sig['drawdown_from_peak']:.1%}) "
                                   f"— rotate to defensive 50/50.", as_of=as_of))
    elif sig["trigger"] == "reentry":
        alerts.append(dict(level="CRITICAL", type="REENTRY",
                           message=f"TQQQ re-entry available (close {sig['tqqq_close']:.2f} "
                                   f">= exit {sig['exit_price_reference']}) — restore 70/15/15.",
                           as_of=as_of))

    if sig["rebalance_recommended"] and sig["trigger"] is None:
        alerts.append(dict(level="WARNING", type="DRIFT",
                           message=f"Rebalance suggested: {sig['reason']}.", as_of=as_of))

    if not alerts:
        alerts.append(dict(level="INFO", type="OK",
                           message=f"No action needed ({sig['reason']}).", as_of=as_of))
    return alerts


def check_and_notify(name: str = "default", notifier: Notifier = None,
                     apply_state: bool = False) -> List[Dict]:
    """Build alerts and dispatch any actionable (non-INFO) ones."""
    notifier = notifier or LogNotifier()
    alerts = build_alerts(name, apply_state=apply_state)
    for a in alerts:
        if a["level"] != "INFO":
            notifier.send(a)
    return alerts
