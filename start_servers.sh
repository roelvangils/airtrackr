#!/bin/bash
# AirTracker - Start All Servers
# This script launches the API server and dashboard

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Starting AirTracker Services${NC}"
echo "=================================="

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

# Save PIDs for later
echo "$API_PID" > logs/api.pid
echo "$DASHBOARD_PID" > logs/dashboard.pid

# Wait a moment for services to start
sleep 3

echo ""
echo -e "${GREEN}✅ All services started!${NC}"
echo ""
echo "📊 Services:"
echo "   • API Server:  http://localhost:8001"
echo "   • API Docs:    http://localhost:8001/docs"
echo "   • Dashboard:   http://localhost:3000"
echo ""
echo "📝 Logs:"
echo "   • API:         tail -f logs/api.log"
echo "   • Dashboard:   tail -f logs/dashboard.log"
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

echo ""
echo -e "${BLUE}✨ AirTracker is ready!${NC}"

# Open log files in Console.app
echo ""
echo "📖 Opening log files in Console.app..."
if command -v open >/dev/null 2>&1; then
    # Use absolute paths for Console.app
    API_LOG="$(pwd)/logs/api.log"
    DASHBOARD_LOG="$(pwd)/logs/dashboard.log"
    open -a Console "$API_LOG" "$DASHBOARD_LOG" 2>/dev/null || true
    sleep 1
    echo "   Log files opened in Console.app:"
    echo "   • API: $API_LOG"
    echo "   • Dashboard: $DASHBOARD_LOG"
else
    echo "   Note: Console.app not available on this system"
fi
