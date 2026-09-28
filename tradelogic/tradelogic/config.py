from __future__ import annotations
import os
from typing import Literal
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator


class AlpacaCfg(BaseModel):
    enabled: bool = True
    allocation: float = Field(1.0, gt=0, le=1)
    feed: Literal["iex", "sip"] = "iex"


class IbkrCfg(BaseModel):
    enabled: bool = False
    allocation: float = Field(1.0, gt=0, le=1)
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 17
    market_data_type: int = 1


class BrokersCfg(BaseModel):
    routing: Literal["mirror", "failover"] = "mirror"
    alpaca: AlpacaCfg = AlpacaCfg()
    ibkr: IbkrCfg = IbkrCfg()


class DataCfg(BaseModel):
    provider: Literal["alpaca", "ibkr", "yfinance"] = "yfinance"
    history_days: int = 420


class StrategyCfg(BaseModel):
    trend_sma: int = 200
    ema_fast: int = 20
    ema_slow: int = 50
    breakout_lookback: int = 20
    rsi_min: float = 50
    rsi_max: float = 78
    volume_mult: float = 1.2
    max_atr_pct: float = 6.0
    min_dollar_volume: float = 20_000_000
    stop_atr_mult: float = 2.0
    trail_atr_mult: float = 3.0
    regime_sma: int = 200


class RiskCfg(BaseModel):
    risk_per_trade_pct: float = 0.5
    max_position_pct: float = 10
    max_open_positions: int = 8
    max_gross_exposure_pct: float = 80
    max_daily_loss_pct: float = 2
    min_price: float = 5
    entry_limit_slippage_pct: float = 0.3
    max_chase_pct: float = 1.5


class ScheduleCfg(BaseModel):
    scan_time: str = "16:20"
    timezone: str = "America/New_York"
    poll_seconds: int = 10
    signal_ttl_hours: int = 20


class BacktestCfg(BaseModel):
    initial_capital: float = 100_000
    commission_per_share: float = 0.005
    slippage_bps: float = 5


class Config(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    auto_execute: bool = False
    db_path: str = "tradelogic.db"
    brokers: BrokersCfg = BrokersCfg()
    data: DataCfg = DataCfg()
    benchmark: str = "SPY"
    universe: list[str]
    strategy: StrategyCfg = StrategyCfg()
    risk: RiskCfg = RiskCfg()
    schedule: ScheduleCfg = ScheduleCfg()
    backtest: BacktestCfg = BacktestCfg()

    @model_validator(mode="after")
    def _checks(self):
        if not (self.brokers.alpaca.enabled or self.brokers.ibkr.enabled):
            raise ValueError("Enable at least one broker (alpaca and/or ibkr).")
        live_ports, paper_ports = {7496, 4001}, {7497, 4002}
        if self.brokers.ibkr.enabled:
            p = self.brokers.ibkr.port
            if self.mode == "paper" and p in live_ports:
                raise ValueError(f"mode is paper but IBKR port {p} is a LIVE port.")
            if self.mode == "live" and p in paper_ports:
                raise ValueError(f"mode is live but IBKR port {p} is a PAPER port.")
        if self.data.provider == "alpaca" and not self.brokers.alpaca.enabled:
            raise ValueError("data.provider=alpaca needs brokers.alpaca.enabled=true")
        if self.data.provider == "ibkr" and not self.brokers.ibkr.enabled:
            raise ValueError("data.provider=ibkr needs brokers.ibkr.enabled=true")
        self.universe = [s.upper() for s in self.universe]
        return self


def load_config(path: str = "config.yaml") -> Config:
    from pathlib import Path
    load_dotenv(Path.cwd() / "settings.env")   # visible file, easy to edit in TextEdit/Notepad
    load_dotenv()                                # or a hidden .env
    with open(path) as f:
        cfg = Config(**yaml.safe_load(f))
    if os.environ.get("TRADELOGIC_DB"):      # lets Docker/systemd pin the DB location
        cfg.db_path = os.environ["TRADELOGIC_DB"]
    return cfg


def assert_live_confirmed(cfg: Config) -> None:
    if cfg.mode == "live" and os.environ.get("TRADELOGIC_CONFIRM_LIVE") != "I_UNDERSTAND_THE_RISKS":
        raise SystemExit(
            "Refusing to start in LIVE mode. Set TRADELOGIC_CONFIRM_LIVE=I_UNDERSTAND_THE_RISKS "
            "in .env only after weeks of paper trading."
        )
