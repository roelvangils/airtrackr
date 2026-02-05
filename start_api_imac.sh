#!/bin/bash
# AirTrackr API Server for iMac (standalone)
#
# Usage:
#   ~/Repos/airtrackr/start_api_imac.sh
#
# Access from MacBook via jump server:
#   ssh roel@kumulus.11ways.be "curl -s http://192.168.50.6:8001/api/v1/devices"

exec 2>> /Users/evelyn/Desktop/airtrackr_errors.log
echo "[$(date)] API script starting..." >> /Users/evelyn/Desktop/airtrackr_errors.log

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export VIRTUAL_ENV="/Users/evelyn/Repos/airtrackr/venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

cd /Users/evelyn/Repos/airtrackr || exit 1

exec "$VIRTUAL_ENV/bin/python3" -m uvicorn swift_api:app --host 192.168.50.6 --port 8001
