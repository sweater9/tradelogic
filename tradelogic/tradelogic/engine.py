"""The trading engine: scans on schedule, creates signals, executes approved ones through
one or both brokers, enforces risk, and keeps the DB (and thus the dashboard) up to date."""
from __future__ import annotations
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo
from .brokers import make_brokers, BrokerError
from .config import Config, assert_live_confirmed
from .data import make_provider
from .db import DB
from .models import Signal
from .risk import RiskManager
from .strategy import scan


class Engine:
    def __init__(self, cfg: Config):
        assert_live_confirmed(cfg)
        self.cfg = cfg
        self.db = DB(cfg.db_path)
        self.risk = RiskManager(cfg.risk)
        self.brokers = make_brokers(cfg)
        self.tz = ZoneInfo(cfg.schedule.timezone)
        self.provider = None

    # ---------- lifecycle ----------
    def start(self):
        for b in self.brokers.values():
            b.connect()
            self.db.log("info", f"connected to {b.name} ({self.cfg.mode})")
        self.provider = make_provider(self.cfg, self.brokers)
        self.db.set("mode", self.cfg.mode)
        self.db.set("auto_execute", "1" if self.cfg.auto_execute else "0")
        self.db.set("routing", self.cfg.brokers.routing)
        self.db.log("info", f"engine started: mode={self.cfg.mode} auto_execute={self.cfg.auto_execute} "
                            f"routing={self.cfg.brokers.routing} brokers={list(self.brokers)}")
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception as e:  # never die silently; log and keep protecting the account
                self.db.log("error", f"tick failed: {type(e).__name__}: {e}")
            time.sleep(self.cfg.schedule.poll_seconds)

    def tick(self):
        self.process_commands()
        self.snapshot()
        self.sync_orders()
        self.expire_signals()
        if self.scan_due():
            self.run_scan()
        for s in self.db.q("SELECT * FROM signals WHERE status='approved' ORDER BY id"):
            self.execute_signal(s)
        self.db.set("heartbeat", str(time.time()))

    # ---------- commands from dashboard ----------
    def process_commands(self):
        for c in self.db.q("SELECT * FROM commands WHERE done=0 ORDER BY id"):
            k, p = c["kind"], json.loads(c["payload"] or "{}")
            try:
                if k == "approve":
                    self.db.x("UPDATE signals SET status='approved' WHERE id=? AND status='pending'", (p["id"],))
                elif k == "reject":
                    self.db.x("UPDATE signals SET status='rejected' WHERE id=? AND status='pending'", (p["id"],))
                elif k == "kill":
                    self.db.set("kill", "1")
                    self.db.log("warn", "KILL SWITCH ON: no new entries. Broker-side stops stay active.")
                    if p.get("flatten"):
                        self.flatten()
                elif k == "resume":
                    self.db.set("kill", "0")
                    self.db.log("info", "kill switch cleared")
                elif k == "scan_now":
                    self.run_scan()
            except Exception as e:
                self.db.log("error", f"command {k} failed: {e}")
            self.db.x("UPDATE commands SET done=1 WHERE id=?", (c["id"],))

    def flatten(self):
        for b in self.brokers.values():
            try:
                b.flatten_all()
                self.db.log("warn", f"{b.name}: flattened (all orders cancelled, positions closed)")
            except Exception as e:
                self.db.log("error", f"{b.name}: flatten failed: {e}")

    # ---------- monitoring ----------
    def snapshot(self):
        now = time.time()
        today = dt.datetime.now(self.tz).date().isoformat()
        for b in self.brokers.values():
            try:
                acct, poss = b.account(), b.positions()
            except Exception as e:
                self.db.set(f"broker_ok:{b.name}", f"error: {type(e).__name__}: {e}"[:200])
                self.db.log("error", f"{b.name}: snapshot failed: {e}")
                continue
            self.db.set(f"broker_ok:{b.name}", "ok")
            self.db.x("INSERT INTO snapshots VALUES(?,?,?,?)", (now, b.name, acct.equity, acct.cash))
            with self.db.conn() as c:
                c.execute("DELETE FROM positions WHERE broker=?", (b.name,))
                c.executemany("INSERT INTO positions VALUES(?,?,?,?,?,?)",
                              [(b.name, p.symbol, p.qty, p.avg_price, p.market_price, now) for p in poss])
            key = f"day_start:{b.name}:{today}"
            start = self.db.get(key)
            if start is None:
                self.db.set(key, str(acct.equity))
            elif self.risk.daily_loss_hit(float(start), acct.equity) and not self.db.killed:
                self.db.set("kill", "1")
                self.db.log("warn", f"{b.name}: daily loss limit {self.cfg.risk.max_daily_loss_pct}% hit "
                                    f"(equity {acct.equity:,.0f} vs start {float(start):,.0f}). New entries halted.")

    def sync_orders(self):
        for o in self.db.q("SELECT * FROM orders WHERE status NOT IN ('filled','canceled','cancelled','rejected','expired','failed')"):
            b = self.brokers.get(o["broker"])
            if not b or not o["broker_order_id"]:
                continue
            try:
                st = b.order_status(o["broker_order_id"])
            except Exception:
                continue
            if st != o["status"]:
                self.db.x("UPDATE orders SET status=?, updated_at=? WHERE id=?", (st, time.time(), o["id"]))

    def expire_signals(self):
        cutoff = time.time() - self.cfg.schedule.signal_ttl_hours * 3600
        self.db.x("UPDATE signals SET status='expired' WHERE status='pending' AND created_at<?", (cutoff,))

    # ---------- scanning ----------
    def scan_due(self) -> bool:
        now = dt.datetime.now(self.tz)
        if now.weekday() >= 5:
            return False
        hh, mm = map(int, self.cfg.schedule.scan_time.split(":"))
        if now.time() < dt.time(hh, mm):
            return False
        return self.db.get("last_scan_date") != now.date().isoformat()

    def held_symbols(self) -> set[str]:
        return {r["symbol"] for r in self.db.q("SELECT DISTINCT symbol FROM positions WHERE qty>0")}

    def run_scan(self) -> list[Signal]:
        self.db.set("last_scan_date", dt.datetime.now(self.tz).date().isoformat())
        syms = sorted(set(self.cfg.universe) | self.held_symbols() | {self.cfg.benchmark})
        frames = self.provider.history(syms, self.cfg.data.history_days)
        bench = frames.pop(self.cfg.benchmark, None)
        if bench is None or len(bench) < self.cfg.strategy.regime_sma:
            self.db.log("error", "scan aborted: benchmark data missing/too short")
            return []
        missing = [s for s in syms if s not in frames and s != self.cfg.benchmark]
        if missing:
            self.db.log("warn", f"no data for: {', '.join(missing)}")
        held = self.held_symbols()
        sigs = scan(frames, bench, self.cfg.strategy, held, self.cfg.risk.min_price)
        buys_pending = self.db.q("SELECT COUNT(*) n FROM signals WHERE action='BUY' AND status IN ('pending','approved')")[0]["n"]
        slots = max(0, self.cfg.risk.max_open_positions - len(held) - buys_pending)
        made = 0
        for s in sigs:
            if s.action == "BUY":
                if slots <= 0:
                    continue
                slots -= 1
            dup = self.db.q("SELECT 1 FROM signals WHERE symbol=? AND action=? AND status IN ('pending','approved')",
                            (s.symbol, s.action))
            if dup:
                continue
            status = "approved" if self.cfg.auto_execute else "pending"
            self.db.x("INSERT INTO signals(created_at,symbol,action,price,stop,target,score,reason,status) "
                      "VALUES(?,?,?,?,?,?,?,?,?)",
                      (time.time(), s.symbol, s.action, s.price, s.stop, s.target, s.score, s.reason, status))
            made += 1
        self.db.log("info", f"scan complete: {len(frames)} symbols, {made} new signals")
        return sigs

    # ---------- execution ----------
    def _targets(self):
        """Brokers to trade on, according to routing mode."""
        return list(self.brokers.values())

    def execute_signal(self, row: dict):
        sig = Signal(row["symbol"], row["action"], row["price"], row["stop"], row["target"],
                     row["score"], row["reason"], row["id"])
        notes, ok = [], False
        for b in self._targets():
            try:
                res = self._exec_on(b, sig)
                notes.append(f"{b.name}: {res}")
                if res.startswith("sent"):
                    ok = True
                    if self.cfg.brokers.routing == "failover":
                        break
            except Exception as e:
                notes.append(f"{b.name}: ERROR {type(e).__name__}: {e}")
                self.db.log("error", f"{b.name} {sig.action} {sig.symbol} failed: {e}")
        self.db.x("UPDATE signals SET status=?, detail=? WHERE id=?",
                  ("executed" if ok else "failed", " | ".join(notes), sig.id))

    def _exec_on(self, b, sig: Signal) -> str:
        acct, poss = b.account(), b.positions()
        held = {p.symbol: p for p in poss if p.qty > 0}
        if sig.action == "SELL":
            p = held.get(sig.symbol)
            if not p:
                return "skipped (not held here)"
            r = b.submit_exit(sig.symbol, int(p.qty))
            self._record(sig, r)
            return f"sent SELL {int(p.qty)}"
        # BUY
        last = b.last_price(sig.symbol) or sig.price
        if last > sig.price * (1 + self.cfg.risk.max_chase_pct / 100):
            return f"skipped (price {last:.2f} ran >{self.cfg.risk.max_chase_pct}% above signal {sig.price:.2f})"
        if last <= sig.stop:
            return f"skipped (price {last:.2f} already below stop {sig.stop:.2f})"
        gross = sum(p.qty * p.market_price for p in held.values())
        dec = self.risk.evaluate_entry(
            killed=self.db.killed, symbol=sig.symbol, price=last, stop=sig.stop, equity=acct.equity,
            cash=acct.cash, allocation=b.allocation, open_symbols=set(held), gross_exposure=gross)
        if not dec.ok:
            return f"vetoed ({dec.reason})"
        limit = last * (1 + self.cfg.risk.entry_limit_slippage_pct / 100)
        r = b.submit_entry(sig.symbol, dec.qty, limit, sig.stop, sig.target)
        self._record(sig, r)
        return f"sent BUY {dec.qty} @ <= {limit:.2f}, stop {sig.stop:.2f}"

    def _record(self, sig: Signal, r):
        now = time.time()
        self.db.x("INSERT INTO orders(created_at,updated_at,signal_id,broker,broker_order_id,symbol,side,qty,"
                  "limit_price,stop_price,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (now, now, sig.id, r.broker, r.order_id, r.symbol, r.side, r.qty, r.limit_price, r.stop_price, r.status))
        self.db.log("info", f"{r.broker}: {r.side.upper()} {r.qty} {r.symbol} submitted ({r.status})")
