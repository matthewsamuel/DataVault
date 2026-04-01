#!/bin/bash

# Stop DataVault Dashboard server

echo "Stopping DataVault Dashboard server..."

# Check if PID file exists
if [ -f "/tmp/datavault_server.pid" ]; then
    PID=$(cat /tmp/datavault_server.pid)
    
    if kill -0 $PID 2>/dev/null; then
        echo "Stopping server (PID: $PID)..."
        kill $PID
        rm /tmp/datavault_server.pid
        echo "Server stopped."
    else
        echo "Server not running (stale PID file)"
        rm /tmp/datavault_server.pid
    fi
else
    # Try to find and kill any running uvicorn process on port 8000
    PID=$(lsof -ti :8000)
    if [ ! -z "$PID" ]; then
        echo "Found server on port 8000 (PID: $PID), stopping..."
        kill $PID
        echo "Server stopped."
    else
        echo "No DataVault server found running."
    fi
fi
