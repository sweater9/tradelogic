import time, json
import pytest
from tradelogic.brokers.base import Broker, BrokerError
from tradelogic.config import Config
from tradelogic.engine import Engine
from tradelogic.models import AccountInfo, OrderResult, Position


class Fake(Broker):
    def __init__(self, name, equity=100_000, fail=False, alloc=1.0):
        self.name, self.equity, self.fail, self.allocation = name, equity, fail, alloc
        self.orders, self.held = [], []
    def connect(self): pass
    def account(self): return AccountInfo(self.name, self.equity, self.equity, self.equity)
    def positions(self): return self.held
    def last_price(self, s): return 50.0
    def submit_entry(self, symbol, qty, limit_price, stop_price, take_profit=None):
        if self.fail: raise BrokerError("boom")
        self.orders.append(("buy", symbol, qty, limit_price, stop_price))
        return OrderResult(self.name, f"{self.name}-1", symbol, "buy", qty, limit_price, stop_price, "new")
    def submit_exit(self, symbol, qty):
        self.orders.append(("sell", symbol, qty))
        return OrderResult(self.name, f"{self.name}-2", symbol, "sell", qty, None, None, "new")
    def order_status(self, oid): return "filled"
    def flatten_all(self): self.orders.append(("flatten",))


def make_engine(tmp_path, brokers, routing="mirror", auto=True):
    cfg = Config(universe=["AAA"], db_path=str(tmp_path / "e.db"), auto_execute=auto,
                 brokers={"routing": routing, "alpaca": {"enabled": True}, "ibkr": {"enabled": True}})
    e = Engine.__new__(Engine)
    from tradelogic.db import DB
    from tradelogic.risk import RiskManager
    from zoneinfo import ZoneInfo
    e.cfg, e.db, e.risk, e.brokers, e.tz, e.provider = cfg, DB(cfg.db_path), RiskManager(cfg.risk), brokers, ZoneInfo("America/New_York"), None
    return e


def add_signal(e, action="BUY", status="approved"):
    return e.db.x("INSERT INTO signals(created_at,symbol,action,price,stop,score,reason,status) VALUES(?,?,?,?,?,?,?,?)",
                  (time.time(), "AAA", action, 50.0, 47.0, 1.0, "t", status))


def test_mirror_sends_to_both_sized_by_own_equity(tmp_path):
    a, i = Fake("alpaca", 100_000), Fake("ibkr", 50_000)
    e = make_engine(tmp_path, {"alpaca": a, "ibkr": i})
    add_signal(e)
    for s in e.db.q("SELECT * FROM signals"): e.execute_signal(s)
    assert a.orders and i.orders
    assert a.orders[0][2] > i.orders[0][2]                      # bigger account -> more shares
    assert a.orders[0][4] == 47.0                               # protective stop attached
    assert e.db.q("SELECT status FROM signals")[0]["status"] == "executed"
    assert len(e.db.q("SELECT * FROM orders")) == 2


def test_failover_uses_second_broker_on_error(tmp_path):
    a, i = Fake("alpaca", fail=True), Fake("ibkr")
    e = make_engine(tmp_path, {"alpaca": a, "ibkr": i}, routing="failover")
    add_signal(e)
    for s in e.db.q("SELECT * FROM signals"): e.execute_signal(s)
    assert not a.orders and len(i.orders) == 1


def test_failover_stops_after_first_success(tmp_path):
    a, i = Fake("alpaca"), Fake("ibkr")
    e = make_engine(tmp_path, {"alpaca": a, "ibkr": i}, routing="failover")
    add_signal(e)
    for s in e.db.q("SELECT * FROM signals"): e.execute_signal(s)
    assert len(a.orders) == 1 and not i.orders


def test_kill_switch_blocks_entries(tmp_path):
    a = Fake("alpaca")
    e = make_engine(tmp_path, {"alpaca": a})
    e.db.set("kill", "1")
    add_signal(e)
    for s in e.db.q("SELECT * FROM signals"): e.execute_signal(s)
    assert not a.orders
    assert e.db.q("SELECT status FROM signals")[0]["status"] == "failed"


def test_chase_guard(tmp_path):
    a = Fake("alpaca"); a.last_price = lambda s: 60.0           # ran 20% above signal
    e = make_engine(tmp_path, {"alpaca": a})
    add_signal(e)
    for s in e.db.q("SELECT * FROM signals"): e.execute_signal(s)
    assert not a.orders


def test_sell_only_where_held(tmp_path):
    a, i = Fake("alpaca"), Fake("ibkr")
    a.held = [Position("alpaca", "AAA", 30, 40, 50)]
    e = make_engine(tmp_path, {"alpaca": a, "ibkr": i})
    add_signal(e, "SELL")
    for s in e.db.q("SELECT * FROM signals"): e.execute_signal(s)
    assert a.orders == [("sell", "AAA", 30)] and not i.orders


def test_daily_loss_halts(tmp_path):
    a = Fake("alpaca", 100_000)
    e = make_engine(tmp_path, {"alpaca": a})
    e.snapshot()
    a.equity = 97_000
    e.snapshot()
    assert e.db.killed


def test_commands_roundtrip(tmp_path):
    a = Fake("alpaca")
    e = make_engine(tmp_path, {"alpaca": a}, auto=False)
    sid = add_signal(e, status="pending")
    e.db.command("approve", json.dumps({"id": sid}))
    e.db.command("kill", json.dumps({"flatten": True}))
    e.process_commands()
    assert e.db.q("SELECT status FROM signals")[0]["status"] == "approved"
    assert e.db.killed and ("flatten",) in a.orders
    e.db.command("resume"); e.process_commands()
    assert not e.db.killed
