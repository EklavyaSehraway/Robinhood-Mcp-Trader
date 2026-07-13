#!/bin/zsh
# Stops the paper trader server. State is on disk, so nothing is lost.
PROJECT="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$PROJECT/backend/state/server.pid"

if [[ -f "$PIDFILE" ]]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null
  rm -f "$PIDFILE"
fi
# Belt and suspenders: kill whatever holds port 8000 (our uvicorn only).
PIDS=$(lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null)
if [[ -n "$PIDS" ]]; then
  echo "$PIDS" | xargs kill 2>/dev/null
fi

osascript -e 'display notification "Server stopped. Portfolio state is saved." with title "Swing Trader"' >/dev/null 2>&1
exit 0
