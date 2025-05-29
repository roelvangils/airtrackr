#!/usr/bin/env python3

import sqlite3
from pathlib import Path
import sys

def query_last_locations(device_name, limit=10):
    """Query the last N locations for a specific device"""
    
    db_path = Path("database") / "airtracker.db"
    
    if not db_path.exists():
        print("No database found. Run airtracker.py first.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Find the device
    cursor.execute('''
        SELECT id, canonical_name 
        FROM devices 
        WHERE canonical_name LIKE ? OR device_name LIKE ?
        LIMIT 1
    ''', (f'%{device_name}%', f'%{device_name}%'))
    
    device = cursor.fetchone()
    
    if not device:
        print(f"Device '{device_name}' not found.")
        print("\nAvailable devices:")
        cursor.execute('SELECT DISTINCT canonical_name FROM devices ORDER BY canonical_name')
        for row in cursor.fetchall():
            print(f"  - {row[0]}")
        conn.close()
        return
    
    device_id, canonical_name = device
    
    print(f"\nLast {limit} locations for: {canonical_name}")
    print("=" * 80)
    
    # Query locations
    cursor.execute('''
        SELECT 
            datetime(dl.timestamp_unix, 'unixepoch', 'localtime') as seen_at,
            dl.distance_meters,
            dl.location_text,
            dl.latitude,
            dl.longitude,
            dl.screenshot_id
        FROM device_locations dl
        WHERE dl.device_id = ?
        ORDER BY dl.timestamp_unix DESC
        LIMIT ?
    ''', (device_id, limit))
    
    locations = cursor.fetchall()
    
    if locations:
        for i, (seen_at, distance, location, lat, lon, screenshot_id) in enumerate(locations, 1):
            print(f"\n{i}. {seen_at} (Screenshot #{screenshot_id})")
            
            if distance is not None:
                if distance == 0:
                    print(f"   Distance: At location")
                else:
                    print(f"   Distance: {distance}m ({distance/1000:.1f}km)")
            
            if location:
                print(f"   Location: {location}")
            else:
                print(f"   Location: No location found")
            
            if lat and lon:
                print(f"   GPS: ({lat:.6f}, {lon:.6f})")
                print(f"   Map: https://www.google.com/maps?q={lat},{lon}")
    else:
        print("No location history found for this device.")
    
    # Show summary
    cursor.execute('''
        SELECT 
            COUNT(*) as total_locations,
            MIN(datetime(timestamp_unix, 'unixepoch', 'localtime')) as first_seen,
            MAX(datetime(timestamp_unix, 'unixepoch', 'localtime')) as last_seen
        FROM device_locations
        WHERE device_id = ?
    ''', (device_id,))
    
    total, first, last = cursor.fetchone()
    
    if total > limit:
        print(f"\n(Showing {limit} of {total} total locations)")
        print(f"Tracking since: {first}")
    
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python query_device_locations.py <device_name> [limit]")
        print("Example: python query_device_locations.py 'Black Valize' 10")
        sys.exit(1)
    
    device_name = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    query_last_locations(device_name, limit)