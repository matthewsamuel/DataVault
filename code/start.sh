#!/bin/bash

# DataVault Dashboard - Quick Start Script
# Stops a previous DataVault server, starts a fresh one and opens the browser.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG_FILE="${DATAVAULT_CONFIG:-config.json}"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: $CONFIG_FILE not found. Copy config.example.json to config.json and edit the paths."
    exit 1
fi

PYTHON="$SCRIPT_DIR/venv/bin/python"

# ---- Create the virtual environment on first run ----
if [ ! -x "$PYTHON" ]; then
    echo "Creating virtual environment..."
    python3.12 -m venv venv || python3 -m venv venv
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -r requirements.txt || exit 1
fi

# ---- Stop an existing DataVault server on port 8000 (listeners only, never clients) ----
# Only a process whose command line is a DataVault uvicorn (backend.main) is stopped; any
# other program on port 8000 is left alone and the start is aborted.
echo "Checking for existing DataVault server..."
for EXISTING_PID in $(lsof -ti :8000 -sTCP:LISTEN 2>/dev/null); do
    COMMAND=$(ps -o command= -p "$EXISTING_PID" 2>/dev/null)
    case "$COMMAND" in
        *uvicorn*backend.main*) ;;
        *)
            echo "Error: port 8000 is in use by another program (PID $EXISTING_PID): $COMMAND"
            echo "Stop that program first; DataVault does not stop it for you."
            exit 1
            ;;
    esac
    echo "Stopping existing server (PID: $EXISTING_PID)..."
    kill "$EXISTING_PID" 2>/dev/null
    # Give it time to finish open requests (a restart forces its own exit after 15 s)
    for i in $(seq 1 20); do
        kill -0 "$EXISTING_PID" 2>/dev/null || break
        sleep 0.5
    done
    if kill -0 "$EXISTING_PID" 2>/dev/null; then
        kill -9 "$EXISTING_PID" 2>/dev/null
    fi
    echo "Old server stopped."
done

# ffmpeg lives in /opt/homebrew/bin (Apple Silicon) or /usr/local/bin (Intel)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# Browser from config.json ("browser" key), Safari by default
BROWSER=$(plutil -extract browser raw -o - "$CONFIG_FILE" 2>/dev/null || echo "Safari")

echo ""
echo "Starting DataVault Dashboard..."
echo "Server will be available at: http://127.0.0.1:8000"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Open the browser once the server is confirmed up (polls in background)
(
    for i in $(seq 1 30); do
        if curl -s -o /dev/null http://127.0.0.1:8000/api/health 2>/dev/null; then
            open -a "$BROWSER" "http://127.0.0.1:8000" || open "http://127.0.0.1:8000"
            break
        fi
        sleep 1
    done
) &

# Start the server
exec "$PYTHON" -m uvicorn --factory backend.main:create_app --host 127.0.0.1 --port 8000
