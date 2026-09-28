"""Risk layer. Sits between signals and orders and can veto anything."""
from __future__ import annotations
from dataclasses import dataclass
from .config import RiskCfg


def size_position(equity: float, cash: float, entry: float, stop: float,
                  risk_pct: float, max_pos_pct: float) -> int:
    """Whole shares such that hitting the stop loses ~risk_pct of equity,
    capped by max position size and available cash."""
    if entry <= 0 or stop is None or stop >= entry or equity <= 0:
        return 0
    per_share = entry - stop
    q_risk = (equity * risk_pct / 100.0) / per_share
    q_cap = equity * max_pos_pct / 100.0 / entry
    q_cash = cash / entry
    return int(max(0, min(q_risk, q_cap, q_cash)))


@dataclass
class Decision:
    qty: int
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.qty > 0


class RiskManager:
    def __init__(self, cfg: RiskCfg):
        self.cfg = cfg

    def daily_loss_hit(self, day_start_equity: float | None, equity: float) -> bool:
        if not day_start_equity or day_start_equity <= 0:
            return False
        return (day_start_equity - equity) / day_start_equity * 100 >= self.cfg.max_daily_loss_pct

    def evaluate_entry(self, *, killed: bool, symbol: str, price: float, stop: float,
                       equity: float, cash: float, allocation: float,
                       open_symbols: set[str], gross_exposure: float) -> Decision:
        c = self.cfg
        if killed:
            return Decision(0, "kill switch / daily loss halt active")
        if symbol in open_symbols:
            return Decision(0, "already holding this symbol")
        if len(open_symbols) >= c.max_open_positions:
            return Decision(0, f"max open positions ({c.max_open_positions}) reached")
        if price < c.min_price:
            return Decision(0, f"price below minimum {c.min_price}")
        eff_equity = equity * allocation
        room = eff_equity * c.max_gross_exposure_pct / 100.0 - gross_exposure
        if room <= 0:
            return Decision(0, "gross exposure limit reached")
        qty = size_position(eff_equity, min(cash, eff_equity), price, stop,
                            c.risk_per_trade_pct, c.max_position_pct)
        qty = min(qty, int(room / price))
        if qty <= 0:
            return Decision(0, "size rounds to zero (stop too wide or cash too low)")
        return Decision(qty, "ok")
