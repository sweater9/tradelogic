from __future__ import annotations
import datetime as dt
from abc import ABC, abstractmethod
from typing import Optional
from zoneinfo import ZoneInfo
from ..models import AccountInfo, OrderResult, Position


class BrokerError(RuntimeError):
    pass


class Broker(ABC):
    name = "base"
    allocation = 1.0

    @abstractmethod
    def connect(self) -> None: ...
    @abstractmethod
    def account(self) -> AccountInfo: ...
    @abstractmethod
    def positions(self) -> list[Position]: ...
    @abstractmethod
    def last_price(self, symbol: str) -> Optional[float]: ...
    @abstractmethod
    def submit_entry(self, symbol: str, qty: int, limit_price: float, stop_price: float,
                     take_profit: Optional[float] = None) -> OrderResult:
        """Buy with a broker-side protective stop attached."""
    @abstractmethod
    def submit_exit(self, symbol: str, qty: int) -> OrderResult:
        """Cancel this symbol's open orders, then sell at market."""
    @abstractmethod
    def order_status(self, order_id: str) -> str: ...
    @abstractmethod
    def flatten_all(self) -> None:
        """Cancel every open order and close every position."""

    def is_market_open(self) -> bool:
        now = dt.datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return False
        return dt.time(9, 30) <= now.time() < dt.time(16, 0)
