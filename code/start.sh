#!/bin/bash

# DataVault Dashboard - Quick Start Script
# This script stops any existing server, starts fresh, and opens Safari.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check if we're in the right directory
if [ ! -f "config.json" ]; then
    echo "Error: config.json not found. Please run from project root."
    exit 1
fi

# ---- Stop any existing server on port 8000 ----
echo "Checking for existing DataVault server..."
EXISTING_PID=$(lsof -ti :8000 2>/dev/null)
if [ ! -z "$EXISTING_PID" ]; then
    echo "Stopping existing server (PID: $EXISTING_PID)..."
    kill $EXISTING_PID 2>/dev/null
    sleep 1
    # Force kill if still running
    if kill -0 $EXISTING_PID 2>/dev/null; then
        kill -9 $EXISTING_PID 2>/dev/null
    fi
    echo "Old server stopped."
fi

# Clean up stale PID file
rm -f /tmp/datavault_server.pid

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Ensure ffmpeg is in PATH
export PATH="/usr/local/bin:$PATH"

echo ""
echo "Starting DataVault Dashboard..."
echo "Server will be available at: http://127.0.0.1:8000"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Open Safari once the server is confirmed up (polls in background)
(
    for i in $(seq 1 30); do
        if curl -s -o /dev/null -w '' http://127.0.0.1:8000 2>/dev/null; then
            open -a Safari "http://127.0.0.1:8000"
            break
        fi
        sleep 1
    done
) &

# Start the server
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
