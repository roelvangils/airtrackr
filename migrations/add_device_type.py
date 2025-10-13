#!/usr/bin/env python3
"""
Database Migration: Add device_type Column

This migration adds a device_type column to track whether an entity
is a person, device, or item from Find My.

Usage:
    python migrations/add_device_type.py
"""

import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

def migrate_database(db_path: str = "database/airtracker.db"):
    """
    Add device_type column to swift_locations and swift_devices tables.

    Args:
        db_path: Path to SQLite database
    """
    print(f"Migrating database: {db_path}")
    print("-" * 50)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if device_type column already exists in swift_locations
        cursor.execute("PRAGMA table_info(swift_locations)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'device_type' not in columns:
            print("Adding device_type column to swift_locations table...")
            cursor.execute('''
                ALTER TABLE swift_locations
                ADD COLUMN device_type TEXT CHECK(device_type IN ('person', 'device', 'item'))
            ''')
            print("✅ Added device_type column to swift_locations")

            # Create index for better query performance
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_swift_locations_device_type
                ON swift_locations(device_type)
            ''')
            print("✅ Created index on device_type")
        else:
            print("ℹ️  device_type column already exists in swift_locations")

        # Check if device_type column already exists in swift_devices
        cursor.execute("PRAGMA table_info(swift_devices)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'device_type' not in columns:
            print("\nAdding device_type column to swift_devices table...")
            cursor.execute('''
                ALTER TABLE swift_devices
                ADD COLUMN device_type TEXT CHECK(device_type IN ('person', 'device', 'item'))
            ''')
            print("✅ Added device_type column to swift_devices")
        else:
            print("ℹ️  device_type column already exists in swift_devices")

        conn.commit()
        print("\n✅ Migration completed successfully!")

        # Show current schema
        print("\n📊 Current swift_locations schema:")
        cursor.execute("PRAGMA table_info(swift_locations)")
        for row in cursor.fetchall():
            print(f"  - {row[1]}: {row[2]}")

        print("\n📊 Current swift_devices schema:")
        cursor.execute("PRAGMA table_info(swift_devices)")
        for row in cursor.fetchall():
            print(f"  - {row[1]}: {row[2]}")

    except sqlite3.Error as e:
        print(f"\n❌ Migration failed: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Add device_type column to AirTracker database"
    )
    parser.add_argument(
        '--database', '-d',
        default='database/airtracker.db',
        help='Path to SQLite database (default: database/airtracker.db)'
    )

    args = parser.parse_args()

    migrate_database(args.database)


if __name__ == "__main__":
    main()
