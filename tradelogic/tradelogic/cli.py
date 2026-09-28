from __future__ import annotations
import argparse
import sys


def main(argv=None):
    ap = argparse.ArgumentParser("tradelogic")
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="start the trading engine")
    a = sub.add_parser("api", help="start the dashboard")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)
    sub.add_parser("health", help="exit 0 if the engine heartbeat is fresh (used by Docker healthcheck)")
    sub.add_parser("check", help="test broker connections and print account info")
    sub.add_parser("scan", help="dry-run scan: print signals, place nothing")
    b = sub.add_parser("backtest", help="backtest the strategy on the configured universe")
    b.add_argument("--years", type=int, default=5)
    b.add_argument("--csv", help="write trades to this CSV")
    args = ap.parse_args(argv)

    from .config import load_config
    if args.cmd == "api":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(args.config), host=args.host, port=args.port)
        return
    cfg = load_config(args.config)

    if args.cmd == "health":
        import time
        from .db import DB
        hb = DB(cfg.db_path).get("heartbeat")
        age = time.time() - float(hb) if hb else None
        ok = age is not None and age < cfg.schedule.poll_seconds * 6 + 30
        print(f"engine heartbeat: {'%.0fs ago' % age if age is not None else 'never'} -> {'OK' if ok else 'STALE'}")
        sys.exit(0 if ok else 1)

    if args.cmd == "run":
        from .engine import Engine
        Engine(cfg).start()

    elif args.cmd == "check":
        from .brokers import make_brokers
        for name, b in make_brokers(cfg).items():
            b.connect()
            a = b.account()
            print(f"{name}: OK  equity={a.equity:,.2f} cash={a.cash:,.2f} buying_power={a.buying_power:,.2f}  "
                  f"positions={len(b.positions())}  mode={cfg.mode}")

    elif args.cmd == "scan":
        from .brokers import make_brokers
        from .data import make_provider
        from .strategy import scan
        brokers = make_brokers(cfg)
        for b in brokers.values():
            b.connect()
        prov = make_provider(cfg, brokers)
        frames = prov.history(sorted(set(cfg.universe) | {cfg.benchmark}), cfg.data.history_days)
        bench = frames.pop(cfg.benchmark)
        held = {p.symbol for b in brokers.values() for p in b.positions()}
        for s in scan(frames, bench, cfg.strategy, held, cfg.risk.min_price):
            print(f"{s.action:4} {s.symbol:6} px={s.price:8.2f} stop={s.stop or 0:8.2f} score={s.score:6.2f}  {s.reason}")

    elif args.cmd == "backtest":
        from .data import YFinanceProvider
        from .backtest import run_backtest
        prov = YFinanceProvider()
        days = args.years * 365 + 300
        frames = prov.history(sorted(set(cfg.universe) | {cfg.benchmark}), days)
        bench = frames.pop(cfg.benchmark)
        res = run_backtest(frames, bench, cfg)
        m = res.metrics
        print(f"Backtest {res.equity.index[0].date()} -> {res.equity.index[-1].date()}  ({len(frames)} symbols)")
        for k in ("strategy", "benchmark"):
            x = m[k]
            print(f"{k:10} return {x['total_return_pct']:7.1f}%  CAGR {x['cagr_pct']:6.1f}%  "
                  f"Sharpe {x['sharpe']:5.2f}  MaxDD {x['max_drawdown_pct']:6.1f}%")
        print("trades:", m["trades"])
        if args.csv:
            res.trades.to_csv(args.csv, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
