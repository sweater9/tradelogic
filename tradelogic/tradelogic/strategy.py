"""Trend-breakout strategy (long only, daily bars).

The SAME `prepare()` output drives both the backtester and the live scanner,
so what you backtest is what you trade.
"""
from __future__ import annotations
import pandas as pd
from .config import StrategyCfg
from .indicators import sma, ema, rsi, atr
from .models import Signal


def regime_series(bench: pd.DataFrame, p: StrategyCfg) -> pd.Series:
    return bench["close"] > sma(bench["close"], p.regime_sma)


def prepare(df: pd.DataFrame, regime: pd.Series, p: StrategyCfg, min_price: float = 5.0) -> pd.DataFrame:
    d = df[["open", "high", "low", "close", "volume"]].astype(float).copy()
    c = d["close"]
    d["sma_trend"] = sma(c, p.trend_sma)
    d["ema_fast"] = ema(c, p.ema_fast)
    d["ema_slow"] = ema(c, p.ema_slow)
    d["rsi"] = rsi(c, 14)
    d["atr"] = atr(d, 14)
    d["hh"] = c.shift(1).rolling(p.breakout_lookback).max()
    d["vol_avg"] = d["volume"].rolling(20).mean()
    d["dollar_vol"] = (c * d["volume"]).rolling(20).mean()
    d["ret_63"] = c.pct_change(63)
    d["chandelier"] = c.rolling(22).max() - p.trail_atr_mult * d["atr"]
    d["regime"] = regime.astype(float).reindex(d.index).ffill().fillna(0) > 0
    d["entry"] = (
        (c > d["sma_trend"])
        & (d["ema_fast"] > d["ema_slow"])
        & (c > d["hh"])
        & (d["rsi"] >= p.rsi_min) & (d["rsi"] <= p.rsi_max)
        & (d["volume"] > p.volume_mult * d["vol_avg"])
        & (d["atr"] / c * 100 <= p.max_atr_pct)
        & (d["dollar_vol"] >= p.min_dollar_volume)
        & (c >= min_price)
        & d["regime"]
    ).fillna(False)
    d["exit"] = ((c < d["ema_slow"]) | (c < d["chandelier"])).fillna(False)
    d["stop"] = c - p.stop_atr_mult * d["atr"]
    d["score"] = d["ret_63"].fillna(-1)
    return d


def scan(frames: dict[str, pd.DataFrame], bench: pd.DataFrame, p: StrategyCfg,
         held: set[str], min_price: float = 5.0) -> list[Signal]:
    regime = regime_series(bench, p)
    out: list[Signal] = []
    for sym, df in frames.items():
        if len(df) < p.trend_sma + 5:
            continue
        d = prepare(df, regime, p, min_price)
        r = d.iloc[-1]
        px = float(r["close"])
        if sym in held:
            if bool(r["exit"]):
                why = f"close below EMA{p.ema_slow}" if r["close"] < r["ema_slow"] else "trailing (chandelier) stop hit"
                out.append(Signal(sym, "SELL", px, reason=why, score=0))
        elif bool(r["entry"]):
            stop = float(r["stop"])
            out.append(Signal(
                sym, "BUY", px, stop=stop, score=float(r["score"]),
                reason=(f"{p.breakout_lookback}d breakout, RSI {r['rsi']:.0f}, "
                        f"vol {r['volume']/r['vol_avg']:.1f}x avg, "
                        f"63d ret {r['ret_63']*100:.1f}%, ATR {r['atr']/px*100:.1f}%"),
            ))
    out.sort(key=lambda s: (s.action != "SELL", -s.score))
    return out
