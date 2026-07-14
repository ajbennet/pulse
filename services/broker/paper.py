"""
PaperBroker — a BrokerAdapter backed by the local paper portfolio.

Lets recommended trades be "executed" against the paper portfolio (updating
holdings, cash, and P&L) using the same interface a real broker will implement.
"""

from datetime import date
from typing import Dict

from services import portfolio_service
from services.broker.base import BrokerAdapter, Order


class PaperBroker(BrokerAdapter):
    def __init__(self, portfolio_name: str = "default"):
        self.name = portfolio_name

    def get_cash(self) -> float:
        return portfolio_service.load(self.name)["cash"]

    def get_positions(self) -> Dict[str, float]:
        p = portfolio_service.load(self.name)
        return {t: h["shares"] for t, h in p["holdings"].items()}

    def place_order(self, order: Order) -> Dict:
        if order.price <= 0:
            raise ValueError("PaperBroker requires an explicit price (no live quotes).")
        portfolio_service.add_transaction(
            self.name, order.ticker, order.action, order.shares, order.price,
            date=date.today().isoformat(), note=order.note or "paper order",
        )
        return {"status": "filled", "ticker": order.ticker, "action": order.action,
                "shares": order.shares, "price": order.price}
