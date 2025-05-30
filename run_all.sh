#!/bin/bash

# AirTracker - Run All Services
# This script starts all components of the AirTracker system:
# 1. Swift Tracker (data collection)
# 2. Swift API (REST API on port 8001)
# 3. Dashboard (web interface)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting AirTracker Services...${NC}"
echo "================================"

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down services...${NC}"
    
    # Kill all child processes
    pkill -P $$
    
    # Kill specific processes by name (fallback)
    pkill -f "swift_tracker.py"
    pkill -f "swift_api.py"
    pkill -f "bun.*dev"
    
    echo -e "${GREEN}All services stopped.${NC}"
    exit 0
}

# Set trap to cleanup on script exit
trap cleanup INT TERM EXIT

# Check if required files exist
if [ ! -f "swift_tracker.py" ]; then
    echo -e "${RED}Error: swift_tracker.py not found!${NC}"
    exit 1
fi

if [ ! -f "swift_api.py" ]; then
    echo -e "${RED}Error: swift_api.py not found!${NC}"
    exit 1
fi

if [ ! -d "dashboard" ]; then
    echo -e "${RED}Error: dashboard directory not found!${NC}"
    exit 1
fi

# Start Swift Tracker
echo -e "${YELLOW}1. Starting Swift Tracker...${NC}"
python3 swift_tracker.py --schedule 2 &
TRACKER_PID=$!
echo -e "   Swift Tracker started (PID: $TRACKER_PID)"

# Give tracker time to initialize
sleep 2

# Start Swift API
echo -e "${YELLOW}2. Starting Swift API on port 8001...${NC}"
python3 swift_api.py &
API_PID=$!
echo -e "   Swift API started (PID: $API_PID)"

# Give API time to start
sleep 2

# Start Dashboard
echo -e "${YELLOW}3. Starting Dashboard...${NC}"
cd dashboard && bun run dev &
DASHBOARD_PID=$!
echo -e "   Dashboard started (PID: $DASHBOARD_PID)"

echo -e "\n${GREEN}All services are running!${NC}"
echo "================================"
echo "Swift Tracker: Running (updates every 2 minutes)"
echo "API: http://localhost:8001"
echo "API Docs: http://localhost:8001/docs"
echo "Dashboard: http://localhost:5173"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"

# Wait for all background processes
wait