#!/bin/bash
# AirTracker - Start Orchestrated Tracker
# This script launches the tracker that cycles through Find My tabs

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🔄 Starting AirTracker Orchestrated Tracker${NC}"
echo "============================================"

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${RED}❌ Virtual environment not found. Run ./start_servers.sh first${NC}"
    exit 1
fi

source venv/bin/activate

# Create logs directory if it doesn't exist
mkdir -p logs

# Check if FindMy is running
if ! pgrep -x "FindMy" > /dev/null; then
    echo -e "${YELLOW}⚠️  Warning: FindMy app is not running${NC}"
    echo "   The tracker requires FindMy to be open."
    echo ""
    read -p "   Would you like to launch FindMy now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "   Opening FindMy..."
        open -a "FindMy"
        echo "   Waiting for FindMy to launch..."
        sleep 5
    else
        echo -e "${RED}❌ Cancelled. Please launch FindMy manually.${NC}"
        exit 1
    fi
fi

# Default schedule: 5 minutes
SCHEDULE="${1:-5}"

echo ""
echo -e "${GREEN}Starting tracker with schedule: every ${SCHEDULE} minutes${NC}"
echo ""

# Ensure log file exists before starting
touch logs/tracker.log

nohup python orchestrated_tracker.py --schedule "$SCHEDULE" > logs/tracker.log 2>&1 &
TRACKER_PID=$!

# Save PID for later
echo "$TRACKER_PID" > logs/tracker.pid

echo "Tracker PID: $TRACKER_PID"
echo ""
echo -e "${GREEN}✅ Tracker started!${NC}"
echo ""
echo "📝 Monitor tracker:"
echo "   tail -f logs/tracker.log"
echo ""
echo "🛑 To stop the tracker:"
echo "   ./stop_servers.sh"
echo ""
echo "ℹ️  The tracker will:"
echo "   • Cycle through People/Devices/Items tabs"
echo "   • Extract data from each tab"
echo "   • Run every ${SCHEDULE} minutes"
echo ""

# Wait a moment and check if it's still running
sleep 3
if ps -p "$TRACKER_PID" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Tracker is running${NC}"
    echo ""
    echo "Recent log output:"
    echo "---"
    tail -20 logs/tracker.log

    echo ""
    echo "📖 Opening tracker log in Console.app..."
    if command -v open >/dev/null 2>&1; then
        # Use absolute path for Console.app
        LOG_PATH="$(pwd)/logs/tracker.log"
        open -a Console "$LOG_PATH" 2>/dev/null || true
        sleep 1
        echo "   Tracker log opened in Console.app"
        echo "   Log path: $LOG_PATH"
    else
        echo "   Note: Console.app not available on this system"
    fi
else
    echo -e "${RED}❌ Tracker failed to start. Check logs/tracker.log${NC}"
    exit 1
fi
