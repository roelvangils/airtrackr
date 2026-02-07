#!/bin/bash
# AirTrackr — Weekly reboot (Monday 04:00)

LOG="/Users/evelyn/Repos/airtrackr/logs/tracker.log"

echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [REBOOT] Weekly reboot initiated" >> "$LOG"
echo "evelyn" | sudo -S /sbin/shutdown -r now
