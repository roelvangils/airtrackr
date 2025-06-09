#!/bin/bash
set -euo pipefail

# AirTracker Setup Script
# This script ensures everything is ready to run AirTracker

echo "AirTracker Setup"
echo "================"
echo ""

# Check if we're on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "❌ This application only works on macOS"
    exit 1
fi

echo "✅ Running on macOS"

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed"
    echo "   Please install Python 3 from https://www.python.org/"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✅ Python $PYTHON_VERSION found"

# Install Python dependencies
echo ""
echo "📦 Installing Python dependencies..."
pip3 install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "❌ Failed to install Python dependencies"
    exit 1
fi

# Check/build Swift extractor
echo ""
echo "🔨 Checking Swift extractor..."

SWIFT_DIR="swift"
EXTRACTOR="$SWIFT_DIR/airtag_extractor"

if [ -f "$EXTRACTOR" ]; then
    # Test if it works on this architecture
    "$EXTRACTOR" --version &> /dev/null
    if [ $? -eq 86 ]; then
        echo "⚠️  Swift extractor needs to be recompiled for your architecture"
        if [ -f "$SWIFT_DIR/build_universal.sh" ]; then
            echo "🔨 Building universal binary..."
            cd "$SWIFT_DIR" && ./build_universal.sh
            cd ..
        else
            echo "🔨 Compiling for current architecture..."
            cd "$SWIFT_DIR" && swiftc airtag_extractor.swift -o airtag_extractor
            cd ..
        fi
    else
        echo "✅ Swift extractor is ready"
    fi
else
    echo "⚠️  Swift extractor not found, building..."
    if [ -f "$SWIFT_DIR/build_universal.sh" ]; then
        echo "🔨 Building universal binary..."
        cd "$SWIFT_DIR" && ./build_universal.sh
        cd ..
    else
        echo "🔨 Compiling for current architecture..."
        cd "$SWIFT_DIR" && swiftc airtag_extractor.swift -o airtag_extractor
        cd ..
    fi
fi

# Create necessary directories
echo ""
echo "📁 Creating directories..."
mkdir -p database
mkdir -p logs
mkdir -p screenshots

# Check Find My app
echo ""
echo "🔍 Checking Find My app..."
if [ -d "/System/Applications/FindMy.app" ]; then
    echo "✅ Find My app found"
else
    echo "❌ Find My app not found at expected location"
    echo "   Please ensure Find My is installed"
fi

# Accessibility permissions reminder
echo ""
echo "⚠️  IMPORTANT: Accessibility Permissions"
echo "   The tracker needs accessibility permissions for Terminal/iTerm"
echo "   "
echo "   To grant permissions:"
echo "   1. Open System Settings > Privacy & Security > Accessibility"
echo "   2. Add Terminal or iTerm2 (whichever you're using)"
echo "   3. Enable the checkbox"
echo ""

# Dashboard setup
echo "🌐 Setting up dashboard..."
if [ -d "dashboard" ]; then
    cd dashboard
    if command -v bun &> /dev/null; then
        echo "   Installing dashboard dependencies with bun..."
        bun install
        bun run build
    elif command -v npm &> /dev/null; then
        echo "   Installing dashboard dependencies with npm..."
        npm install
        npm run build
    else
        echo "⚠️  Neither bun nor npm found. Dashboard setup skipped."
        echo "   Install bun from https://bun.sh/ or npm from https://nodejs.org/"
    fi
    cd ..
else
    echo "⚠️  Dashboard directory not found"
fi

echo ""
echo "✨ Setup complete!"
echo ""
echo "To start tracking:"
echo "  ./run_all.sh"
echo ""
echo "Or run components individually:"
echo "  python3 swift_tracker.py --schedule 2    # Run tracker every 2 minutes"
echo "  python3 swift_api.py                     # Start API server"
echo "  cd dashboard && bun run dev              # Start dashboard"