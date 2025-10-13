#!/bin/bash
# AirTracker - Setup Script
# This script sets up the Python environment with Python 3.14 compatibility

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🔧 Setting up AirTracker Environment${NC}"
echo "======================================"

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Remove old venv if it exists
if [ -d "venv" ]; then
    echo -e "${YELLOW}Removing old virtual environment...${NC}"
    rm -rf venv
fi

echo ""
echo -e "${GREEN}1. Creating new virtual environment...${NC}"
python3 -m venv venv

echo ""
echo -e "${GREEN}2. Activating virtual environment...${NC}"
source venv/bin/activate

echo ""
echo -e "${GREEN}3. Upgrading pip...${NC}"
pip install --upgrade pip

echo ""
echo -e "${GREEN}4. Installing dependencies with Python 3.14 compatibility...${NC}"
# Set compatibility flag for pydantic-core to work with Python 3.14
export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
pip install -r requirements.txt

echo ""
echo -e "${GREEN}5. Building Swift extractor...${NC}"
cd swift
./build_universal.sh
cd ..

echo ""
echo -e "${GREEN}6. Creating necessary directories...${NC}"
mkdir -p database logs screenshots

echo ""
echo -e "${GREEN}7. Setting up database...${NC}"
python migrations/add_device_type.py 2>/dev/null || echo "   Database migration already applied or not needed"

echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "🚀 You can now start AirTracker:"
echo "   ./start_all.sh"
echo ""
echo "📚 Need help? Check DEBUGGING.md"
