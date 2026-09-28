from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    symbol: str
    action: str            # BUY | SELL
    price: float
    stop: Optional[float] = None
    target: Optional[float] = None
    score: float = 0.0
    reason: str = ""
    id: Optional[int] = None


@dataclass
class Position:
    broker: str
    symbol: str
    qty: float
    avg_price: float
    market_price: float


@dataclass
class AccountInfo:
    broker: str
    equity: float
    cash: float
    buying_power: float


@dataclass
class OrderResult:
    broker: str
    order_id: str
    symbol: str
    side: str
    qty: int
    limit_price: Optional[float]
    stop_price: Optional[float]
    status: str
