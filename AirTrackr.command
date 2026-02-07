#!/bin/bash
# AirTrackr — Terminal Login Item
#
# Add this file as a Login Item in System Settings > General > Login Items.
# Terminal.app must have Accessibility permissions in Privacy & Security.
#
# This opens in Terminal at login and runs the tracker with crash recovery.
# The API server is handled separately by the com.airtrackr.api LaunchAgent.

exec /Users/evelyn/Repos/airtrackr/start_all_imac.sh
