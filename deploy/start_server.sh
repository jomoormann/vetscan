#!/bin/bash
# VetScan Server Startup Script
# Uses gunicorn with uvicorn workers for robust process management

set -e

APP_DIR="$HOME/domains/vetscan.net/app"
LOG_DIR="$APP_DIR/logs"
PID_FILE="$APP_DIR/gunicorn.pid"

cd "$APP_DIR"
source venv/bin/activate

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Kill any existing processes
echo "$(date): Stopping existing server..." >> "$LOG_DIR/startup.log"
if [ -f "$PID_FILE" ]; then
    kill -TERM $(cat "$PID_FILE") 2>/dev/null || true
    rm -f "$PID_FILE"
fi
# Also kill any orphaned processes
pkill -f "gunicorn.*vetscan" 2>/dev/null || true
pkill -f "uvicorn.*web_server" 2>/dev/null || true
sleep 2

# Start gunicorn with uvicorn workers
echo "$(date): Starting gunicorn..." >> "$LOG_DIR/startup.log"
export PYTHONPATH="$APP_DIR/src"

exec gunicorn src.web_server:app \
    --bind 0.0.0.0:8000 \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 1 \
    --timeout 120 \
    --graceful-timeout 30 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --pid "$PID_FILE" \
    --access-logfile "$LOG_DIR/access.log" \
    --error-logfile "$LOG_DIR/error.log" \
    --capture-output \
    --log-level info \
    >> "$LOG_DIR/gunicorn.log" 2>&1
