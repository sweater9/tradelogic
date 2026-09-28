#!/bin/bash
# Double-click to start TradeLogic (Mac). Sets itself up the first time.
cd "$(dirname "$0")" || exit 1
pause() { echo; read -r -p "Press Return to close this window..."; }

echo "=== TradeLogic ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ then double-click Start.command again."
  pause; exit 1
fi

[ -f config.yaml ] || cp config.example.yaml config.yaml

if [ ! -d .venv ]; then
  echo "First-time setup. This takes a few minutes, please wait..."
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt \
    || { rm -rf .venv; echo "Setup failed. Take a screenshot of the messages above and send it to Claude."; pause; exit 1; }
fi
PY=.venv/bin/python

getv() { grep -E "^$1=" settings.env | head -1 | cut -d= -f2- | tr -d ' \r"'"'"; }

if [ -z "$(getv ALPACA_API_KEY)" ] || [ -z "$(getv ALPACA_SECRET_KEY)" ]; then
  echo "Your Alpaca keys are not filled in yet."
  echo "I'm opening settings.env. Paste your PAPER key and secret after the '=' signs, save (Cmd+S), then double-click Start.command again."
  open -e settings.env
  pause; exit 1
fi

TOKEN="$(getv DASHBOARD_TOKEN)"
if [ "${#TOKEN}" -lt 16 ]; then
  TOKEN="$($PY -c 'import secrets;print(secrets.token_urlsafe(32))')"
  $PY - "$TOKEN" <<'PYEOF'
import re, sys
t = sys.argv[1]
s = open("settings.env").read()
s = re.sub(r"^DASHBOARD_TOKEN=.*$", "DASHBOARD_TOKEN=" + t, s, flags=re.M)
open("settings.env", "w").write(s)
PYEOF
  echo "Created a private dashboard password (saved in settings.env)."
fi

echo "Checking your Alpaca connection..."
if ! $PY -m tradelogic check; then
  echo
  echo "Could not connect. Most common causes: a typo in the keys, or LIVE keys used while the app is in paper mode."
  echo "Fix settings.env and double-click Start.command again."
  pause; exit 1
fi

trap 'kill 0' EXIT INT TERM
$PY -m tradelogic run &
$PY -m tradelogic api &
sleep 4
open "http://127.0.0.1:8000/?token=$TOKEN"
echo
echo "TradeLogic is running. The dashboard opened in your browser."
echo "Keep this window open. Close it (or press Ctrl+C) to stop. Existing stop-losses at Alpaca stay active."
wait
