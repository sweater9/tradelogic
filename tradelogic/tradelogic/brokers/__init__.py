from .base import Broker, BrokerError


def make_brokers(cfg) -> dict[str, Broker]:
    """Build enabled brokers in priority order (alpaca first, then ibkr)."""
    out: dict[str, Broker] = {}
    if cfg.brokers.alpaca.enabled:
        from .alpaca import AlpacaBroker
        out["alpaca"] = AlpacaBroker(cfg.brokers.alpaca, paper=(cfg.mode == "paper"))
    if cfg.brokers.ibkr.enabled:
        from .ibkr import IbkrBroker
        out["ibkr"] = IbkrBroker(cfg.brokers.ibkr)
    return out
