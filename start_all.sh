#!/bin/bash
# AirTracker - Start All Services
# This script launches the API server, dashboard, and orchestrated tracker

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Starting AirTracker - Complete System${NC}"
echo "=========================================="

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}⚠️  Virtual environment not found. Creating...${NC}"
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

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
        echo -e "${YELLOW}⚠️  Continuing without FindMy. Tracker will fail.${NC}"
    fi
fi

echo ""
echo -e "${GREEN}1. Starting API Server (port 8001)...${NC}"
nohup python swift_api.py > logs/api.log 2>&1 &
API_PID=$!
echo "   API Server PID: $API_PID"
sleep 2

echo ""
echo -e "${GREEN}2. Starting Dashboard (port 3000)...${NC}"
cd dashboard
nohup bun run dev > ../logs/dashboard.log 2>&1 &
DASHBOARD_PID=$!
echo "   Dashboard PID: $DASHBOARD_PID"
cd ..

# Default schedule: 5 minutes (can be overridden with first argument)
SCHEDULE="${1:-5}"

echo ""
echo -e "${GREEN}3. Starting Orchestrated Tracker (every ${SCHEDULE} minutes)...${NC}"
# Ensure log file exists before starting
touch logs/tracker.log
nohup python orchestrated_tracker.py --schedule "$SCHEDULE" > logs/tracker.log 2>&1 &
TRACKER_PID=$!
echo "   Tracker PID: $TRACKER_PID"

# Save PIDs for later
echo "$API_PID" > logs/api.pid
echo "$DASHBOARD_PID" > logs/dashboard.pid
echo "$TRACKER_PID" > logs/tracker.pid

# Wait a moment for services to start
sleep 3

echo ""
echo -e "${GREEN}✅ All services started!${NC}"
echo ""
echo "📊 Services:"
echo "   • API Server:  http://localhost:8001"
echo "   • API Docs:    http://localhost:8001/docs"
echo "   • Dashboard:   http://localhost:3000"
echo "   • Tracker:     Running every ${SCHEDULE} minutes"
echo ""
echo "📝 Logs:"
echo "   • API:         tail -f logs/api.log"
echo "   • Dashboard:   tail -f logs/dashboard.log"
echo "   • Tracker:     tail -f logs/tracker.log"
echo ""
echo "🛑 To stop all services:"
echo "   ./stop_servers.sh"
echo ""

# Check if services are responding
echo "🔍 Checking service health..."
sleep 2

if curl -s http://localhost:8001/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ API Server is healthy${NC}"
else
    echo -e "${YELLOW}⚠️  API Server may still be starting...${NC}"
fi

if curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Dashboard is running${NC}"
else
    echo -e "${YELLOW}⚠️  Dashboard may still be starting...${NC}"
fi

if ps -p "$TRACKER_PID" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Tracker is running${NC}"
else
    echo -e "${RED}❌ Tracker failed to start${NC}"
fi

echo ""
echo -e "${BLUE}✨ AirTracker is fully operational!${NC}"
echo ""
echo "ℹ️  The tracker will:"
echo "   • Cycle through People/Devices/Items tabs"
echo "   • Extract location data from FindMy"
echo "   • Update the database every ${SCHEDULE} minutes"

# Open log files in Console.app
echo ""
echo "📖 Opening log files in Console.app..."
if command -v open >/dev/null 2>&1; then
    # Use absolute paths for Console.app
    API_LOG="$(pwd)/logs/api.log"
    DASHBOARD_LOG="$(pwd)/logs/dashboard.log"
    TRACKER_LOG="$(pwd)/logs/tracker.log"
    open -a Console "$API_LOG" "$DASHBOARD_LOG" "$TRACKER_LOG" 2>/dev/null || true
    sleep 1
    echo "   Log files opened in Console.app:"
    echo "   • API: $API_LOG"
    echo "   • Dashboard: $DASHBOARD_LOG"
    echo "   • Tracker: $TRACKER_LOG"
else
    echo "   Note: Console.app not available on this system"
fi

echo ""
echo "🎉 All done! Open http://localhost:3000 to view the dashboard."
