from __future__ import annotations
import math
from typing import Optional
from .base import Broker, BrokerError
from ..models import AccountInfo, OrderResult, Position


class IbkrBroker(Broker):
    """Interactive Brokers via TWS / IB Gateway (ib_async). TWS or Gateway must be running
    with API access enabled (Global Configuration > API > Settings)."""
    name = "ibkr"

    def __init__(self, cfg):
        from ib_async import IB
        self.cfg = cfg
        self.allocation = cfg.allocation
        self.ib = IB()
        self._contracts = {}

    def connect(self) -> None:
        if self.ib.isConnected():
            return
        try:
            self.ib.connect(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id, timeout=15)
        except Exception as e:
            raise BrokerError(f"Cannot connect to IBKR at {self.cfg.host}:{self.cfg.port} - is TWS/Gateway running "
                              f"with API enabled? ({e})")
        self.ib.reqMarketDataType(self.cfg.market_data_type)

    def _ok(self):
        if not self.ib.isConnected():
            self.connect()

    def _contract(self, symbol: str):
        from ib_async import Stock
        if symbol not in self._contracts:
            c = Stock(symbol, "SMART", "USD")
            q = self.ib.qualifyContracts(c)
            if not q:
                raise BrokerError(f"IBKR could not qualify {symbol}")
            self._contracts[symbol] = c
        return self._contracts[symbol]

    def account(self) -> AccountInfo:
        self._ok()
        v = {x.tag: float(x.value) for x in self.ib.accountValues()
             if x.currency in ("USD", "BASE") and x.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower")}
        if "NetLiquidation" not in v:
            raise BrokerError("IBKR account values unavailable")
        return AccountInfo(self.name, v["NetLiquidation"], v.get("TotalCashValue", 0.0), v.get("BuyingPower", 0.0))

    def positions(self) -> list[Position]:
        self._ok()
        return [Position(self.name, p.contract.symbol, float(p.position), float(p.averageCost), float(p.marketPrice))
                for p in self.ib.portfolio() if p.position != 0 and p.contract.secType == "STK"]

    def last_price(self, symbol: str) -> Optional[float]:
        self._ok()
        t = self.ib.reqMktData(self._contract(symbol), "", True, False)
        self.ib.sleep(2)
        px = t.marketPrice()
        self.ib.cancelMktData(self._contract(symbol))
        return None if px is None or (isinstance(px, float) and math.isnan(px)) else float(px)

    def submit_entry(self, symbol, qty, limit_price, stop_price, take_profit=None) -> OrderResult:
        from ib_async import LimitOrder, StopOrder
        self._ok()
        c = self._contract(symbol)
        parent = LimitOrder("BUY", int(qty), round(limit_price, 2), tif="DAY", transmit=False)
        parent.orderId = self.ib.client.getReqId()
        stop = StopOrder("SELL", int(qty), round(stop_price, 2), tif="GTC", parentId=parent.orderId,
                         transmit=take_profit is None)
        children = [stop]
        if take_profit:
            tp = LimitOrder("SELL", int(qty), round(take_profit, 2), tif="GTC", parentId=parent.orderId, transmit=True)
            oca = f"oca{parent.orderId}"
            for o in (stop, tp):
                o.ocaGroup, o.ocaType = oca, 1
            children = [tp, stop]
        trade = self.ib.placeOrder(c, parent)
        for o in children:
            self.ib.placeOrder(c, o)
        self.ib.sleep(1)
        return OrderResult(self.name, str(parent.orderId), symbol, "buy", int(qty), round(limit_price, 2),
                           round(stop_price, 2), trade.orderStatus.status.lower())

    def submit_exit(self, symbol, qty) -> OrderResult:
        from ib_async import MarketOrder
        self._ok()
        for t in self.ib.openTrades():
            if t.contract.symbol == symbol:
                self.ib.cancelOrder(t.order)
        self.ib.sleep(1.5)
        trade = self.ib.placeOrder(self._contract(symbol), MarketOrder("SELL", int(qty), tif="DAY"))
        self.ib.sleep(1)
        return OrderResult(self.name, str(trade.order.orderId), symbol, "sell", int(qty), None, None,
                           trade.orderStatus.status.lower())

    def order_status(self, order_id: str) -> str:
        self._ok()
        for t in self.ib.trades():
            if str(t.order.orderId) == str(order_id):
                return t.orderStatus.status.lower()
        return "unknown"

    def flatten_all(self) -> None:
        from ib_async import MarketOrder
        self._ok()
        self.ib.reqGlobalCancel()
        self.ib.sleep(1.5)
        for p in self.ib.portfolio():
            if p.position > 0 and p.contract.secType == "STK":
                self.ib.placeOrder(p.contract, MarketOrder("SELL", int(p.position)))
        self.ib.sleep(1)

    def history(self, symbol: str, days: int):
        from ib_async import util
        self._ok()
        dur = f"{days} D" if days <= 365 else f"{math.ceil(days / 365)} Y"
        bars = self.ib.reqHistoricalData(self._contract(symbol), endDateTime="", durationStr=dur,
                                         barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1)
        df = util.df(bars)
        if df is None or df.empty:
            return None
        return df.set_index("date")[["open", "high", "low", "close", "volume"]]
