#!/bin/bash
# AirTracker - Stop All Servers
# This script stops the API server, dashboard, and tracker

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🛑 Stopping AirTracker Services${NC}"
echo "=================================="

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Function to kill process by PID file
kill_by_pidfile() {
    local pidfile=$1
    local service_name=$2

    if [ -f "$pidfile" ]; then
        local pid=$(cat "$pidfile")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo -e "${GREEN}Stopping $service_name (PID: $pid)...${NC}"
            kill "$pid" 2>/dev/null || true
            sleep 1
            # Force kill if still running
            if ps -p "$pid" > /dev/null 2>&1; then
                echo -e "${YELLOW}Force stopping $service_name...${NC}"
                kill -9 "$pid" 2>/dev/null || true
            fi
        else
            echo -e "${YELLOW}$service_name not running${NC}"
        fi
        rm -f "$pidfile"
    fi
}

# Stop services by PID files
if [ -d "logs" ]; then
    kill_by_pidfile "logs/api.pid" "API Server"
    kill_by_pidfile "logs/dashboard.pid" "Dashboard"
    kill_by_pidfile "logs/tracker.pid" "Tracker"
fi

# Additional cleanup - kill any remaining processes
echo ""
echo "🧹 Cleaning up any remaining processes..."

# Kill any swift_api.py processes
pkill -f "python.*swift_api.py" 2>/dev/null && echo "  • Killed API server processes" || true

# Kill any bun dev processes
pkill -f "bun.*run.*dev" 2>/dev/null && echo "  • Killed dashboard processes" || true

# Kill any orchestrated_tracker.py processes
pkill -f "python.*orchestrated_tracker.py" 2>/dev/null && echo "  • Killed tracker processes" || true

# Kill any swift_tracker.py processes (old tracker)
pkill -f "python.*swift_tracker.py" 2>/dev/null && echo "  • Killed old tracker processes" || true

echo ""
echo -e "${GREEN}✅ All services stopped${NC}"
