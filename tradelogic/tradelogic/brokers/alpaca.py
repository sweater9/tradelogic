from __future__ import annotations
import os
from typing import Optional
from .base import Broker, BrokerError
from ..models import AccountInfo, OrderResult, Position


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, cfg, paper: bool):
        self.cfg, self.paper = cfg, paper
        self.allocation = cfg.allocation
        self.client = self.data = self.feed = None

    def connect(self) -> None:
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.enums import DataFeed
        key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
        if not key or not sec:
            raise BrokerError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env")
        self.client = TradingClient(key, sec, paper=self.paper)
        self.data = StockHistoricalDataClient(key, sec)
        self.feed = DataFeed.IEX if self.cfg.feed == "iex" else DataFeed.SIP
        self.client.get_account()  # fail fast on bad keys

    def account(self) -> AccountInfo:
        a = self.client.get_account()
        if getattr(a, "trading_blocked", False):
            raise BrokerError("Alpaca account is trading-blocked")
        return AccountInfo(self.name, float(a.equity), float(a.cash), float(a.buying_power))

    def positions(self) -> list[Position]:
        return [Position(self.name, p.symbol, float(p.qty), float(p.avg_entry_price), float(p.current_price))
                for p in self.client.get_all_positions()]

    def last_price(self, symbol: str) -> Optional[float]:
        from alpaca.data.requests import StockLatestTradeRequest
        t = self.data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self.feed))
        return float(t[symbol].price)

    def submit_entry(self, symbol, qty, limit_price, stop_price, take_profit=None) -> OrderResult:
        from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
        kw = dict(symbol=symbol, qty=int(qty), side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
                  limit_price=round(limit_price, 2), stop_loss=StopLossRequest(stop_price=round(stop_price, 2)))
        if take_profit:
            kw.update(order_class=OrderClass.BRACKET, take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)))
        else:
            kw.update(order_class=OrderClass.OTO)
        o = self.client.submit_order(LimitOrderRequest(**kw))
        return OrderResult(self.name, str(o.id), symbol, "buy", int(qty), round(limit_price, 2),
                           round(stop_price, 2), self._st(o.status))

    def submit_exit(self, symbol, qty) -> OrderResult:
        from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
        for o in self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])):
            self.client.cancel_order_by_id(o.id)
        o = self.client.submit_order(MarketOrderRequest(symbol=symbol, qty=int(qty), side=OrderSide.SELL,
                                                        time_in_force=TimeInForce.DAY))
        return OrderResult(self.name, str(o.id), symbol, "sell", int(qty), None, None, self._st(o.status))

    def order_status(self, order_id: str) -> str:
        return self._st(self.client.get_order_by_id(order_id).status)

    def flatten_all(self) -> None:
        self.client.close_all_positions(cancel_orders=True)

    @staticmethod
    def _st(s) -> str:
        return str(getattr(s, "value", s)).lower()

    # helper used by AlpacaProvider
    def is_market_open(self) -> bool:
        try:
            return bool(self.client.get_clock().is_open)
        except Exception:
            return super().is_market_open()
