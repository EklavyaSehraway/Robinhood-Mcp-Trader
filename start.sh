#!/bin/zsh
# Starts the weekly-swing paper trader (backend + dashboard on :8000).
cd "$(dirname "$0")/backend"
exec ./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
