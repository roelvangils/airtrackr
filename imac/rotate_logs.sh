#!/bin/bash
# AirTrackr — Log Rotation
#
# Rotates log files larger than 1MB and removes backups older than 30 days.
#
# Install via crontab:
#   crontab -e
#   0 3 * * 0 /Users/evelyn/Repos/airtrackr/imac/rotate_logs.sh >> /Users/evelyn/Repos/airtrackr/logs/rotate.log 2>&1

LOGDIR="/Users/evelyn/Repos/airtrackr/logs"
MAX_SIZE=$((1 * 1024 * 1024))  # 1MB in bytes
RETENTION_DAYS=30
DATE_SUFFIX=$(date +%Y%m%d)

echo "[$(date)] Starting log rotation..."

# Rotate logs larger than 1MB
for logfile in "$LOGDIR"/*.log; do
    [ -f "$logfile" ] || continue

    size=$(stat -f%z "$logfile" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_SIZE" ]; then
        backup="${logfile}.${DATE_SUFFIX}.bak"
        echo "  Rotating $(basename "$logfile") (${size} bytes) -> $(basename "$backup")"
        mv "$logfile" "$backup"
        touch "$logfile"
    fi
done

# Remove old backups
find "$LOGDIR" -name "*.bak" -mtime +"$RETENTION_DAYS" -delete -print 2>/dev/null | while read -r f; do
    echo "  Removed old backup: $(basename "$f")"
done

echo "[$(date)] Log rotation complete."
