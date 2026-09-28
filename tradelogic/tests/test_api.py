import os, pytest
from fastapi.testclient import TestClient


def test_api_auth_and_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "x" * 24)
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text(f"db_path: {tmp_path/'a.db'}\nuniverse: [AAA]\n")
    from tradelogic.api import create_app
    c = TestClient(create_app(str(cfgp)))
    assert c.get("/api/status").status_code == 401
    h = {"X-Token": "x" * 24}
    assert c.get("/api/status", headers=h).json()["killed"] is False
    assert c.post("/api/kill", headers=h, json={"flatten": False}).status_code == 200
    assert c.post("/api/signals/1/bogus", headers=h).status_code == 400
    assert c.get("/", headers=h).status_code == 200


def test_api_refuses_weak_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "short")
    cfgp = tmp_path / "c.yaml"; cfgp.write_text(f"db_path: {tmp_path/'a.db'}\nuniverse: [AAA]\n")
    from tradelogic.api import create_app
    with pytest.raises(SystemExit):
        create_app(str(cfgp))


def test_api_previews_positions_config(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("DASHBOARD_TOKEN", "x" * 24)
    cfgp = tmp_path / "c.yaml"; cfgp.write_text(f"db_path: {tmp_path/'a.db'}\nuniverse: [AAA]\n")
    from tradelogic.api import create_app
    from tradelogic.db import DB
    db = DB(str(tmp_path / "a.db"))
    db.x("INSERT INTO snapshots VALUES(?,?,?,?)", (time.time(), "alpaca", 100000.0, 100000.0))
    db.x("INSERT INTO signals(created_at,symbol,action,price,stop,score,reason,status) VALUES(?,?,?,?,?,?,?,?)",
         (time.time(), "AAA", "BUY", 50.0, 48.0, 1.0, "t", "pending"))
    db.x("INSERT INTO positions VALUES(?,?,?,?,?,?)", ("alpaca", "AAA", 10, 40, 50, time.time()))
    db.x("INSERT INTO orders(created_at,updated_at,broker,symbol,side,qty,stop_price,status) VALUES(?,?,?,?,?,?,?,?)",
         (time.time(), time.time(), "alpaca", "AAA", "buy", 10, 38.0, "filled"))
    c = TestClient(create_app(str(cfgp))); h = {"X-Token": "x" * 24}
    p = c.get("/api/signals", headers=h).json()[0]["preview"][0]
    assert p["broker"] == "alpaca" and p["qty"] == 200 and abs(p["risk"] - 400) < 1e-6
    assert c.get("/api/positions", headers=h).json()[0]["stop"] == 38.0
    cfg = c.get("/api/config", headers=h).json()
    assert cfg["brokers"]["alpaca"]["enabled"] and "ALPACA" not in str(cfg)
    assert c.get("/api/status", headers=h).json()["brokers"] == {"alpaca": "unknown"}
