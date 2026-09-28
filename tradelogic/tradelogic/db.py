from __future__ import annotations
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL, symbol TEXT, action TEXT,
  price REAL, stop REAL, target REAL, score REAL, reason TEXT,
  status TEXT DEFAULT 'pending', detail TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL, updated_at REAL, signal_id INTEGER,
  broker TEXT, broker_order_id TEXT, symbol TEXT, side TEXT, qty INTEGER,
  limit_price REAL, stop_price REAL, status TEXT, message TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS commands(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL, kind TEXT, payload TEXT DEFAULT '', done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS snapshots(ts REAL, broker TEXT, equity REAL, cash REAL);
CREATE TABLE IF NOT EXISTS positions(
  broker TEXT, symbol TEXT, qty REAL, avg_price REAL, market_price REAL, updated_at REAL,
  PRIMARY KEY(broker, symbol));
CREATE TABLE IF NOT EXISTS events(ts REAL, level TEXT, message TEXT);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, tuple(args)).fetchall()]

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self.conn() as c:
            return c.execute(sql, tuple(args)).lastrowid

    # key/value state
    def get(self, key: str, default: str | None = None) -> str | None:
        r = self.q("SELECT value FROM kv WHERE key=?", (key,))
        return r[0]["value"] if r else default

    def set(self, key: str, value: str) -> None:
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def log(self, level: str, msg: str) -> None:
        print(f"[{level}] {msg}", flush=True)
        self.x("INSERT INTO events VALUES(?,?,?)", (time.time(), level, msg))

    def command(self, kind: str, payload: str = "") -> int:
        return self.x("INSERT INTO commands(created_at,kind,payload) VALUES(?,?,?)", (time.time(), kind, payload))

    @property
    def killed(self) -> bool:
        return self.get("kill", "0") == "1"
