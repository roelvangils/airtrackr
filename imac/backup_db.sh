#!/bin/bash
# Daily database backup as SQL dump, auto-committed to git.
# Cron: 0 4 * * 0 /Users/evelyn/Repos/airtrackr/imac/backup_db.sh >> /Users/evelyn/Repos/airtrackr/logs/backup.log 2>&1

set -euo pipefail

REPO_DIR="/Users/evelyn/Repos/airtrackr"
DB_PATH="$REPO_DIR/database/airtracker.db"
DUMP_PATH="$REPO_DIR/database/airtracker_backup.sql"
LOG="$REPO_DIR/logs/tracker.log"

cd "$REPO_DIR"

echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [BACKUP] Starting database backup..." >> "$LOG"

# Create SQL dump (text-based, git-friendly)
sqlite3 "$DB_PATH" .dump > "$DUMP_PATH"
DUMP_SIZE=$(du -h "$DUMP_PATH" | cut -f1)
echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [BACKUP] SQL dump created ($DUMP_SIZE)" >> "$LOG"

# Only commit if something changed
if [ -n "$(git status --porcelain -- "$DUMP_PATH")" ]; then
    git add "$DUMP_PATH"
    git commit -m "Automated DB backup $(date +%Y-%m-%d)" -- "$DUMP_PATH"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [BACKUP] Committed to git" >> "$LOG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [BACKUP] No changes, skipping commit" >> "$LOG"
fi
