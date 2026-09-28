"""Dashboard + control API. Reads state from the DB that the engine writes; sends
control actions back as commands. Bound to localhost by default, token required.
API keys never reach the browser."""
from __future__ import annotations
import datetime as dt
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .config import load_config
from .db import DB
from .risk import size_position


class Kill(BaseModel):
    flatten: bool = False


def create_app(config_path: str = "config.yaml") -> FastAPI:
    cfg = load_config(config_path)
    token = os.environ.get("DASHBOARD_TOKEN", "")
    if len(token) < 16:
        raise SystemExit("Set DASHBOARD_TOKEN (16+ random characters) in .env before starting the dashboard.")
    db = DB(cfg.db_path)
    app = FastAPI(title="TradeLogic", docs_url=None, redoc_url=None)
    enabled = [n for n in ("alpaca", "ibkr") if getattr(cfg.brokers, n).enabled]

    def auth(x_token: str = Header(default=""), t: str = Query(default="")):
        if (x_token or t) != token:
            raise HTTPException(401, "bad token")

    def accounts():
        today = dt.datetime.now(ZoneInfo(cfg.schedule.timezone)).date().isoformat()
        rows = db.q("SELECT broker, equity, cash, MAX(ts) ts FROM snapshots GROUP BY broker")
        for r in rows:
            start = db.get(f"day_start:{r['broker']}:{today}")
            r["day_start"] = float(start) if start else None
            r["day_change"] = (r["equity"] - r["day_start"]) if start else None
        return rows

    def previews(sig: dict, accts: list[dict]) -> list[dict]:
        """What approving this BUY would do on each broker, using the same sizing as the engine."""
        if sig["action"] != "BUY" or not sig.get("stop"):
            return []
        out = []
        targets = enabled[:1] if cfg.brokers.routing == "failover" else enabled
        for a in accts:
            if a["broker"] not in targets:
                continue
            alloc = getattr(cfg.brokers, a["broker"]).allocation
            eq = a["equity"] * alloc
            qty = size_position(eq, min(a["cash"], eq), sig["price"], sig["stop"],
                                cfg.risk.risk_per_trade_pct, cfg.risk.max_position_pct)
            risk = qty * (sig["price"] - sig["stop"])
            out.append({"broker": a["broker"], "qty": qty, "cost": qty * sig["price"], "risk": risk,
                        "risk_pct": risk / a["equity"] * 100 if a["equity"] else 0})
        return out

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).parent / "dashboard" / "index.html")

    @app.get("/api/status", dependencies=[Depends(auth)])
    def status():
        hb = db.get("heartbeat")
        return {
            "mode": db.get("mode", cfg.mode), "auto_execute": db.get("auto_execute", "0") == "1",
            "routing": db.get("routing", cfg.brokers.routing), "killed": db.killed,
            "engine_alive": bool(hb) and time.time() - float(hb) < cfg.schedule.poll_seconds * 6 + 30,
            "last_scan": db.get("last_scan_date"), "accounts": accounts(),
            "brokers": {b: db.get(f"broker_ok:{b}", "unknown") for b in enabled},
        }

    @app.get("/api/config", dependencies=[Depends(auth)])
    def config():
        return {
            "mode": cfg.mode, "auto_execute": cfg.auto_execute, "routing": cfg.brokers.routing,
            "brokers": {n: {"enabled": getattr(cfg.brokers, n).enabled,
                            "allocation": getattr(cfg.brokers, n).allocation} for n in ("alpaca", "ibkr")},
            "data": cfg.data.provider, "benchmark": cfg.benchmark, "universe_size": len(cfg.universe),
            "risk": cfg.risk.model_dump(), "strategy": cfg.strategy.model_dump(),
            "schedule": {"scan_time": cfg.schedule.scan_time, "timezone": cfg.schedule.timezone},
        }

    @app.get("/api/signals", dependencies=[Depends(auth)])
    def signals():
        rows = db.q("SELECT * FROM signals ORDER BY id DESC LIMIT 60")
        accts = accounts()
        for r in rows:
            r["preview"] = previews(r, accts) if r["status"] == "pending" else []
        return rows

    @app.get("/api/positions", dependencies=[Depends(auth)])
    def positions():
        rows = db.q("SELECT * FROM positions ORDER BY broker, symbol")
        for r in rows:
            o = db.q("SELECT stop_price FROM orders WHERE symbol=? AND broker=? AND side='buy' "
                     "AND stop_price IS NOT NULL ORDER BY id DESC LIMIT 1", (r["symbol"], r["broker"]))
            r["stop"] = o[0]["stop_price"] if o else None
        return rows

    @app.get("/api/orders", dependencies=[Depends(auth)])
    def orders():
        return db.q("SELECT * FROM orders ORDER BY id DESC LIMIT 60")

    @app.get("/api/events", dependencies=[Depends(auth)])
    def events():
        return db.q("SELECT * FROM events ORDER BY ts DESC LIMIT 80")

    @app.get("/api/equity", dependencies=[Depends(auth)])
    def equity():
        return db.q("SELECT ts, broker, equity FROM snapshots WHERE ts > ? ORDER BY ts",
                    (time.time() - 30 * 86400,))[-6000:]

    @app.post("/api/signals/{sid}/{action}", dependencies=[Depends(auth)])
    def decide(sid: int, action: str):
        if action not in ("approve", "reject"):
            raise HTTPException(400, "action must be approve or reject")
        db.command(action, json.dumps({"id": sid}))
        return {"queued": action, "id": sid}

    @app.post("/api/kill", dependencies=[Depends(auth)])
    def kill(body: Kill):
        db.command("kill", json.dumps({"flatten": body.flatten}))
        return {"queued": "kill", "flatten": body.flatten}

    @app.post("/api/resume", dependencies=[Depends(auth)])
    def resume():
        db.command("resume")
        return {"queued": "resume"}

    @app.post("/api/scan", dependencies=[Depends(auth)])
    def scan_now():
        db.command("scan_now")
        return {"queued": "scan_now"}

    return app
