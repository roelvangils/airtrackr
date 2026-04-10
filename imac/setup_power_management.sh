#!/bin/bash
# AirTrackr — Power Management Setup for Headless iMac
#
# Run this script ONCE after initial setup or after macOS updates.
# Requires sudo.

set -e

echo "=== AirTrackr Power Management Setup ==="
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run with sudo"
   echo "Usage: sudo $0"
   exit 1
fi

echo "1. Disabling system sleep..."
pmset -a sleep 0

echo "2. Disabling display sleep..."
pmset -a displaysleep 0

echo "3. Disabling disk sleep..."
pmset -a disksleep 0

echo "4. Disabling Power Nap (causes app pauses)..."
pmset -a powernap 0

echo "5. Disabling standby mode..."
pmset -a standby 0
pmset -a autopoweroff 0

echo "6. Enabling TCP keepalive..."
pmset -a tcpkeepalive 1

echo "7. Enabling wake on network access..."
pmset -a womp 1

echo "8. Disabling proximity wake (no Apple Watch nearby)..."
pmset -a proximitywake 0

echo "9. Enabling auto-restart after power failure..."
pmset -a autorestart 1

echo ""
echo "=== Disabling App Nap system-wide ==="
defaults write -g NSAppSleepDisabled -bool YES

echo ""
echo "=== Disabling App Nap for specific apps ==="
# Disable App Nap for Find My
defaults write com.apple.findmy NSAppSleepDisabled -bool YES
# Disable App Nap for Terminal (runs tracker)
defaults write com.apple.Terminal NSAppSleepDisabled -bool YES

echo ""
echo "=== Current Power Settings ==="
pmset -g

echo ""
echo "=== Done! ==="
echo "Reboot recommended for all changes to take effect."
