# TradeLogic

A self-hosted trading terminal for **your own account**. It scans stocks after the close, produces
buy/sell signals, and places orders through **Alpaca**, **Interactive Brokers**, or **both**.
Paper mode and human approval are the defaults.

## How it works
- `engine` (one process): scans on schedule, creates signals, executes approved ones, enforces risk, writes state to SQLite.
- `api` (second process): dashboard at http://127.0.0.1:8000 (phone-friendly). Decide on signals with a risk preview, see holdings and P&L, stop trading, close everything. Open `http://127.0.0.1:8000/?demo` to preview the UI with fake data.
- Strategy: long-only trend breakout (20-day breakout above 200-day SMA, EMA20>EMA50, RSI, volume, ATR filters,
  only when the benchmark is above its 200-day). Stop = 2xATR, placed **at the broker** with the entry.
  The identical logic drives the backtester and live scanner.

## Broker options (config.yaml)
| Setup | `alpaca.enabled` | `ibkr.enabled` | `routing` |
|---|---|---|---|
| Alpaca only | true | false | - |
| IBKR only | false | true | - |
| Both, same trade on each | true | true | `mirror` (each sized to its own equity) |
| Both, backup broker | true | true | `failover` (first that succeeds) |

## Easiest start on a Mac (no typing in Terminal)
1. Unzip, open `settings.env` in TextEdit, paste your Alpaca PAPER key and secret, save.
2. Double-click `Start.command` (first time: right-click > Open, then Open). It installs itself, checks your Alpaca
   connection, starts everything and opens the dashboard. Requires Python 3 (python.org).
Closing that window stops trading; for 24/7 use DEPLOY.md.

## Setup (manual)
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env        # or edit settings.env; add Alpaca paper keys + a long random DASHBOARD_TOKEN
# Interactive Brokers later: pip install -r requirements-ibkr.txt (Python 3.10+)
```
**IBKR:** run TWS or IB Gateway, log in to your PAPER account, enable *Settings > API > Enable ActiveX and Socket Clients*,
keep port 7497 (TWS paper) or 4002 (Gateway paper). Live ports (7496/4001) are refused while `mode: paper`.

## Run
```
python -m tradelogic check                 # test broker connections
python -m tradelogic backtest --years 5    # BEFORE trading anything
python -m tradelogic scan                  # dry run, places nothing
python -m tradelogic run                   # engine (terminal 1)
python -m tradelogic api                   # dashboard (terminal 2)
pytest                                     # 20 tests
```
To run 24/7 with Docker or systemd, see **DEPLOY.md**.

## Safety design
- Paper by default. `mode: live` additionally needs `TRADELOGIC_CONFIRM_LIVE=I_UNDERSTAND_THE_RISKS`.
- `auto_execute: false`: nothing trades until you tap Approve.
- Risk layer can veto any entry: kill switch, daily-loss halt, max positions, max position size, gross exposure, min price, chase guard.
- "Stop new trades" keeps existing broker-side stops. "Close everything" cancels all orders (including stops) and sells all positions.
- Dashboard needs a token and listens on localhost only. Put it behind a VPN/SSH tunnel if you need remote access.

## Before real money
1. Backtest across different periods; compare to buy-and-hold SPY (the strategy has to earn its complexity).
2. Paper trade 1-3 months in approval mode; compare fills to backtest assumptions.
3. Go live with a small amount, keep approval mode on, watch the first weeks daily.

## Known limits
- Daily bars, US stocks, long-only. Whole shares. Stop is fixed at entry (no trailing modification at the broker;
  the strategy's trailing/EMA exit is a daily-close signal you approve).
- Data: yfinance is free but unofficial; prefer Alpaca or IBKR data for live use.
- Not tested against live brokers in development (no credentials): run `check` and paper trade first.
- Nothing here guarantees profit. Losses are yours. Tax and regulatory duties are yours. Not financial advice.
