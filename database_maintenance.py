#!/usr/bin/env python3
"""
Database maintenance module for AirTrackr.

Provides backup, analyze, optimize, integrity check, and archive functions
using the shared db module.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta

from db import get_connection, DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


class DatabaseMaintenance:
    """Handle database maintenance tasks"""

    def __init__(self):
        self.db_path = DB_PATH
        self.backup_dir = Path("database") / "backups"
        self.backup_dir.mkdir(exist_ok=True)

    def backup_database(self):
        """Create a backup of the database"""
        if not self.db_path.exists():
            logging.error("Database not found")
            return False

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"airtracker_backup_{timestamp}.db"
        backup_path = self.backup_dir / backup_filename

        try:
            source = sqlite3.connect(self.db_path)
            dest = sqlite3.connect(backup_path)
            source.backup(dest)
            source.close()
            dest.close()

            logging.info(f"Database backed up to: {backup_path}")

            # Keep only last 7 backups
            self.cleanup_old_backups()
            return True

        except Exception as e:
            logging.error(f"Backup failed: {e}")
            return False

    def cleanup_old_backups(self, keep_count=7):
        """Keep only the most recent backups"""
        backups = sorted(self.backup_dir.glob("airtracker_backup_*.db"))
        if len(backups) > keep_count:
            for old_backup in backups[:-keep_count]:
                old_backup.unlink()
                logging.info(f"Deleted old backup: {old_backup.name}")

    def analyze_database(self):
        """Analyze database statistics"""
        with get_connection() as conn:
            cursor = conn.cursor()

            print("\nDATABASE STATISTICS")
            print("=" * 60)

            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """)
            tables = [row[0] for row in cursor.fetchall()]

            for table_name in tables:
                cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
                row_count = cursor.fetchone()[0]
                print(f"\n{table_name}: {row_count:,} rows")

            # Database file size
            db_size = self.db_path.stat().st_size
            print(f"\nTotal database size: {db_size / 1024 / 1024:.2f} MB")

            # Date range for swift_locations
            cursor.execute("""
                SELECT MIN(timestamp) as oldest, MAX(timestamp) as newest
                FROM swift_locations
            """)
            result = cursor.fetchone()
            if result[0] and result[1]:
                print(f"Data range: {result[0]} to {result[1]}")

    def optimize_database(self):
        """Optimize database performance"""
        with get_connection() as conn:
            logging.info("Running ANALYZE...")
            conn.execute("ANALYZE")

            logging.info("Running VACUUM...")
            conn.execute("VACUUM")

            cursor = conn.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
            """)
            for (index_name,) in cursor.fetchall():
                logging.info(f"Rebuilding index: {index_name}")
                conn.execute(f'REINDEX "{index_name}"')

            logging.info("Database optimization complete")

    def check_integrity(self):
        """Check database integrity"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()[0]

            if result == "ok":
                logging.info("Database integrity check: PASSED")
                return True
            else:
                logging.error(f"Database integrity check: FAILED - {result}")
                return False

    def run_full_maintenance(self):
        """Run all maintenance tasks"""
        print("Running full database maintenance...")

        if not self.check_integrity():
            print("ERROR: Database integrity check failed!")
            return

        if not self.backup_database():
            print("ERROR: Backup failed!")
            return

        self.analyze_database()
        self.optimize_database()
        print("\nMaintenance complete!")


if __name__ == "__main__":
    import sys

    maintenance = DatabaseMaintenance()

    if len(sys.argv) > 1:
        command = sys.argv[1]
        commands = {
            "backup": maintenance.backup_database,
            "analyze": maintenance.analyze_database,
            "optimize": maintenance.optimize_database,
            "integrity": maintenance.check_integrity,
            "full": maintenance.run_full_maintenance,
        }
        if command in commands:
            commands[command]()
        else:
            print("Usage: python database_maintenance.py [backup|analyze|optimize|integrity|full]")
    else:
        maintenance.run_full_maintenance()
