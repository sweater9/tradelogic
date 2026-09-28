# Running TradeLogic 24/7

Pick ONE: Docker (simplest) or systemd (no Docker). Both run the engine and the dashboard as
separate services that restart automatically after a crash or reboot.

Prepare first (in the project folder): `config.yaml` (from config.example.yaml) and `.env`
(from .env.example, with your paper Alpaca keys and a long random DASHBOARD_TOKEN).
Run `python -m tradelogic check` once on your laptop to confirm the keys work.

## Option A: Docker
```
docker compose up -d --build
docker compose ps                 # engine should become "healthy" after ~1 minute
docker compose logs -f engine
```
- Dashboard: http://127.0.0.1:8000 on that machine.
- Data (database, history) lives in the `tl_data` volume and survives rebuilds.
- Update: `git pull` / replace files, then `docker compose up -d --build`.
- Change settings: edit config.yaml, then `docker compose restart engine api`.
- Stop: `docker compose down` (keeps data). Broker-side stop-losses stay active at Alpaca.

## Option B: systemd (Linux)
```
sudo bash deploy/install-systemd.sh
systemctl status tradelogic-engine tradelogic-api
journalctl -u tradelogic-engine -f
```
- Runs as an unprivileged `tradelogic` user, database in /var/lib/tradelogic.
- Change settings: edit /opt/tradelogic/config.yaml, then `sudo systemctl restart tradelogic-engine tradelogic-api`.
- Update: re-run the install script from the new project folder (it keeps your database).

## Reaching the dashboard from your phone
The dashboard listens on the machine's loopback only, on purpose. Do NOT forward port 8000 to the internet.
Good options:
- **Tailscale** (easiest): install on the server and your phone, then use `tailscale serve --bg 8000`
  and open the private https address it prints.
- **SSH tunnel**: `ssh -L 8000:127.0.0.1:8000 you@server`, then open http://127.0.0.1:8000.
- Any VPN you already trust.
The dashboard also requires your DASHBOARD_TOKEN.

## Choosing the machine
A small always-on box is enough (1 vCPU, 512 MB-1 GB RAM): a home mini-PC/Raspberry Pi, or a cheap VPS.
Use a machine with a UPS or reliable power/internet. If it goes down, existing positions are still
protected by the stop-loss orders held at the broker, but no new trades or exits happen until it's back.

## Alerts (recommended)
- `docker compose ps` / `systemctl is-active` show state; the dashboard shows "Engine off" if the heartbeat stops.
- For push alerts, point a free uptime monitor (e.g. Healthchecks.io) at a cron job running
  `python -m tradelogic health`, which exits non-zero when the engine is stale.

## Adding Interactive Brokers later
IBKR needs TWS or IB Gateway running and logged in on the SAME machine (it restarts daily and needs 2FA
unless you use a tool such as IBC to automate it). Then in config.yaml set `ibkr.enabled: true`.
- systemd: use `host: 127.0.0.1` and add `After=` for your gateway service in tradelogic-engine.service.
- Docker: use `host: host.docker.internal`, and in TWS/Gateway API settings allow connections from the
  Docker network (uncheck "Allow connections from localhost only" or add the docker bridge IP as trusted).
Start with the IBKR PAPER account (port 7497 TWS / 4002 Gateway).

## Backups
Back up the database occasionally: `docker compose cp engine:/data/tradelogic.db ./backup.db` or copy
/var/lib/tradelogic/tradelogic.db. It holds history and logs, not credentials.
