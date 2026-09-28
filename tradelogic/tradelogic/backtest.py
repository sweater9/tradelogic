"""Event-driven daily backtester using the same strategy + sizing + limits as live.

Realism rules: signals form on a bar's CLOSE and fill at the NEXT bar's OPEN (no look-ahead);
stops fill at min(open, stop) on the bar whose low breaches them; slippage + commissions applied;
positions are sized by risk_per_trade with position/exposure/count caps."""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd
from .config import Config
from .risk import size_position
from .strategy import prepare, regime_series


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def _metrics(eq: pd.Series, trades: pd.DataFrame, bench: pd.Series) -> dict:
    def stats(s: pd.Series) -> dict:
        r = s.pct_change().dropna()
        yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
        return {
            "total_return_pct": (s.iloc[-1] / s.iloc[0] - 1) * 100,
            "cagr_pct": ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100,
            "sharpe": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0,
            "max_drawdown_pct": float((s / s.cummax() - 1).min() * 100),
        }
    m = {"strategy": stats(eq), "benchmark": stats(bench)}
    if len(trades):
        w, l = trades[trades.pnl > 0], trades[trades.pnl <= 0]
        m["trades"] = {
            "count": int(len(trades)),
            "win_rate_pct": float(len(w) / len(trades) * 100),
            "profit_factor": float(w.pnl.sum() / -l.pnl.sum()) if l.pnl.sum() < 0 else float("inf"),
            "avg_win": float(w.pnl.mean()) if len(w) else 0.0,
            "avg_loss": float(l.pnl.mean()) if len(l) else 0.0,
            "avg_hold_days": float(trades.hold_days.mean()),
        }
    else:
        m["trades"] = {"count": 0}
    return m


def run_backtest(frames: dict[str, pd.DataFrame], bench: pd.DataFrame, cfg: Config) -> BacktestResult:
    sp, rk, bt = cfg.strategy, cfg.risk, cfg.backtest
    slip = bt.slippage_bps / 10_000.0
    comm = bt.commission_per_share
    regime = regime_series(bench, sp)
    dates = bench.index
    arr = {}
    for s, df in frames.items():
        d = prepare(df, regime, sp, rk.min_price).reindex(dates)
        arr[s] = {k: d[k].to_numpy() for k in ("open", "high", "low", "close", "entry", "exit", "stop", "score")}
        arr[s]["entry"] = np.nan_to_num(arr[s]["entry"].astype(float)) > 0
        arr[s]["exit"] = np.nan_to_num(arr[s]["exit"].astype(float)) > 0

    cash = bt.initial_capital
    pos: dict[str, dict] = {}
    last_close: dict[str, float] = {}
    trades: list[dict] = []
    curve = []
    pend_exit: set[str] = set()
    pend_entry: list[tuple[str, float, float]] = []  # sym, score, stop

    def close_pos(s, px, i, why):
        nonlocal cash
        p = pos.pop(s)
        proceeds = p["qty"] * px - p["qty"] * comm
        cash += proceeds
        pnl = proceeds - p["cost"]
        trades.append(dict(symbol=s, entry_date=p["date"], exit_date=dates[i], qty=p["qty"],
                           entry_px=p["px"], exit_px=px, pnl=pnl, reason=why,
                           hold_days=(dates[i] - p["date"]).days))

    for i, dt_ in enumerate(dates):
        # 1) fills at today's open from yesterday's signals
        for s in list(pend_exit):
            o = arr[s]["open"][i]
            if s in pos and not np.isnan(o):
                close_pos(s, o * (1 - slip), i, "signal")
        pend_exit.clear()
        eq_open = cash + sum(p["qty"] * last_close.get(s, p["px"]) for s, p in pos.items())
        for s, _, stop in sorted(pend_entry, key=lambda t: -t[1]):
            if s in pos or len(pos) >= rk.max_open_positions:
                continue
            o = arr[s]["open"][i]
            if np.isnan(o) or o <= stop:
                continue
            px = o * (1 + slip)
            gross = sum(p["qty"] * last_close.get(k, p["px"]) for k, p in pos.items())
            room = eq_open * rk.max_gross_exposure_pct / 100 - gross
            qty = min(size_position(eq_open, cash, px, stop, rk.risk_per_trade_pct, rk.max_position_pct),
                      int(max(room, 0) / px))
            if qty <= 0:
                continue
            cost = qty * px + qty * comm
            if cost > cash:
                continue
            cash -= cost
            pos[s] = dict(qty=qty, px=px, stop=stop, date=dt_, cost=cost)
        pend_entry = []

        # 2) protective stops (checked against today's range, incl. positions opened today)
        for s in list(pos):
            lo, o = arr[s]["low"][i], arr[s]["open"][i]
            if not np.isnan(lo) and lo <= pos[s]["stop"]:
                close_pos(s, min(o, pos[s]["stop"]) * (1 - slip), i, "stop")

        # 3) mark to market
        for s in arr:
            c = arr[s]["close"][i]
            if not np.isnan(c):
                last_close[s] = c
        curve.append(cash + sum(p["qty"] * last_close.get(s, p["px"]) for s, p in pos.items()))

        # 4) form signals on today's close for tomorrow's open
        for s in pos:
            if arr[s]["exit"][i]:
                pend_exit.add(s)
        for s in arr:
            if s not in pos and arr[s]["entry"][i] and not np.isnan(arr[s]["stop"][i]):
                pend_entry.append((s, float(arr[s]["score"][i]), float(arr[s]["stop"][i])))

    # liquidate at the end so trade stats include open positions
    for s in list(pos):
        close_pos(s, last_close.get(s, pos[s]["px"]), len(dates) - 1, "end")

    eq = pd.Series(curve, index=dates)
    first = int(np.argmax(eq.values != eq.values[0])) if (eq.values != eq.values[0]).any() else 0
    start = max(first - 1, 0)
    eq = eq.iloc[start:]
    b = bench["close"].loc[eq.index]
    b = b / b.iloc[0] * bt.initial_capital
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        tdf = pd.DataFrame(columns=["symbol", "entry_date", "exit_date", "qty", "entry_px", "exit_px", "pnl", "reason", "hold_days"])
    return BacktestResult(eq, b, tdf, _metrics(eq, tdf, b))
