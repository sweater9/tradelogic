"""Market data providers. All return dict[symbol] -> DataFrame(open,high,low,close,volume)
indexed by tz-naive trading date, with the still-forming daily bar removed."""
from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd

NY = ZoneInfo("America/New_York")
COLS = ["open", "high", "low", "close", "volume"]


def drop_incomplete(df: pd.DataFrame, now: dt.datetime | None = None) -> pd.DataFrame:
    """Remove today's bar if the market hasn't closed yet (avoids acting on partial candles)."""
    if df.empty:
        return df
    now = now or dt.datetime.now(NY)
    today = pd.Timestamp(now.date())
    if df.index[-1] >= today and now.hour < 16:
        return df.iloc[:-1]
    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)[COLS].dropna()
    df.index = pd.to_datetime(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return drop_incomplete(df)


class DataProvider:
    def history(self, symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
        raise NotImplementedError


class YFinanceProvider(DataProvider):
    def history(self, symbols, days):
        import yfinance as yf
        raw = yf.download(symbols, period=f"{max(days, 30)}d", auto_adjust=True,
                          group_by="ticker", progress=False, threads=True)
        out = {}
        for s in symbols:
            try:
                df = raw[s] if len(symbols) > 1 else raw
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(-1)
                df = _clean(df)
                if len(df):
                    out[s] = df
            except Exception:
                continue
        return out


class AlpacaProvider(DataProvider):
    def __init__(self, broker):
        self.broker = broker

    def history(self, symbols, days):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import Adjustment
        start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=int(days * 1.6))
        out = {}
        for i in range(0, len(symbols), 50):
            chunk = symbols[i:i + 50]
            req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Day, start=start,
                                   adjustment=Adjustment.ALL, feed=self.broker.feed)
            df = self.broker.data.get_stock_bars(req).df
            if df.empty:
                continue
            for s in chunk:
                if s in df.index.get_level_values(0):
                    d = df.xs(s, level=0).copy()
                    d.index = pd.to_datetime(d.index).tz_convert(NY).tz_localize(None)
                    out[s] = _clean(d)
        return out


class IbkrProvider(DataProvider):
    def __init__(self, broker):
        self.broker = broker

    def history(self, symbols, days):
        out = {}
        for s in symbols:
            try:
                df = self.broker.history(s, days)
                if df is not None and len(df):
                    out[s] = _clean(df)
            except Exception:
                continue
        return out


def make_provider(cfg, brokers: dict) -> DataProvider:
    p = cfg.data.provider
    if p == "yfinance":
        return YFinanceProvider()
    if p == "alpaca":
        return AlpacaProvider(brokers["alpaca"])
    return IbkrProvider(brokers["ibkr"])
