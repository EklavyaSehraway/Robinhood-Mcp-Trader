#!/bin/zsh
# Launches the paper trader in the background (idempotent) and opens the dashboard.
# Called by the "Swing Trader" desktop app; safe to run by hand too.
PROJECT="$(cd "$(dirname "$0")" && pwd)"
URL="http://127.0.0.1:8000"
LOG="$PROJECT/backend/state/server.log"
PIDFILE="$PROJECT/backend/state/server.pid"

# Already healthy? Just open the dashboard.
if curl -s -m 2 "$URL/api/status" >/dev/null 2>&1; then
  open "$URL"
  exit 0
fi

mkdir -p "$PROJECT/backend/state"
cd "$PROJECT/backend"
nohup ./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"

# Wait up to 15s for the server to come up, then open the browser.
for i in {1..30}; do
  if curl -s -m 1 "$URL/api/status" >/dev/null 2>&1; then
    open "$URL"
    exit 0
  fi
  sleep 0.5
done

osascript -e 'display alert "Swing Trader failed to start" message "Check backend/state/server.log in the Robinhoodmcptrader folder."' >/dev/null 2>&1
exit 1
