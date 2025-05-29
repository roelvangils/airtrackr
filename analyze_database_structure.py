#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from collections import defaultdict

def analyze_current_structure():
    """Analyze issues with current database structure"""
    
    db_path = Path("database") / "airtracker.db"
    if not db_path.exists():
        print("No database found.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("CURRENT DATABASE STRUCTURE ANALYSIS")
    print("=" * 60)
    
    # 1. Check for duplicate device entries
    print("\n1. DUPLICATE DEVICE NAMES (same device, multiple records):")
    cursor.execute('''
        SELECT device_name, COUNT(*) as occurrences
        FROM locations 
        WHERE device_name IS NOT NULL 
        AND device_name NOT LIKE '%initialized%'
        AND device_name NOT LIKE '%cleaned%'
        AND device_name NOT LIKE '%timestamp%'
        GROUP BY device_name 
        HAVING COUNT(*) > 0
        ORDER BY device_name
    ''')
    
    for device, count in cursor.fetchall():
        print(f"   - {device}: {count} record(s)")
    
    # 2. Show the problem with tracking history
    print("\n2. TRACKING HISTORY PROBLEM:")
    print("   Current structure makes it hard to query:")
    print("   - 'Show all locations for Black Valize over time'")
    print("   - 'Which AirTag moved the most today?'")
    print("   - 'Alert me when an AirTag hasn't been seen for 1 hour'")
    
    # 3. Show reliance on region index
    print("\n3. REGION INDEX DEPENDENCY:")
    cursor.execute('''
        SELECT region_index, device_name, COUNT(*) 
        FROM locations 
        WHERE device_name IN ('Black Valize', 'Yellow Valize', 'Auto')
        GROUP BY region_index, device_name
        ORDER BY device_name, region_index
    ''')
    
    print("   Device positions by region index:")
    for region, device, count in cursor.fetchall():
        print(f"   - Region {region}: {device}")
    
    print("\n   ⚠️  Problem: If AirTags change order in Find My, tracking breaks!")
    
    # 4. Data redundancy
    print("\n4. DATA REDUNDANCY:")
    cursor.execute('''
        SELECT 
            COUNT(*) as total_records,
            COUNT(DISTINCT device_name) as unique_devices,
            SUM(LENGTH(device_name)) as total_chars_stored
        FROM locations
        WHERE device_name IS NOT NULL
    ''')
    
    total, unique, chars = cursor.fetchone()
    print(f"   - Total location records: {total}")
    print(f"   - Unique devices: {unique}")
    print(f"   - Characters wasted on duplicate names: ~{chars - (unique * 15)}")
    
    # 5. Show what queries would look like
    print("\n5. QUERY COMPLEXITY:")
    print("   Current: Getting device history is complex:")
    print("   ```sql")
    print("   SELECT * FROM locations")
    print("   WHERE device_name = 'Black Valize'")
    print("   ORDER BY timestamp_unix DESC")
    print("   ```")
    print("\n   With proper structure:")
    print("   ```sql")
    print("   SELECT * FROM device_locations")
    print("   WHERE device_id = 1  -- Black Valize's ID")
    print("   ORDER BY timestamp_unix DESC")
    print("   ```")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("BENEFITS OF PROPER RELATIONAL STRUCTURE:")
    print("1. Each AirTag stored once in 'devices' table")
    print("2. Location history linked by device_id (not name)")
    print("3. Can track devices even if they move positions")
    print("4. Easy to query device history and patterns")
    print("5. Can add device metadata (type, owner, etc.)")
    print("6. Supports alerts and notifications")

if __name__ == "__main__":
    analyze_current_structure()