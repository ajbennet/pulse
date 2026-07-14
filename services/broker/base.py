"""
Broker adapter interface — the seam for future brokerage integration.

Implement this ABC (e.g. AlpacaBroker, IBKRBroker) to let PULSE read live
positions and place orders. Nothing in the app calls a real broker yet; the
PaperBroker below fulfils the same interface against the local paper portfolio
so the UI and signal flow can be built and tested end-to-end now.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class Order:
    ticker: str
    action: str          # "BUY" | "SELL"
    shares: float
    price: float = 0.0   # 0 => market
    note: str = ""


class BrokerAdapter(ABC):
    """Minimal broker surface: read state, place orders."""

    @abstractmethod
    def get_cash(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> Dict[str, float]:
        """Return {ticker: shares}."""

    @abstractmethod
    def place_order(self, order: Order) -> Dict:
        """Execute/record an order and return a result dict."""

    def place_orders(self, orders: List[Order]) -> List[Dict]:
        return [self.place_order(o) for o in orders]
