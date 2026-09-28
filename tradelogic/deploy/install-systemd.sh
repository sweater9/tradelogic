#!/usr/bin/env bash
# Installs TradeLogic as two systemd services on Debian/Ubuntu-style Linux.
# Run from the project folder:  sudo bash deploy/install-systemd.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }
[ -f config.yaml ] && [ -f .env ] || { echo "Create config.yaml and .env first (see config.example.yaml, .env.example)"; exit 1; }

id tradelogic >/dev/null 2>&1 || useradd --system --home /opt/tradelogic --shell /usr/sbin/nologin tradelogic
mkdir -p /opt/tradelogic
cp -r tradelogic requirements.txt requirements-ibkr.txt config.yaml .env /opt/tradelogic/
python3 -m venv /opt/tradelogic/.venv
/opt/tradelogic/.venv/bin/pip install -q -r /opt/tradelogic/requirements.txt
# For Interactive Brokers later: /opt/tradelogic/.venv/bin/pip install -r /opt/tradelogic/requirements-ibkr.txt
chown -R root:tradelogic /opt/tradelogic
chmod 640 /opt/tradelogic/.env /opt/tradelogic/config.yaml
chmod -R g+rX /opt/tradelogic

cp deploy/systemd/tradelogic-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tradelogic-engine tradelogic-api
echo
echo "Installed. Check status:  systemctl status tradelogic-engine tradelogic-api"
echo "Logs:                    journalctl -u tradelogic-engine -f"
echo "Dashboard:               http://127.0.0.1:8000 (on that machine)"
