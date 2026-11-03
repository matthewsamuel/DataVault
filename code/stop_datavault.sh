#!/bin/bash

# Stop DataVault Dashboard server (only the process listening on port 8000)

echo "Stopping DataVault Dashboard server..."

# Only a process whose command line is a DataVault uvicorn (backend.main) is stopped
FOUND=""
for PID in $(lsof -ti :8000 -sTCP:LISTEN 2>/dev/null); do
    COMMAND=$(ps -o command= -p "$PID" 2>/dev/null)
    case "$COMMAND" in
        *uvicorn*backend.main*)
            echo "Stopping server (PID: $PID)..."
            kill "$PID"
            FOUND=1
            echo "Server stopped."
            ;;
        *)
            echo "Port 8000 is used by another program (PID $PID), left alone: $COMMAND"
            ;;
    esac
done
if [ -z "$FOUND" ]; then
    echo "No DataVault server found running."
fi

rm -f /tmp/datavault_server.pid
