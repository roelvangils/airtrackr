#!/bin/bash
# AirTrackr — Set screen brightness to 0 at login
#
# Uses the 'brightness' CLI tool to set display brightness to 0.
# The display stays "on" (Accessibility API works) but emits no light.
#
# Called by com.airtrackr.dim.plist LaunchAgent at login.
# Install brightness tool: brew install brightness

sleep 5  # Wait for login/display to be ready
/usr/local/bin/brightness 0
