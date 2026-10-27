#!/bin/bash

# Stop DataVault Dashboard server (only the process listening on port 8000)

echo "Stopping DataVault Dashboard server..."

PID=$(lsof -ti :8000 -sTCP:LISTEN 2>/dev/null)
if [ -n "$PID" ]; then
    echo "Stopping server (PID: $PID)..."
    kill $PID
    echo "Server stopped."
else
    echo "No DataVault server found running."
fi

rm -f /tmp/datavault_server.pid
