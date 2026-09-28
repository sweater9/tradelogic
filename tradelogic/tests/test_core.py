import numpy as np, pandas as pd, pytest
from tradelogic.config import Config
from tradelogic.risk import RiskManager, size_position
from tradelogic.indicators import rsi, atr, sma
from tradelogic.strategy import prepare, regime_series, scan
from tradelogic.backtest import run_backtest
from tradelogic.db import DB


def test_size_position_risk_cap():
    # equity 100k, risk 0.5% = $500, $2 risk/share -> 250 shares; cap 10% = $10k / $50 = 200
    assert size_position(100_000, 100_000, 50, 48, 0.5, 10) == 200
    # tight cap not binding: $10 stop distance -> 50 shares
    assert size_position(100_000, 100_000, 50, 40, 0.5, 10) == 50
    assert size_position(100_000, 1_000, 50, 48, 0.5, 10) == 20      # cash-limited
    assert size_position(100_000, 100_000, 50, 55, 0.5, 10) == 0     # stop above price


def test_risk_vetoes(cfg):
    rm = RiskManager(cfg.risk)
    base = dict(symbol="X", price=50, stop=48, equity=100_000, cash=100_000, allocation=1.0,
                open_symbols=set(), gross_exposure=0)
    assert rm.evaluate_entry(killed=False, **base).ok
    assert not rm.evaluate_entry(killed=True, **base).ok
    assert not rm.evaluate_entry(killed=False, **{**base, "open_symbols": {"X"}}).ok
    assert not rm.evaluate_entry(killed=False, **{**base, "open_symbols": set("ABCDEFGH")}).ok
    assert not rm.evaluate_entry(killed=False, **{**base, "gross_exposure": 80_000}).ok
    assert not rm.evaluate_entry(killed=False, **{**base, "price": 3, "stop": 2.5}).ok
    half = rm.evaluate_entry(killed=False, **{**base, "allocation": 0.5})
    full = rm.evaluate_entry(killed=False, **base)
    assert 0 < half.qty < full.qty


def test_daily_loss(cfg):
    rm = RiskManager(cfg.risk)
    assert rm.daily_loss_hit(100_000, 97_900)
    assert not rm.daily_loss_hit(100_000, 98_500)
    assert not rm.daily_loss_hit(None, 1)


def test_indicators():
    s = pd.Series(np.arange(1, 60, dtype=float))
    assert rsi(s).iloc[-1] == 100.0
    assert sma(s, 5).iloc[-1] == 57.0
    df = pd.DataFrame(dict(high=s + 1, low=s - 1, close=s))
    assert atr(df).iloc[-1] == pytest.approx(2.0, rel=0.05)


def test_no_lookahead(cfg, market):
    """Signals at index i must not change if future bars are removed."""
    frames, bench = market
    df = frames["AAA"]
    reg = regime_series(bench, cfg.strategy)
    full = prepare(df, reg, cfg.strategy)
    cut = prepare(df.iloc[:600], reg.iloc[:600], cfg.strategy)
    for col in ("entry", "exit", "stop", "rsi"):
        a, b = full[col].iloc[:600], cut[col]
        assert (a.fillna(-1).values == b.fillna(-1).values).all(), col


def test_scan_returns_signals(cfg, market):
    frames, bench = market
    out = scan(frames, bench, cfg.strategy, held=set())
    assert all(s.action == "BUY" and s.stop < s.price for s in out)


def test_backtest_runs_and_is_consistent(cfg, market):
    frames, bench = market
    res = run_backtest(frames, bench, cfg)
    assert len(res.equity) > 300 and (res.equity > 0).all()
    t = res.trades
    if len(t):
        assert (t.exit_date >= t.entry_date).all()
        # final equity == initial capital + total pnl (all positions liquidated at end)
        assert res.equity.iloc[-1] == pytest.approx(cfg.backtest.initial_capital + t.pnl.sum(), rel=1e-6)


def test_backtest_stop_loss_and_costs(cfg):
    """A stock that gaps down after a breakout must be stopped out, and costs must reduce pnl."""
    df = pd.DataFrame(dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=5e6),
                      index=pd.bdate_range("2021-01-04", periods=400))
    df.iloc[:300, :4] = np.linspace(60, 100, 300)[:, None] * np.array([1, 1.01, .99, 1])
    df.iloc[300:, :4] = np.array([100, 101, 99, 100])
    df.iloc[350:, :4] = np.array([70, 71, 69, 70])            # crash: stop must trigger on gap
    bench = df.copy()
    r0 = run_backtest({"ZZZ": df}, bench, cfg)
    cfg2 = cfg.model_copy(deep=True); cfg2.backtest.commission_per_share = 0.05; cfg2.backtest.slippage_bps = 25
    r1 = run_backtest({"ZZZ": df}, bench, cfg2)
    assert r1.equity.iloc[-1] <= r0.equity.iloc[-1]
    if len(r0.trades):
        assert (r0.trades.exit_px <= 100.5).all()


def test_db_kill_switch(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    assert not db.killed
    db.set("kill", "1"); assert db.killed
    cid = db.command("resume")
    assert db.q("SELECT kind FROM commands WHERE id=?", (cid,))[0]["kind"] == "resume"


def test_config_guards():
    with pytest.raises(Exception):
        Config(universe=["A"], mode="paper", brokers={"alpaca": {"enabled": False}, "ibkr": {"enabled": True, "port": 7496}})
    with pytest.raises(Exception):
        Config(universe=["A"], mode="live", brokers={"alpaca": {"enabled": False}, "ibkr": {"enabled": True, "port": 7497}})
    with pytest.raises(Exception):
        Config(universe=["A"], brokers={"alpaca": {"enabled": False}, "ibkr": {"enabled": False}})
    c = Config(universe=["a"], brokers={"alpaca": {"enabled": True}, "ibkr": {"enabled": True}})
    assert c.universe == ["A"] and c.brokers.routing == "mirror"
